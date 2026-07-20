import json
import time
from pathlib import Path

from flexrouter.quota import QuotaTracker


def test_no_quotas_configured_means_always_available(tmp_path):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    assert tracker.is_available("groq", "m1", {}) is True


def test_records_persist_to_disk_across_instances(tmp_path):
    state_dir = str(tmp_path / ".flexrouter")
    tracker1 = QuotaTracker(state_dir)
    tracker1.record("groq", "m1")
    tracker1.record("groq", "m1")

    tracker2 = QuotaTracker(state_dir)
    assert tracker2.is_available("groq", "m1", {"rpd": 2}) is False
    assert tracker2.is_available("groq", "m1", {"rpd": 3}) is True


def test_persist_uses_atomic_write(tmp_path):
    state_dir = tmp_path / ".flexrouter"
    tracker = QuotaTracker(str(state_dir))
    tracker.record("groq", "m1")
    assert (state_dir / "quotas.json").exists()
    assert not (state_dir / "quotas.tmp").exists()
    data = json.loads((state_dir / "quotas.json").read_text())
    assert "groq/m1" in data


def test_rph_and_rpd_are_independent_windows(tmp_path):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    for _ in range(5):
        tracker.record("groq", "m1")
    # rpd limit of 5 is exhausted, but rph limit of 100 is not — must check ALL configured windows
    assert tracker.is_available("groq", "m1", {"rpd": 5, "rph": 100}) is False
    assert tracker.is_available("groq", "m1", {"rph": 100}) is True


def test_unknown_quota_key_is_ignored(tmp_path):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    tracker.record("groq", "m1")
    assert tracker.is_available("groq", "m1", {"rpweek": 0}) is True


def test_seconds_until_available_when_exhausted(tmp_path, monkeypatch):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    fake_now = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])
    tracker.record("groq", "m1")
    wait = tracker.seconds_until_available("groq", "m1", {"rph": 1})
    assert 3599 <= wait <= 3600

    fake_now[0] += 3600.1
    assert tracker.is_available("groq", "m1", {"rph": 1}) is True


def test_different_provider_model_keys_are_independent(tmp_path):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    tracker.record("groq", "m1")
    assert tracker.is_available("groq", "m2", {"rpd": 1}) is True
    assert tracker.is_available("other", "m1", {"rpd": 1}) is True
