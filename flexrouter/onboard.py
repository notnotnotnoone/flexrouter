from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable
import re
import httpx

STANDARD_RL_HEADERS = {
    "rpm": "x-ratelimit-limit-requests",
    "tpm": "x-ratelimit-limit-tokens",
}


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


def _best_score(model_id: str, aa_lookup: list[tuple[str, int]]) -> int:
    needle = _normalize(model_id)
    for norm_name, score in aa_lookup:
        if needle in norm_name or norm_name in needle:
            return score
    return 50


async def score_with_aa(models: list[dict], aa_key: str | None) -> list[dict]:
    """Assign AA intelligence scores to models. Unmatched → score=50."""
    if not aa_key:
        return [{**m, "score": 50} for m in models]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://artificialanalysis.ai/data/llms/models",
                headers={"x-api-key": aa_key},
            )
            if resp.status_code >= 400:
                return [{**m, "score": 50} for m in models]
            data = resp.json()
        aa_models = data.get("data", [])
        aa_lookup = [
            (
                _normalize(entry.get("name", "")),
                int(entry.get("evaluations", {}).get("artificial_analysis_intelligence_index", 50)),
            )
            for entry in aa_models
        ]
    except Exception:
        return [{**m, "score": 50} for m in models]

    return [{**m, "score": _best_score(m["id"], aa_lookup)} for m in models]


def run_onboard() -> None:
    raise NotImplementedError("run_onboard is not yet implemented")
