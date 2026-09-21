# tests/test_key_state.py
import json
import time
from datetime import datetime, timedelta, timezone

from flexrouter.key_state import KeyState, KeyStateStore


def test_an_unseen_key_defaults_to_live(tmp_path):
    store = KeyStateStore(str(tmp_path))
    s = store.get("openrouter", "or-main")
    assert s.status == "live"
    assert store.is_available("openrouter", "or-main")


def test_mark_success_resets_failures_and_updates_counters(tmp_path):
    store = KeyStateStore(str(tmp_path))
    store.mark_cooling("openrouter", "or-main", 60, "too_fast")
    store.mark_success("openrouter", "or-main", tokens=120, latency_ms=340)
    s = store.get("openrouter", "or-main")
    assert s.status == "live"
    assert s.consecutive_failures == 0
    assert s.tokens_today == 120
    assert s.requests_today == 1
    assert s.ema_latency_ms == 340


def test_mark_cooling_floors_at_thirty_seconds(tmp_path):
    store = KeyStateStore(str(tmp_path))
    now = time.time()
    store.mark_cooling("openrouter", "or-main", 5, "too_fast", now=now)
    s = store.get("openrouter", "or-main")
    assert s.status == "cooling"
    assert s.until >= now + 30


def test_cooling_key_becomes_available_after_its_window(tmp_path):
    store = KeyStateStore(str(tmp_path))
    now = time.time()
    store.mark_cooling("openrouter", "or-main", 30, "too_fast", now=now)
    assert store.is_available("openrouter", "or-main", now=now) is False
    assert store.is_available("openrouter", "or-main", now=now + 31) is True
    s = store.get("openrouter", "or-main")
    assert s.status == "live"  # auto-recovered


def test_benched_key_never_recovers_on_its_own(tmp_path):
    store = KeyStateStore(str(tmp_path))
    now = time.time()
    store.mark_benched("openrouter", "or-main", "bad_key", now=now)
    assert store.is_available("openrouter", "or-main", now=now + 1_000_000) is False
    assert store.seconds_until_available("openrouter", "or-main", now=now) == float("inf")


def test_ema_latency_blends_toward_new_samples(tmp_path):
    store = KeyStateStore(str(tmp_path))
    store.mark_success("openrouter", "or-main", tokens=1, latency_ms=100)
    store.mark_success("openrouter", "or-main", tokens=1, latency_ms=1100)
    s = store.get("openrouter", "or-main")
    # 0.3*1100 + 0.7*100 = 400
    assert s.ema_latency_ms == 400


def test_daily_counters_reset_on_a_new_utc_day(tmp_path):
    store = KeyStateStore(str(tmp_path))
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    store.mark_success("openrouter", "or-main", tokens=500, latency_ms=10, now=yesterday.timestamp())
    s = store.get("openrouter", "or-main", now=yesterday.timestamp())
    assert s.tokens_today == 500

    store.mark_success("openrouter", "or-main", tokens=7, latency_ms=10)  # today, real time
    s = store.get("openrouter", "or-main")
    assert s.tokens_today == 7  # not 507 — yesterday's count did not carry over


def test_get_alone_also_rolls_over_stale_daily_counters(tmp_path):
    store = KeyStateStore(str(tmp_path))
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    store.mark_benched("openrouter", "or-main", "bad_key", now=yesterday.timestamp())
    s = store.get("openrouter", "or-main", now=yesterday.timestamp())
    assert s.failures_24h == 1

    # New UTC day, and only a read — no intervening mark_* call.
    s = store.get("openrouter", "or-main")
    assert s.requests_today == 0
    assert s.tokens_today == 0
    assert s.failures_24h == 0


def test_all_unavailable_true_only_when_every_key_is_down(tmp_path):
    store = KeyStateStore(str(tmp_path))
    now = time.time()
    store.mark_cooling("openrouter", "k1", 60, "too_fast", now=now)
    assert store.all_unavailable("openrouter", ["k1", "k2"], now=now) is False  # k2 unseen -> live
    store.mark_benched("openrouter", "k2", "bad_key", now=now)
    assert store.all_unavailable("openrouter", ["k1", "k2"], now=now) is True


def test_all_benched_or_disabled_is_false_if_any_key_is_only_cooling(tmp_path):
    store = KeyStateStore(str(tmp_path))
    now = time.time()
    store.mark_cooling("openrouter", "k1", 60, "too_fast", now=now)
    store.mark_benched("openrouter", "k2", "bad_key", now=now)
    assert store.all_benched_or_disabled("openrouter", ["k1", "k2"], now=now) is False
    assert store.all_unavailable("openrouter", ["k1", "k2"], now=now) is True


def test_min_seconds_until_available_picks_the_soonest(tmp_path):
    store = KeyStateStore(str(tmp_path))
    now = time.time()
    store.mark_cooling("openrouter", "k1", 90, "too_fast", now=now)
    store.mark_cooling("openrouter", "k2", 30, "too_fast", now=now)
    secs = store.min_seconds_until_available("openrouter", ["k1", "k2"], now=now)
    assert 29 <= secs <= 31


def test_begin_and_end_request_track_active_requests(tmp_path):
    store = KeyStateStore(str(tmp_path))
    store.begin_request("openrouter", "k1")
    store.begin_request("openrouter", "k1")
    assert store.get("openrouter", "k1").active_requests == 2
    store.end_request("openrouter", "k1")
    assert store.get("openrouter", "k1").active_requests == 1


def test_end_request_never_goes_negative(tmp_path):
    store = KeyStateStore(str(tmp_path))
    store.end_request("openrouter", "k1")
    assert store.get("openrouter", "k1").active_requests == 0


def test_state_survives_a_new_store_instance(tmp_path):
    store = KeyStateStore(str(tmp_path))
    store.mark_benched("openrouter", "k1", "bad_key")
    reloaded = KeyStateStore(str(tmp_path))
    assert reloaded.get("openrouter", "k1").status == "benched"


def test_no_secret_ever_appears_in_the_state_file(tmp_path):
    store = KeyStateStore(str(tmp_path))
    store.mark_benched("openrouter", "sk-should-not-appear-here", "bad_key")
    on_disk = (tmp_path / "key_state.json").read_text(encoding="utf-8")
    # The key_id itself is expected to appear (it's an identifier, not a
    # secret) — this test guards against a future change accidentally
    # writing a `secret` field into the state file.
    assert "secret" not in on_disk
