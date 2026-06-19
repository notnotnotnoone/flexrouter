# tests/test_dashboard_api.py
from flexrouter.audit import AuditLogger
from flexrouter.dashboard import api

def test_get_stats_wraps_compute(tmp_path):
    AuditLogger(str(tmp_path)).log("default", "groq", "llama", 1, 1, 0.0, 100, "ok")
    s = api.get_stats(str(tmp_path))
    assert s["totals"]["requests"] == 1

def test_get_uptime_shape(tmp_path):
    u = api.get_uptime(str(tmp_path))
    assert set(u) == {"models", "incidents", "system", "providers"}

def test_get_config_validation_shape(monkeypatch):
    monkeypatch.setattr(api, "get_config", lambda: {
        "providers": {"groq": {"base_url": "https://x.com/v1", "api_keys": ["k"]}},
        "tiers": {"default": [{"provider": "groq", "model": "m", "score": 50, "rpm": 1, "tpm": 1}]},
    })
    v = api.get_config_validation()
    assert "errors" in v and "warnings" in v
