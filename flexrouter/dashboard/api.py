from __future__ import annotations
import json
from pathlib import Path
from flexrouter.config import validate_config
from flexrouter.dashboard.stats import compute_stats
from flexrouter.dashboard.uptime import compute_uptime
from flexrouter.health_history import HealthHistory


def get_status(state_dir: str) -> dict:
    health_path = Path(state_dir) / "health.json"
    if health_path.exists():
        return json.loads(health_path.read_text())
    return {"total_cost_usd": 0.0, "session_start": None, "providers": {}, "models": {}}


def get_logs(state_dir: str, n: int = 50) -> list[dict]:
    import csv
    csv_path = Path(state_dir) / "audit.csv"
    if not csv_path.exists():
        return []
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    return rows[-n:]


def _config_unredacted() -> dict:
    """The settings as flexrouter sees them: the file, plus overrides.

    Never return this from anything a caller can reach. A key typed into the
    settings file is a supported (deprecated) way to reach a provider, so this
    structure can contain live secrets.
    """
    import yaml

    from flexrouter import home
    from flexrouter.overrides import apply_overrides, load_overrides

    home.ensure_home()
    raw = yaml.safe_load(home.config_path().read_text(encoding="utf-8")) or {}
    return apply_overrides(raw, load_overrides())


def get_config() -> dict:
    """The settings as flexrouter sees them, with every credential masked."""
    from flexrouter.config import redact_config

    return redact_config(_config_unredacted())


def get_overrides() -> dict:
    from flexrouter.overrides import load_overrides
    return load_overrides()


def post_config(raw: dict) -> None:
    """Record a settings change as an override.

    config.yaml is hand-written and is never rewritten — that is what keeps
    the owner's comments alive (spec §1).
    """
    from flexrouter.overrides import load_overrides, save_overrides

    for name, praw in (raw.get("providers") or {}).items():
        if not isinstance(praw, dict):
            continue
        if praw.get("api_key") or praw.get("api_keys"):
            raise ValueError(
                f"Credentials for {name!r} don't go in settings — "
                f"add them with: flexrouter keys add {name}")

    data = load_overrides()
    for key, value in (raw.get("settings") or {}).items():
        data.setdefault("settings", {})[key] = value
    for name, fields in (raw.get("providers") or {}).items():
        data.setdefault("providers", {}).setdefault(name, {}).update(fields or {})
    for ident, fields in (raw.get("models") or {}).items():
        data.setdefault("models", {}).setdefault(ident, {}).update(fields or {})
    save_overrides(data)


def get_stats(state_dir: str) -> dict:
    return compute_stats(state_dir)


def get_uptime(state_dir: str) -> dict:
    return compute_uptime(state_dir)


def get_config_validation() -> dict:
    # Checked against the unredacted structure: masking a key would look
    # like a different key, and validation never leaves this process.
    return validate_config(_config_unredacted())


def get_health_current(state_dir: str) -> dict:
    latest = HealthHistory(state_dir).latest()
    if not latest:
        return {"models": {}, "providers": {}}
    return {"models": latest.get("models", {}), "providers": latest.get("providers", {})}


def get_last_refresh(state_dir: str) -> dict:
    path = Path(state_dir) / "last_refresh.json"
    if not path.exists():
        return {"timestamp": None}
    return json.loads(path.read_text())


def run_refresh(state_dir: str) -> dict:
    from dataclasses import asdict

    from flexrouter import home
    from flexrouter.refresh import refresh_config
    return asdict(refresh_config(str(home.config_path()), state_dir))
