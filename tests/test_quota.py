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


# ── token-count windows (tps/tph/tpd) ────────────────────────────────────

def test_tpd_sums_tokens_not_request_count(tmp_path):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    tracker.record("groq", "m1", tokens=400)
    tracker.record("groq", "m1", tokens=400)
    assert tracker.is_available("groq", "m1", {"tpd": 1000}) is True
    tracker.record("groq", "m1", tokens=400)
    assert tracker.is_available("groq", "m1", {"tpd": 1000}) is False


def test_request_and_token_windows_are_tracked_from_the_same_events(tmp_path):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    tracker.record("groq", "m1", tokens=10000)
    # one huge call blows the token budget but not a generous request budget
    assert tracker.is_available("groq", "m1", {"rpd": 100, "tpd": 5000}) is False
    assert tracker.is_available("groq", "m1", {"rpd": 100}) is True


def test_tokens_default_to_zero_when_not_given(tmp_path):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    tracker.record("groq", "m1")
    assert tracker.is_available("groq", "m1", {"tpd": 1}) is True


def test_token_records_persist_to_disk_across_instances(tmp_path):
    state_dir = str(tmp_path / ".flexrouter")
    tracker1 = QuotaTracker(state_dir)
    tracker1.record("groq", "m1", tokens=600)
    tracker1.record("groq", "m1", tokens=600)

    tracker2 = QuotaTracker(state_dir)
    assert tracker2.is_available("groq", "m1", {"tpd": 1000}) is False
    assert tracker2.is_available("groq", "m1", {"tpd": 2000}) is True


def test_pre_token_tracking_quotas_json_is_read_as_zero_tokens(tmp_path):
    """A quotas.json written before token tracking existed stores bare
    timestamps, not [timestamp, tokens] pairs. Loading it must not blow up,
    and those old events count as zero tokens rather than crash the app."""
    state_dir = tmp_path / ".flexrouter"
    state_dir.mkdir(parents=True)
    now = time.time()
    (state_dir / "quotas.json").write_text(json.dumps({"groq/m1": [now, now]}))

    tracker = QuotaTracker(str(state_dir))
    assert tracker.is_available("groq", "m1", {"rpd": 2}) is False
    assert tracker.is_available("groq", "m1", {"tpd": 1}) is True


def test_seconds_until_available_for_a_token_window(tmp_path, monkeypatch):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    fake_now = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])
    tracker.record("groq", "m1", tokens=600)
    fake_now[0] += 10
    tracker.record("groq", "m1", tokens=600)

    wait = tracker.seconds_until_available("groq", "m1", {"tph": 1000})
    # must wait for the FIRST 600-token event to fall out of the hour
    # window before the running total drops back under 1000
    assert 3589 <= wait <= 3591

    fake_now[0] += 3600
    assert tracker.is_available("groq", "m1", {"tph": 1000}) is True


def test_tps_is_a_one_second_window(tmp_path, monkeypatch):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    fake_now = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])
    tracker.record("groq", "m1", tokens=500)
    assert tracker.is_available("groq", "m1", {"tps": 500}) is False

    fake_now[0] += 1.1
    assert tracker.is_available("groq", "m1", {"tps": 500}) is True


def test_rpm_is_a_sixty_second_window(tmp_path, monkeypatch):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    fake_now = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])
    tracker.record("groq", "m1")
    assert tracker.is_available("groq", "m1", {"rpm": 1}) is False

    fake_now[0] += 60.1
    assert tracker.is_available("groq", "m1", {"rpm": 1}) is True


def test_tpm_sums_tokens_in_a_sixty_second_window(tmp_path, monkeypatch):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    fake_now = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])
    tracker.record("groq", "m1", tokens=400)
    assert tracker.is_available("groq", "m1", {"tpm": 500}) is True
    tracker.record("groq", "m1", tokens=200)
    assert tracker.is_available("groq", "m1", {"tpm": 500}) is False

    fake_now[0] += 60.1
    assert tracker.is_available("groq", "m1", {"tpm": 500}) is True
