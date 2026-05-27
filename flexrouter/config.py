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
    for name, praw in raw.get("providers", {}).items():
        keys_raw = praw.get("api_keys", [])
        if isinstance(keys_raw, str):
            keys_raw = [{"env": keys_raw}]
        resolved: list[str] = []
        for k in keys_raw:
            env_name = k["env"]
            val = os.environ.get(env_name)
            if not val:
                raise ConfigError(f"Env var {env_name!r} not set (required by provider {name!r})")
            resolved.append(val)
        providers[name] = ProviderConfig(base_url=praw["base_url"], api_keys=resolved)

    # Parse tiers
    tiers: dict[str, list[ModelConfig]] = {}
    for tier_name, models in raw.get("tiers", {}).items():
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
        retry=retry,
        provider_budget=settings.get("provider_budget", {}),
        hooks=settings.get("hooks", []),
    )


def discover_config() -> Optional[Path]:
    """Search CWD then home directory for flexrouter.yaml."""
    cwd_path = Path("flexrouter.yaml")
    if cwd_path.exists():
        return cwd_path
    home_path = Path.home() / ".flexrouter.yaml"
    if home_path.exists():
        return home_path
    return None
