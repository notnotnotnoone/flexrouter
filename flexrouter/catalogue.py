"""The provider catalogue: who exists, and what models they currently list.

This is the read-only half of what used to be the `init` wizard. The wizard
itself is gone — it wrote a settings file full of plaintext keys into whatever
folder you happened to be standing in, which is the exact fault the shared
home exists to kill. What survives here is the part the daily catalogue check
still needs: the provider registry, the `/v1/models` lookup, and the scoring
helper. Nothing in this module writes anything.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable
import logging
import re
import statistics

import httpx

logger = logging.getLogger(__name__)

STANDARD_RL_HEADERS = {
    "rpm": "x-ratelimit-limit-requests",
    "tpm": "x-ratelimit-limit-tokens",
}

# Artificial Analysis retired the unversioned /data/llms/models path; it now
# 404s for keyed and unkeyed callers alike. Because score_with_aa treats any
# >=400 as "fall back to 50", that failure was silent — every model scored 50
# and the tests kept passing because all three mocked the dead URL. Keep this
# as one named constant so the URL can only be changed deliberately.
AA_MODELS_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"

# What an unmatched model scores when there is no AA data at all (no key, or
# the call failed). In that case EVERY model gets this number, so the value
# itself cannot skew ranking — they are all equal and it is just a placeholder.
# The dangerous case is the mixed one, where some models are scored and some
# are not; that is handled separately in score_with_aa, which uses the median
# of the live set rather than this constant.
UNSCORED_FALLBACK = 50


@dataclass(frozen=True)
class ProviderDef:
    name: str
    base_url: str
    signup_url: str
    free: bool
    free_filter: Callable[[dict], bool]
    default_rpm: int
    default_tpm: int
    rate_limit_headers: dict = field(default_factory=lambda: dict(STANDARD_RL_HEADERS))
    ollama: bool = False


def _all_free(_model: dict) -> bool:
    return True


def _openrouter_free(model: dict) -> bool:
    pricing = model.get("pricing", {})
    try:
        return float(pricing.get("prompt", "1")) == 0.0
    except (ValueError, TypeError):
        return ":free" in model.get("id", "")


# Google's model list carries no capability or pricing fields, so these come
# from ai.google.dev/gemini-api/docs/pricing: flash, flash-lite, 2.5-pro and
# Gemma are free; image, speech, live/audio and agent models are either paid
# or not answerable through chat completions.
_GOOGLEAI_NOT_CHAT = ("image", "tts", "audio", "live", "transcribe", "translate",
                      "embedding", "robotics", "computer-use", "customtools", "omni")


def _googleai_free(model: dict) -> bool:
    mid = model.get("id", "").lower().removeprefix("models/")
    if any(word in mid for word in _GOOGLEAI_NOT_CHAT):
        return False
    if mid.startswith("gemma-"):
        return True
    return mid.startswith("gemini-") and ("flash" in mid or mid.startswith("gemini-2.5-pro"))


def _llm7_free(model: dict) -> bool:
    # docs.llm7.io/guides/models-api: "turbo" models are the free tier, "pro"
    # models and anything usage_based_only draw on a paid balance.
    return (model.get("model_type") == "chat" and model.get("tier") == "turbo"
            and not model.get("usage_based_only"))


def _mistral_chat(model: dict) -> bool:
    # Mistral's /models marks embed, OCR, moderation and speech models with
    # capabilities.completion_chat = false.
    return bool((model.get("capabilities") or {}).get("completion_chat")) \
        and not model.get("archived")


def _groq_chat(model: dict) -> bool:
    if model.get("active") is False:
        return False
    outputs = model.get("output_modalities")
    return outputs is None or "text" in outputs


# A preset names its filter; this is the only place a name becomes code.
# Keeping it a closed dict rather than a lookup by import path is
# deliberate: a preset file is hand-edited, and "name a Python callable"
# is one keystroke away from "import anything you like".
FREE_FILTERS: dict[str, Callable[[dict], bool]] = {
    "all": _all_free,
    "zero_price": _openrouter_free,
    "flash_only": _googleai_free,
    "googleai_free": _googleai_free,
    "llm7_free": _llm7_free,
    "mistral_chat": _mistral_chat,
    "groq_chat": _groq_chat,
}


class DiscoveryError(Exception):
    """A provider's model list could not be read."""


def _as_provider_def(p) -> ProviderDef:
    """One preset in the shape `refresh.py` has always read.

    `default_rpm`/`default_tpm` keep their old names here even though the
    preset calls them seeds, because this dataclass is what the refresh
    path consumes and renaming it is not this change's job.
    """
    return ProviderDef(
        name=p.name,
        base_url=p.base_url,
        signup_url=p.signup_url,
        free=p.free,
        free_filter=FREE_FILTERS.get(p.free_filter, _all_free),
        default_rpm=p.seed_rpm,
        default_tpm=p.seed_tpm,
        ollama=(p.name == "ollama"),
    )


def _load_providers() -> list[ProviderDef]:
    from flexrouter import presets
    return [_as_provider_def(p) for p in presets.shipped().values()]


PROVIDERS: list[ProviderDef] = _load_providers()


async def discover_models(provider: ProviderDef, api_key: str) -> list[dict]:
    """GET /v1/models for a provider, return free-filtered model dicts.

    Raises DiscoveryError rather than returning [] when the list cannot be
    read: an empty list is indistinguishable from "the provider dropped every
    model", and refresh would report them all as vanished.
    """
    from flexrouter.errors import extract_error_message

    url = f"{provider.base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise DiscoveryError(f"could not reach {url}: {type(exc).__name__}") from exc
    if resp.status_code >= 400:
        detail = extract_error_message(resp.text) or resp.text[:200]
        raise DiscoveryError(f"HTTP {resp.status_code} from {url}: {detail}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise DiscoveryError(f"{url} did not answer with JSON") from exc
    models = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(models, list):
        raise DiscoveryError(f"{url} did not answer with a model list")
    return [m for m in models if isinstance(m, dict) and provider.free_filter(m)]


async def discover_ollama() -> list[dict]:
    """Auto-detect Ollama at localhost:11434. Returns model dicts or []."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://localhost:11434/v1/models")
            if resp.status_code >= 400:
                return []
            data = resp.json()
        return data.get("data", data) if isinstance(data, dict) else data
    except Exception:
        return []


def _context_window(model: dict) -> int:
    for key in ("context_window", "context_length"):
        v = model.get(key)
        if isinstance(v, dict):
            v = v.get("tokens")
        if v is not None:
            return int(v)
    return 131_072


# Words that say which release channel or packaging a model id is, not which
# model it is, and date stamps like 2512 or 0731. Dropped on both sides.
_MATCH_NOISE = frozenset({"latest", "preview", "instruct", "it", "chat", "free", "exp"})
_DATE_STAMP = re.compile(r"^\d{4}$|^\d{8}$")

AaLookup = dict[tuple, list[int]]


def _match_tokens(s: str) -> tuple:
    s = s.lower()
    s = re.sub(r"\([^)]*\)", " ", s)  # AA's variant labels: "(Reasoning)", "(high)"
    s = re.sub(r":free$", "", s)
    s = s.split("/")[-1]
    tokens = re.findall(r"[a-z]+|\d+[a-z]*", s)
    return tuple(t for t in tokens if t not in _MATCH_NOISE and not _DATE_STAMP.match(t))


def _aa_lookup_from(payload: dict) -> AaLookup:
    """AA's scores keyed by the tokens of each entry's name and slug.

    One model often appears several times (low/high effort, reasoning on or
    off), so each key holds every variant's score.
    """
    lookup: AaLookup = {}
    for entry in payload.get("data", []):
        # A handful of AA entries carry the index as an explicit null.
        # `.get(key, 50)` does not save you there - the default only applies
        # when the key is absent. AA has no opinion on those models.
        raw = (entry.get("evaluations") or {}).get("artificial_analysis_intelligence_index")
        if not isinstance(raw, (int, float)):
            continue
        keys = {_match_tokens(entry.get("name", "")), _match_tokens(entry.get("slug", ""))}
        for key in keys:
            if key:
                lookup.setdefault(key, []).append(int(raw))
    return lookup


def _best_score(model_id: str, aa_lookup: AaLookup, fallback: int = UNSCORED_FALLBACK) -> int:
    """The AA score for exactly this model, or `fallback`.

    Only an equal token set counts. Substring matching gave gpt-oss-120b the
    score of "HyperNova 60B (based on gpt-oss-120b)", and claude-opus-5 the
    score of Opus 5.5.
    """
    scores = aa_lookup.get(_match_tokens(model_id))
    return statistics.median_low(scores) if scores else fallback


async def fetch_aa_lookup(aa_key: str) -> AaLookup:
    """Fetch AA's model data as a lookup for `_best_score`.

    Unlike score_with_aa this does not swallow failures: it raises on a
    network error or a non-2xx response, so a caller that *asked* for a
    scoring run (the dashboard's re-score button) can report the failure
    instead of quietly stamping the fallback over everything. Entries whose
    index is absent or an explicit null are skipped — AA has no opinion on
    those models.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            AA_MODELS_URL,
            headers={"x-api-key": aa_key},
        )
        resp.raise_for_status()
        return _aa_lookup_from(resp.json())


async def score_with_aa(models: list[dict], aa_key: str | None,
                        unscored_fallback: int = UNSCORED_FALLBACK) -> list[dict]:
    """Assign AA intelligence scores to models. Unmatched → score=50."""
    if not aa_key:
        return [{**m, "score": unscored_fallback} for m in models]

    try:
        aa_lookup = await fetch_aa_lookup(aa_key)
    except Exception:
        # Falling back to 50 keeps a flaky AA from breaking routing, which is
        # deliberate — but doing it silently is what let a dead URL and the
        # null-score crash above hide for months behind a green suite.
        logger.warning("AA scoring failed; every model falls back to %s",
                       unscored_fallback, exc_info=True)
        return [{**m, "score": unscored_fallback} for m in models]

    # AA rescaled their index: as of this writing the 647 scored models run
    # 3..53 with a median of 11, so the old hardcoded 50 for an unmatched model
    # put it above 98.9% of everything AA has ever rated — an unknown model
    # outranking GPT-4o. Anchoring the fallback to the median of whatever came
    # back keeps unmatched models mid-pack and survives the next rescale
    # without anyone having to notice it happened.
    all_scores = [s for scores in aa_lookup.values() for s in scores]
    fallback = statistics.median_low(all_scores) if all_scores else unscored_fallback
    return [{**m, "score": _best_score(m.get("id", ""), aa_lookup, fallback)} for m in models]
