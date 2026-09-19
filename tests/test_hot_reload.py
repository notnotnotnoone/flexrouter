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


def test_a_vanished_settings_file_does_not_stop_a_running_router(router):
    """Deleting or renaming the settings file under a running router used to
    make every call after it fail. Nothing was read, so nothing changed."""
    before = router._cfg
    _age_the_watched_files()
    home.config_path().unlink()

    router._maybe_hot_reload()

    assert router._cfg is before
    assert router._reload_error is None
    assert router.remaining_capacity("fast")


def test_settings_that_cannot_be_read_leave_the_old_ones_serving(router, caplog):
    """A broken edit is a reason to keep the settings we have, not a reason
    to break the request in flight."""
    before = router._cfg
    _age_the_watched_files()
    home.config_path().write_text("providers: [unclosed\n", encoding="utf-8")

    with caplog.at_level("ERROR"):
        router._maybe_hot_reload()

    assert router._cfg is before
    assert [m.model for m in router._cfg.tiers["fast"]] == ["keep-me", "dead-model"]
    assert router._reload_error
    assert "still running on the ones it loaded earlier" in caplog.text


def test_a_failed_reload_is_not_retried_on_every_single_call(router):
    """The timestamp is taken before the attempt, so a file that stays broken
    costs one failed read, not one per request."""
    _age_the_watched_files()
    home.config_path().write_text("providers: [unclosed\n", encoding="utf-8")
    router._maybe_hot_reload()

    attempts = []
    original = router.reload

    def counting_reload():
        attempts.append(1)
        original()

    router.reload = counting_reload
    router._maybe_hot_reload()
    router._maybe_hot_reload()
    assert attempts == []


def test_a_good_edit_after_a_bad_one_is_still_picked_up(router):
    _age_the_watched_files()
    home.config_path().write_text("providers: [unclosed\n", encoding="utf-8")
    router._maybe_hot_reload()
    assert router._reload_error

    _age_the_watched_files()
    fixed = {**SETTINGS, "buckets": {"fast": [
        {"provider": "groq", "model": "brand-new", "score": 85, "rpm": 30,
         "tpm": 6000}]}}
    home.config_path().write_text(yaml.dump(fixed), encoding="utf-8")

    router._maybe_hot_reload()
    assert [m.model for m in router._cfg.tiers["fast"]] == ["brand-new"]
    assert router._reload_error is None


def test_an_edit_inside_one_clock_tick_is_still_noticed(router):
    """The watch used to compare a single newest timestamp, as a float.

    A filesystem clock is only so fine, and on Windows two writes can land in
    the same tick — so "newest" read identically either side of a real edit
    and the change was missed until something else happened to move. It made
    the suite flaky, which is the cheap version of the same bug: in a running
    service it means a change the owner made in the dashboard quietly does
    nothing. The fingerprint now carries each file's size as well, so an edit
    that changes the length is caught however coarse the clock is.
    """
    before = router._newest_mtime()

    swapped = {**SETTINGS, "buckets": {"fast": [
        {"provider": "groq", "model": "keep-me", "score": 85, "rpm": 30, "tpm": 6000},
    ]}}
    home.config_path().write_text(yaml.dump(swapped), encoding="utf-8")
    # Pin the timestamp back to exactly what it was, so only the size differs.
    st = home.config_path().stat()
    os.utime(home.config_path(), ns=(st.st_atime_ns, before[0][0]))

    assert router._newest_mtime() != before
    router._maybe_hot_reload()
    assert [m.model for m in router._cfg.tiers["fast"]] == ["keep-me"]
    assert router._reload_error is None


def test_a_file_going_backwards_in_time_is_noticed(router):
    """A single max() over timestamps also hid a file being replaced by an
    older copy — restoring a backup, or a sync tool writing an earlier
    version. The newest stayed the newest, so nothing reloaded."""
    before = router._newest_mtime()
    save_overrides({"models": {"groq/dead-model": {"enabled": False}}})
    st = home.overrides_path().stat()
    ancient = st.st_mtime_ns - 60_000_000_000
    os.utime(home.overrides_path(), ns=(ancient, ancient))

    assert router._newest_mtime() != before
    router._maybe_hot_reload()
    assert [m.model for m in router._cfg.tiers["fast"]] == ["keep-me"]
