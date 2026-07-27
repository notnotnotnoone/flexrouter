from __future__ import annotations
import json
from pathlib import Path
from flexrouter.config import discover_config, load_config, validate_config
from flexrouter.dashboard.stats import compute_stats
from flexrouter.dashboard.uptime import compute_uptime
from flexrouter.exceptions import ConfigError
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


def get_config() -> dict:
    path = discover_config()
    if not path:
        return {}
    import yaml
    return yaml.safe_load(path.read_text()) or {}


def post_config(raw: dict) -> None:
    path = discover_config() or Path("flexrouter.yaml")
    import yaml
    path.write_text(yaml.dump(raw, default_flow_style=False))


def get_stats(state_dir: str) -> dict:
    return compute_stats(state_dir)


def get_uptime(state_dir: str) -> dict:
    return compute_uptime(state_dir)


def get_config_validation() -> dict:
    return validate_config(get_config())


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
    from flexrouter.refresh import refresh_config
    path = discover_config() or Path("flexrouter.yaml")
    return asdict(refresh_config(str(path), state_dir))
