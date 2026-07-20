from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import yaml
from flexrouter.exceptions import ConfigError

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

@dataclass
class ProviderConfig:
    base_url: str
    api_keys: list[str]  # resolved values (not env var names)
    header_parser: str = "openai_compatible"

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
    dashboard_port: int = 7352
    sample_interval_seconds: int = 60
    health_history_days: int = 30
    retry: RetryConfig = field(default_factory=RetryConfig)
    provider_budget: dict[str, float] = field(default_factory=dict)
    hooks: list[str] = field(default_factory=list)


def load_config(path: Path | str) -> FlexConfig:
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except Exception as e:
        raise ConfigError(f"Cannot read {path}: {e}") from e

    if not raw:
        raise ConfigError(f"{path} is empty")

    settings = raw.get("settings", {})

    # Parse retry
    preset_name = settings.get("retry_policy", "balanced")
    preset = RETRY_PRESETS.get(preset_name, RETRY_PRESETS["balanced"])
    retry = RetryConfig(
        retries=settings.get("retries", preset["retries"]),
        backoff_seconds=float(settings.get("backoff_seconds", preset["backoff_seconds"])),
    )

    # Parse providers — resolve env vars
    providers: dict[str, ProviderConfig] = {}
    for name, praw in (raw.get("providers") or {}).items():
        keys_raw = praw.get("api_keys", [])
        if isinstance(keys_raw, str):
            keys_raw = [{"env": keys_raw}]
        resolved: list[str] = []
        for k in keys_raw:
            if isinstance(k, str):
                if k:
                    resolved.append(k)
            elif "key" in k:
                if k["key"]:
                    resolved.append(k["key"])
            elif "env" in k:
                env_name = k["env"]
                val = os.environ.get(env_name)
                if not val:
                    raise ConfigError(f"Env var {env_name!r} not set (required by provider {name!r})")
                resolved.append(val)
        providers[name] = ProviderConfig(
            base_url=praw["base_url"],
            api_keys=resolved,
            header_parser=praw.get("header_parser", "openai_compatible"),
        )

    # Parse tiers
    tiers: dict[str, list[ModelConfig]] = {}
    for tier_name, models in (raw.get("tiers") or {}).items():
        tiers[tier_name] = [
            ModelConfig(
                provider=m["provider"],
                model=m["model"],
                score=m["score"],
                rpm=m["rpm"],
                tpm=m["tpm"],
                context_window=m.get("context_window", 200000),
                vision=m.get("vision", False),
            )
            for m in models
        ]

    return FlexConfig(
        tiers=tiers,
        providers=providers,
        state_dir=settings.get("state_dir", ".flexrouter"),
        window_seconds=int(settings.get("window_seconds", 60)),
        penalty_base_seconds=int(settings.get("penalty_base_seconds", 30)),
        penalty_max_seconds=int(settings.get("penalty_max_seconds", 1800)),
        session_ttl_minutes=int(settings.get("session_ttl_minutes", 30)),
        dashboard_port=int(settings.get("dashboard_port", 7352)),
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


def discover_config() -> Optional[Path]:
    """Search CWD then home directory for flexrouter.yaml."""
    cwd_path = Path("flexrouter.yaml")
    if cwd_path.exists():
        return cwd_path
    home_path = Path.home() / ".flexrouter.yaml"
    if home_path.exists():
        return home_path
    return None
