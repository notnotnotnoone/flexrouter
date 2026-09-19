"""A running router has to notice every file its configuration comes from.

Watching only the settings file means watching the one file nothing may
write. A model disabled in the dashboard lands in overrides.json and a key
lands in keys.json, so neither would be noticed until a restart.
"""
import os

import pytest
import yaml

from flexrouter import home
from flexrouter._router import FlexRouter
from flexrouter.keys import add_key
from flexrouter.overrides import save_overrides

SETTINGS = {
    "settings": {"port": 4891, "sample_interval_seconds": 100000},
    "providers": {"groq": {"base_url": "https://api.groq.com/openai/v1"}},
    "buckets": {"fast": [
        {"provider": "groq", "model": "keep-me", "score": 85, "rpm": 30, "tpm": 6000},
        {"provider": "groq", "model": "dead-model", "score": 90, "rpm": 30, "tpm": 6000},
    ]},
}


@pytest.fixture
def router(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "home"))
    home.ensure_home()
    home.config_path().write_text(yaml.dump(SETTINGS), encoding="utf-8")
    r = FlexRouter()
    yield r
    r.close()


def _age_the_watched_files(seconds: int = 10) -> None:
    """Push every watched file's timestamp back, so a write that follows is
    visibly newer even on a filesystem with a coarse clock."""
    past = None
    for path in (home.config_path(), home.overrides_path(), home.keys_path()):
        if path.exists():
            st = path.stat()
            past = st.st_mtime - seconds
            os.utime(path, (past, past))


def test_disabling_a_model_in_overrides_is_picked_up_without_a_restart(router):
    assert [m.model for m in router._cfg.tiers["fast"]] == ["keep-me", "dead-model"]

    _age_the_watched_files()
    save_overrides({"models": {"groq/dead-model": {"enabled": False}}})

    router._maybe_hot_reload()
    assert [m.model for m in router._cfg.tiers["fast"]] == ["keep-me"]


def test_a_settings_change_is_still_picked_up(router):
    _age_the_watched_files()
    changed = {**SETTINGS, "buckets": {"fast": [
        {"provider": "groq", "model": "brand-new", "score": 85, "rpm": 30, "tpm": 6000}]}}
    home.config_path().write_text(yaml.dump(changed), encoding="utf-8")

    router._maybe_hot_reload()
    assert [m.model for m in router._cfg.tiers["fast"]] == ["brand-new"]


def test_a_newly_saved_key_is_picked_up(router):
    assert router._cfg.providers["groq"].api_keys == []

    _age_the_watched_files()
    add_key("groq", "gsk-added-while-running")

    router._maybe_hot_reload()
    assert router._cfg.providers["groq"].api_keys == ["gsk-added-while-running"]


def test_nothing_reloads_when_nothing_changed(router):
    before = router._cfg
    router._maybe_hot_reload()
    assert router._cfg is before


def test_the_overrides_file_is_watched(router):
    assert home.overrides_path() in router._watched_paths()
    assert home.keys_path() in router._watched_paths()
    assert home.config_path() in router._watched_paths()
