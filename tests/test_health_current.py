from flexrouter.health_history import HealthHistory
from flexrouter.dashboard import api


def test_latest_returns_last_sample(tmp_path):
    hh = HealthHistory(str(tmp_path))
    hh.record({"models": {"groq/llama": {"status": "up"}}, "providers": {"groq": {"models_up": 1, "models_total": 1}}})
    hh.record({"models": {"groq/llama": {"status": "penalized"}}, "providers": {"groq": {"models_up": 0, "models_total": 1}}})
    assert hh.latest()["models"]["groq/llama"]["status"] == "penalized"


def test_latest_none_when_empty(tmp_path):
    assert HealthHistory(str(tmp_path)).latest() is None


def test_get_health_current_defaults(tmp_path):
    out = api.get_health_current(str(tmp_path))
    assert out == {"models": {}, "providers": {}}
