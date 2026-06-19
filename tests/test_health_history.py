# tests/test_health_history.py
import json
from datetime import datetime, timezone, timedelta
from flexrouter.health_history import HealthHistory

def test_record_appends_jsonl(tmp_path):
    hh = HealthHistory(str(tmp_path))
    hh.record({"models": {"groq/llama": {"status": "up"}}})
    lines = (tmp_path / "health_history.jsonl").read_text().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["models"]["groq/llama"]["status"] == "up"
    assert "timestamp" in obj  # injected automatically

def test_read_filters_since(tmp_path):
    hh = HealthHistory(str(tmp_path))
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    new = datetime.now(timezone.utc).isoformat(timespec="seconds")
    hh.record({"timestamp": old, "models": {}})
    hh.record({"timestamp": new, "models": {}})
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    assert len(hh.read(since_iso=cutoff)) == 1

def test_compact_drops_beyond_retention(tmp_path):
    hh = HealthHistory(str(tmp_path), retention_days=7)
    ancient = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds")
    fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
    hh.record({"timestamp": ancient, "models": {}})
    hh.record({"timestamp": fresh, "models": {}})
    hh.compact()
    assert len(hh.read()) == 1
