from __future__ import annotations
import json
from pathlib import Path
from flexrouter.config import discover_config, load_config
from flexrouter.exceptions import ConfigError


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
