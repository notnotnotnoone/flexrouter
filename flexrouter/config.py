from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from flexrouter import home
from flexrouter.exceptions import ConfigError
from flexrouter.keys import KeyRecord, load_keys
from flexrouter.overrides import apply_overrides, load_overrides

RETRY_PRESETS = {
    "conservative": {"retries": 2, "backoff_seconds": 5.0},
    "balanced":     {"retries": 3, "backoff_seconds": 2.0},
    "aggressive":   {"retries": 5, "backoff_seconds": 1.0},
}

@dataclass
class ModelConfig:
    provider: str
    model: str
    score: int
    rpm: int
    tpm: int
    context_window: int = 200000
    vision: bool = False
    quotas: dict[str, int] = field(default_factory=dict)

@dataclass
class ProviderConfig:
    base_url: str
    api_keys: list[str]  # resolved secret strings, in selection order
    header_parser: str = "openai_compatible"
    keys: list[KeyRecord] = field(default_factory=list)

@dataclass
class RetryConfig:
    retries: int = 3
    backoff_seconds: float = 2.0

@dataclass
class FlexConfig:
    tiers: dict[str, list[ModelConfig]]
    providers: dict[str, ProviderConfig]
    state_dir: str = ".flexrouter"
    window_seconds: int = 60
    penalty_base_seconds: int = 30
    penalty_max_seconds: int = 1800
    session_ttl_minutes: int = 30
    port: int = 4891
    dashboard_port: int = 7352  # deprecated alias for `port`
    sample_interval_seconds: int = 60
    health_history_days: int = 30
    retry: RetryConfig = field(default_factory=RetryConfig)
    provider_budget: dict[str, float] = field(default_factory=dict)
    hooks: list[str] = field(default_factory=list)


def _line_of(text: str, needle: str) -> int | None:
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return i
    return None


def _env_names(praw: dict) -> list[str]:
    """Env var names this provider declares, in order."""
    names: list[str] = []
    single = praw.get("api_key_env")
    if single:
        names.append(single)
    raw_keys = praw.get("api_keys", [])
    if isinstance(raw_keys, str):
        raw_keys = [{"env": raw_keys}]
    for k in raw_keys or []:
        if isinstance(k, dict) and k.get("env"):
            names.append(k["env"])
    return names


def _inline_secrets(praw: dict) -> list[str]:
    """Secrets typed straight into the settings file. Deprecated."""
    found: list[str] = []
    if praw.get("api_key"):
        found.append(str(praw["api_key"]))
    raw_keys = praw.get("api_keys", [])
    if isinstance(raw_keys, str):
        raw_keys = []
    for k in raw_keys or []:
        if isinstance(k, str) and k:
            found.append(k)
        elif isinstance(k, dict) and k.get("key"):
            found.append(str(k["key"]))
    return found


def resolve_keys(provider: str, praw: dict, vault: dict[str, list[KeyRecord]],
                 source_path: Path | None) -> list[KeyRecord]:
    """Credential resolution order (spec 1): stored key, then environment
    variable, then an inline secret - which is deprecated and warns."""
    stored = [r for r in vault.get(provider, []) if r.enabled and r.secret]
    if stored:
        return stored

    from_env: list[KeyRecord] = []
    for name in _env_names(praw):
        value = os.environ.get(name)
        if value:
            from_env.append(KeyRecord(id=f"env:{name}", secret=value,
                                      label=name, source="env"))
    if from_env:
        return from_env

    inline = _inline_secrets(praw)
    if not inline:
        return []

    text = source_path.read_text(encoding="utf-8") if source_path else ""
    where = _line_of(text, inline[0]) if text else None
    filename = source_path.name if source_path else "config.yaml"
    warnings.warn(
        f"Provider {provider!r} has its key typed straight into {filename}"
        f"{f', line {where}' if where else ''}. That file is meant to be "
        f"shareable. Move it with: flexrouter keys add {provider}",
        DeprecationWarning, stacklevel=2,
    )
    return [KeyRecord(id=f"{provider}-inline-{i + 1}", secret=s, source="inline")
            for i, s in enumerate(inline)]


def load_config(path: Path | str | None = None) -> FlexConfig:
    """Load settings from the flexrouter home, with overrides layered on top."""
    home.ensure_home()
    source = Path(path) if path else home.config_path()
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except Exception as e:
        raise ConfigError(f"Cannot read {source}: {e}") from e

    if not raw:
        raise ConfigError(f"{source} is empty")

    raw = apply_overrides(raw, load_overrides())
    settings = raw.get("settings") or {}

    preset_name = settings.get("retry_policy", "balanced")
    preset = RETRY_PRESETS.get(preset_name, RETRY_PRESETS["balanced"])
    retry = RetryConfig(
        retries=settings.get("retries", preset["retries"]),
        backoff_seconds=float(settings.get("backoff_seconds", preset["backoff_seconds"])),
    )

    vault = load_keys()
    providers: dict[str, ProviderConfig] = {}
    for name, praw in (raw.get("providers") or {}).items():
        praw = praw or {}
        records = resolve_keys(name, praw, vault, source)
        providers[name] = ProviderConfig(
            base_url=praw["base_url"],
            api_keys=[r.secret for r in records],
            header_parser=praw.get("header_parser", "openai_compatible"),
            keys=records,
        )

    buckets_raw = raw.get("buckets")
    if buckets_raw is None:
        buckets_raw = raw.get("tiers")
    tiers: dict[str, list[ModelConfig]] = {}
    for bucket_name, models in (buckets_raw or {}).items():
        tiers[bucket_name] = [
            ModelConfig(
                provider=m["provider"],
                model=m["model"],
                score=m["score"],
                rpm=m["rpm"],
                tpm=m["tpm"],
                context_window=m.get("context_window", 200000),
                vision=m.get("vision", False),
                quotas=m.get("quotas", {}),
            )
            for m in (models or [])
        ]

    port = int(settings.get("port", settings.get("dashboard_port", home.DEFAULT_PORT)))
    return FlexConfig(
        tiers=tiers,
        providers=providers,
        state_dir=settings.get("state_dir", str(home.state_dir())),
        window_seconds=int(settings.get("window_seconds", 60)),
        penalty_base_seconds=int(settings.get("penalty_base_seconds", 30)),
        penalty_max_seconds=int(settings.get("penalty_max_seconds", 1800)),
        session_ttl_minutes=int(settings.get("session_ttl_minutes", 30)),
        port=port,
        dashboard_port=port,
        sample_interval_seconds=int(settings.get("sample_interval_seconds", 60)),
        health_history_days=int(settings.get("health_history_days", 30)),
        retry=retry,
        provider_budget=settings.get("provider_budget", {}),
        hooks=settings.get("hooks", []),
    )


NON_CHAT_PATTERNS = ("whisper", "tts", "orpheus", "image", "lyria", "guard",
                     "native-audio", "-live", "embedding", "rerank")
_LOCAL_HOST_HINTS = ("localhost", "127.0.0.1", "::1")


def is_probably_chat_model(model_id: str) -> bool:
    mid = (model_id or "").lower()
    return not any(p in mid for p in NON_CHAT_PATTERNS)


def validate_config(raw: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    providers = raw.get("providers") or {}
    if not providers:
        errors.append("providers: no providers defined")

    for name, praw in providers.items():
        if not isinstance(praw, dict) or not praw.get("base_url"):
            errors.append(f"providers.{name}: missing base_url")
            continue
        base_url = str(praw.get("base_url", ""))
        if not base_url.startswith(("http://", "https://")):
            errors.append(f"providers.{name}.base_url: must start with http:// or https://")
        keys = praw.get("api_keys", [])
        is_local = any(h in base_url for h in _LOCAL_HOST_HINTS)
        if not keys and not is_local:
            warnings.append(f"providers.{name}.api_keys: empty for a non-local provider")

    tiers = raw.get("tiers") or {}
    if not tiers:
        errors.append("tiers: no tiers defined")

    for tier_name, models in tiers.items():
        for i, m in enumerate(models or []):
            where = f"tiers.{tier_name}[{i}]"
            if not isinstance(m, dict):
                errors.append(f"{where}: not a mapping")
                continue
            provider = m.get("provider")
            model = m.get("model")
            if not provider:
                errors.append(f"{where}.provider: missing")
            elif provider not in providers:
                errors.append(f"{where}.provider: {provider!r} not defined in providers")
            if not model:
                errors.append(f"{where}.model: missing")
            for field_name in ("score", "rpm", "tpm"):
                val = m.get(field_name)
                if val is None:
                    errors.append(f"{where}.{field_name}: missing")
                elif not isinstance(val, (int, float)) or val <= 0:
                    errors.append(f"{where}.{field_name}: must be a positive number, got {val!r}")
            ctx = m.get("context_window")
            if isinstance(ctx, (int, float)) and ctx < 1000:
                warnings.append(
                    f"{where}.context_window: {ctx} is suspiciously low — "
                    f"{model!r} may not be a chat model")
            if model and not is_probably_chat_model(str(model)):
                warnings.append(
                    f"{where}: {model!r} matches a non-chat name pattern — "
                    f"likely not a chat-completions model")

    return {"errors": errors, "warnings": warnings}
