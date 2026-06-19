# tests/test_uptime.py
from datetime import datetime, timezone, timedelta
from flexrouter.health_history import HealthHistory
from flexrouter.events import EventLogger
from flexrouter.dashboard.uptime import compute_uptime

def _iso(dt): return dt.isoformat(timespec="seconds")

def test_uptime_fraction_from_samples(tmp_path):
    hh = HealthHistory(str(tmp_path))
    now = datetime.now(timezone.utc)
    for i in range(10):
        state = "up" if i < 8 else "penalized"
        hh.record({"timestamp": _iso(now - timedelta(minutes=i)),
                   "models": {"groq/llama": {"status": state, "penalized": state == "penalized"}}})
    u = compute_uptime(str(tmp_path), now=now)
    model = next(m for m in u["models"] if m["model"] == "groq/llama")
    assert 0.7 <= model["uptime_24h"] <= 0.85

def test_no_samples_is_none(tmp_path):
    u = compute_uptime(str(tmp_path))
    assert u["models"] == []

def test_incidents_from_events(tmp_path):
    ev = EventLogger(str(tmp_path))
    ev.record("groq", "llama", "penalized", penalty_seconds=30)
    ev.record("groq", "llama", "recovered")
    u = compute_uptime(str(tmp_path))
    assert len(u["incidents"]) >= 1
    assert u["incidents"][0]["provider"] == "groq"
