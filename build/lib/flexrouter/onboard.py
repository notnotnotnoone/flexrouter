from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
import asyncio
import os
import re
import sys
import click
import httpx
import yaml

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
                int((entry.get("evaluations") or {}).get("artificial_analysis_intelligence_index", 50)),
            )
            for entry in aa_models
        ]
    except Exception:
        return [{**m, "score": 50} for m in models]

    return [{**m, "score": _best_score(m.get("id", ""), aa_lookup)} for m in models]


def _quote_yaml_scalar(value: str) -> str:
    """Safely quote a string for YAML by using the representer.

    Handles special characters like colons and hashes that could break YAML syntax.
    """
    test_yaml = yaml.dump({value: "dummy"})
    line = test_yaml.split('\n')[0]
    quoted_value = line.split(': ')[0]
    return quoted_value


def build_yaml(
    provider_keys: dict[str, str],
    free_models: list[dict],
    paid_models: list[dict],
    providers: list[ProviderDef],
) -> str:
    provider_map = {p.name: p for p in providers}

    # Collect all referenced provider names
    all_models = free_models + paid_models
    used_providers = {m["_provider"] for m in all_models}
    # Always include providers with keys even if no models found
    used_providers |= set(provider_keys.keys())

    lines = ["providers:"]
    for name in sorted(used_providers):
        pdef = provider_map[name]
        lines.append(f"  {name}:")
        lines.append(f"    base_url: {pdef.base_url}")
        key = provider_keys.get(name)
        if key:
            quoted_key = _quote_yaml_scalar(key)
            lines.append("    api_keys:")
            lines.append(f"      - key: {quoted_key}")
        else:
            lines.append("    api_keys: []")

    def model_block(m: dict, pdef: ProviderDef) -> list[str]:
        rpm = m["rpm"] if isinstance(m.get("rpm"), int) and m["rpm"] > 0 else pdef.default_rpm
        tpm = m["tpm"] if isinstance(m.get("tpm"), int) and m["tpm"] > 0 else pdef.default_tpm
        return [
            f"    - provider: {m['_provider']}",
            f"      model: {m['id']}",
            f"      score: {m['score']}",
            f"      rpm: {rpm}",
            f"      tpm: {tpm}",
            f"      context_window: {_context_window(m)}",
        ]

    lines.append("")
    if not free_models and not paid_models:
        lines.append("tiers: {}")
    else:
        lines.append("tiers:")

        if free_models:
            sorted_free = sorted(free_models, key=lambda m: m["score"], reverse=True)
            lines.append("  default:")
            for m in sorted_free:
                lines.extend(model_block(m, provider_map[m["_provider"]]))

        if paid_models:
            sorted_paid = sorted(paid_models, key=lambda m: m["score"], reverse=True)
            lines.append("  paid:")
            for m in sorted_paid:
                lines.extend(model_block(m, provider_map[m["_provider"]]))

    lines += [
        "",
        "settings:",
        "  state_dir: .flexrouter",
        "  dashboard_port: 7352",
        "",
    ]
    return "\n".join(lines)


def _read_existing_key(config_path: Path, provider_name: str) -> str | None:
    if not config_path.exists():
        return None
    try:
        raw = yaml.safe_load(config_path.read_text())
        keys = (raw or {}).get("providers", {}).get(provider_name, {}).get("api_keys", [])
        for k in keys:
            if isinstance(k, str) and k:
                return k
            if isinstance(k, dict) and k.get("key"):
                return k["key"]
    except Exception:
        pass
    return None


def _mask(key: str) -> str:
    if len(key) <= 8:
        return key[:2] + "***"
    return key[:6] + "..." + key[-4:]


def run_onboard() -> None:
    if not sys.stdin.isatty():
        click.echo("init requires an interactive terminal.")
        return

    config_path = Path("flexrouter.yaml")
    provider_keys: dict[str, str] = {}

    # --- Free providers ---
    free_providers = [p for p in PROVIDERS if p.free and not p.ollama]
    click.echo("\nflexrouter onboarding\n")
    click.echo("Free providers:")

    for pdef in free_providers:
        existing = _read_existing_key(config_path, pdef.name)
        click.echo(f"\n- {pdef.name}")
        click.echo(f"  signup: {pdef.signup_url}")
        click.echo(f"  current: {_mask(existing) if existing else '(none)'}")
        click.echo("  Enter key, press Enter to keep, or - to clear.")
        raw = input("  key: ").strip()
        if raw == "-":
            click.echo("  cleared")
        elif raw:
            provider_keys[pdef.name] = raw
            click.echo("  updated")
        elif existing:
            provider_keys[pdef.name] = existing
            click.echo("  unchanged")
        else:
            click.echo("  skipped")

    # --- Paid providers ---
    include_paid = click.confirm("\nInclude paid providers?", default=False)
    paid_providers: list[ProviderDef] = []
    if include_paid:
        paid_defs = [p for p in PROVIDERS if not p.free]
        for pdef in paid_defs:
            existing = _read_existing_key(config_path, pdef.name)
            click.echo(f"\n- {pdef.name} (paid)")
            click.echo(f"  signup: {pdef.signup_url}")
            click.echo(f"  current: {_mask(existing) if existing else '(none)'}")
            click.echo("  Enter key, press Enter to keep, or - to clear.")
            raw = input("  key: ").strip()
            if raw == "-":
                click.echo("  cleared")
            elif raw:
                provider_keys[pdef.name] = raw
                paid_providers.append(pdef)
                click.echo("  updated")
            elif existing:
                provider_keys[pdef.name] = existing
                paid_providers.append(pdef)
                click.echo("  unchanged")
            else:
                click.echo("  skipped")

    if not provider_keys:
        click.echo("\nNo keys provided. flexrouter.yaml not written.")
        return

    # --- Discover models ---
    click.echo("\nDiscovering models...")

    async def _discover_all() -> tuple[list[dict], list[dict]]:
        free_models: list[dict] = []
        p_models: list[dict] = []

        for pdef in free_providers:
            key = provider_keys.get(pdef.name)
            if not key:
                continue
            models = await discover_models(pdef, key)
            for m in models:
                m["_provider"] = pdef.name
            free_models.extend(models)
            click.echo(f"  {pdef.name}: {len(models)} free models")

        # Ollama
        ollama_models = await discover_ollama()
        for m in ollama_models:
            m["_provider"] = "ollama"
        if ollama_models:
            click.echo(f"  ollama: {len(ollama_models)} local models")
            free_models.extend(ollama_models)

        for pdef in paid_providers:
            key = provider_keys.get(pdef.name)
            if not key:
                continue
            models = await discover_models(pdef, key)
            for m in models:
                m["_provider"] = pdef.name
            p_models.extend(models)
            click.echo(f"  {pdef.name}: {len(models)} models")

        return free_models, p_models

    free_models, paid_models = asyncio.run(_discover_all())

    # --- AA Scoring ---
    aa_key = os.environ.get("AA_API_KEY")
    if aa_key:
        click.echo("Scoring with Artificial Analysis...")
        all_models = asyncio.run(score_with_aa(free_models + paid_models, aa_key))
        split = len(free_models)
        free_models = all_models[:split]
        paid_models = all_models[split:]
    else:
        click.echo("AA_API_KEY not set — using score=50 for all models.")
        free_models = [{**m, "score": 50} for m in free_models]
        paid_models = [{**m, "score": 50} for m in paid_models]

    # --- Write yaml ---
    text = build_yaml(provider_keys, free_models, paid_models, PROVIDERS)
    config_path.write_text(text)
    click.echo(f"\nWritten to {config_path}")
    click.echo(f"  {len(free_models)} free models, {len(paid_models)} paid models")
    click.echo("Run `flexrouter dashboard` to start.")
