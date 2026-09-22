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


def _googleai_free(model: dict) -> bool:
    mid = model.get("id", "").lower()
    return "flash" in mid


PROVIDERS: list[ProviderDef] = [
    ProviderDef(
        name="cerebras",
        base_url="https://api.cerebras.ai/v1",
        signup_url="https://cloud.cerebras.ai",
        free=True,
        free_filter=_all_free,
        default_rpm=30,
        default_tpm=60_000,
    ),
    ProviderDef(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        signup_url="https://console.groq.com/keys",
        free=True,
        free_filter=_all_free,
        default_rpm=30,
        default_tpm=6_000,
    ),
    ProviderDef(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        signup_url="https://openrouter.ai/keys",
        free=True,
        free_filter=_openrouter_free,
        default_rpm=20,
        default_tpm=100_000,
    ),
    ProviderDef(
        name="googleai",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        signup_url="https://aistudio.google.com/apikey",
        free=True,
        free_filter=_googleai_free,
        default_rpm=15,
        default_tpm=1_000_000,
    ),
    ProviderDef(
        name="ollama",
        base_url="http://localhost:11434/v1",
        signup_url="https://ollama.com",
        free=True,
        free_filter=_all_free,
        default_rpm=600,
        default_tpm=10_000_000,
        ollama=True,
    ),
    ProviderDef(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        signup_url="https://platform.deepseek.com",
        free=False,
        free_filter=_all_free,
        default_rpm=60,
        default_tpm=200_000,
    ),
    ProviderDef(
        name="siliconflow",
        base_url="https://api.siliconflow.cn/v1",
        signup_url="https://cloud.siliconflow.cn",
        free=False,
        free_filter=_all_free,
        default_rpm=20,
        default_tpm=100_000,
    ),
    ProviderDef(
        name="sambanova",
        base_url="https://api.sambanova.ai/v1",
        signup_url="https://cloud.sambanova.ai",
        free=False,
        free_filter=_all_free,
        default_rpm=20,
        default_tpm=50_000,
    ),
]


async def discover_models(provider: ProviderDef, api_key: str) -> list[dict]:
    """GET /v1/models for a provider, return free-filtered model dicts."""
    url = f"{provider.base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                return []
            data = resp.json()
        models = data.get("data", data) if isinstance(data, dict) else data
        return [m for m in models if provider.free_filter(m)]
    except Exception:
        return []


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
        if v is not None:
            return int(v)
    return 131_072


def _normalize(s: str) -> str:
    """Lowercase, strip provider prefix, remove :free suffix, collapse non-alphanum to spaces."""
    s = s.lower()
    s = re.sub(r":free$", "", s)          # remove :free suffix
    s = s.split("/")[-1]                   # take part after last /
    s = re.sub(r"[^a-z0-9 ]", " ", s)     # non-alphanum → space
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _best_score(model_id: str, aa_lookup: list[tuple[str, int]], fallback: int = UNSCORED_FALLBACK) -> int:
    needle = _normalize(model_id)
    for norm_name, score in aa_lookup:
        if needle in norm_name or norm_name in needle:
            return score
    return fallback


async def score_with_aa(models: list[dict], aa_key: str | None,
                        unscored_fallback: int = UNSCORED_FALLBACK) -> list[dict]:
    """Assign AA intelligence scores to models. Unmatched → score=50."""
    if not aa_key:
        return [{**m, "score": unscored_fallback} for m in models]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                AA_MODELS_URL,
                headers={"x-api-key": aa_key},
            )
            if resp.status_code >= 400:
                return [{**m, "score": unscored_fallback} for m in models]
            data = resp.json()
        aa_models = data.get("data", [])
        # A handful of AA entries carry the key with an explicit null (9 of 656
        # at time of writing, all clustered at the tail). `.get(key, 50)` does
        # NOT save you there — the default only applies when the key is absent,
        # so a null sailed through into int(None) and blew up the whole
        # comprehension, discarding every score that had already been built.
        # Skipping them is the honest reading: AA has no opinion on that model,
        # and _best_score already answers 50 for anything it cannot match.
        aa_lookup = []
        for entry in aa_models:
            raw = (entry.get("evaluations") or {}).get("artificial_analysis_intelligence_index")
            if not isinstance(raw, (int, float)):
                continue
            aa_lookup.append((_normalize(entry.get("name", "")), int(raw)))
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
    fallback = statistics.median_low([s for _, s in aa_lookup]) if aa_lookup else unscored_fallback
    return [{**m, "score": _best_score(m.get("id", ""), aa_lookup, fallback)} for m in models]
