# tests/test_dashboard_api.py
import pytest

from flexrouter.audit import AuditLogger
from flexrouter import home
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


@pytest.fixture
def api_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    home.ensure_home()
    home.config_path().write_text(
        "# keep me\nsettings:\n  port: 4891\nproviders: {}\nbuckets:\n  smart: []\n",
        encoding="utf-8")
    return tmp_path


def test_get_config_reads_the_home(api_home):
    assert api.get_config()["settings"]["port"] == 4891


def test_post_config_never_touches_the_settings_file(api_home):
    before = home.config_path().read_bytes()
    api.post_config({"settings": {"port": 7000}})
    assert home.config_path().read_bytes() == before
    assert "# keep me" in home.config_path().read_text(encoding="utf-8")


def test_post_config_writes_an_override_instead(api_home):
    api.post_config({"settings": {"port": 7000}})
    assert api.get_overrides()["settings"]["port"] == 7000


def test_post_config_shows_up_in_get_config(api_home):
    api.post_config({"settings": {"port": 7000}})
    assert api.get_config()["settings"]["port"] == 7000


def test_post_config_rejects_credentials(api_home):
    with pytest.raises(ValueError):
        api.post_config({"providers": {"groq": {"api_key": "sk-nope"}}})
