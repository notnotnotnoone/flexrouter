# tests/test_stats_window.py
"""`recent_window` is what the Overview reads. `compute_stats` next to it
answers over all of history; these guard the difference."""
import csv
from datetime import datetime, timedelta, timezone

from flexrouter.dashboard.stats import recent_window

HEADERS = ["timestamp", "tier", "provider", "model", "prompt_tokens",
           "completion_tokens", "cost_usd", "latency_ms", "status"]


def _write(tmp_path, rows):
    with (tmp_path / "audit.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(rows)
    return str(tmp_path)


def _row(when, provider="groq", model="llama", status="ok",
         latency=200, tier="default"):
    return {
        "timestamp": when.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "tier": tier, "provider": provider, "model": model,
        "prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 0.0,
        "latency_ms": latency, "status": status,
    }


def test_no_audit_file_is_an_empty_window_not_a_crash(tmp_path):
    w = recent_window(str(tmp_path))
    assert w["requests"] == 0
    assert w["providers"] == []
    assert w["answered_rate"] is None
    # The shape is still complete, so the page has something to render.
    assert len(w["labels"]) == 24
    assert len(w["fails_by_hour"]) == 24


def test_the_window_always_has_one_slot_per_hour(tmp_path):
    now = datetime.now().astimezone().replace(minute=30)
    state = _write(tmp_path, [_row(now)])
    w = recent_window(state, hours=6, now=now)
    assert len(w["labels"]) == 6
    assert len(w["totals_by_hour"]) == 6
    # An hour with nothing in it is a zero, not a missing point: a chart
    # with a gap in it should show the gap.
    assert w["totals_by_hour"].count(0) == 5


def test_anything_older_than_the_window_is_left_out(tmp_path):
    now = datetime.now().astimezone().replace(minute=30)
    state = _write(tmp_path, [
        _row(now),
        _row(now - timedelta(hours=3)),
        _row(now - timedelta(hours=50)),   # outside a 24h window
    ])
    w = recent_window(state, now=now)
    assert w["requests"] == 2


def test_providers_come_back_busiest_first(tmp_path):
    now = datetime.now().astimezone().replace(minute=30)
    rows = [_row(now, provider="groq") for _ in range(3)]
    rows += [_row(now, provider="cerebras") for _ in range(7)]
    w = recent_window(_write(tmp_path, rows), now=now)
    assert [p["name"] for p in w["providers"]] == ["cerebras", "groq"]
    assert w["providers"][0]["requests"] == 7


def test_failures_are_counted_but_never_timed(tmp_path):
    now = datetime.now().astimezone().replace(minute=30)
    rows = [
        _row(now, latency=100),
        _row(now, status="rate_limited", latency=99999),
    ]
    w = recent_window(_write(tmp_path, rows), now=now)
    assert w["requests"] == 2 and w["failed"] == 1
    assert w["answered_rate"] == 0.5
    assert w["fails_by_hour"][-1] == 1
    # A request that failed has no meaningful latency - letting its number
    # into the percentiles would make a broken provider look slow instead.
    assert w["latency"]["p50"] == 100


def test_an_hour_is_graded_by_what_happened_in_it(tmp_path):
    now = datetime.now().astimezone().replace(minute=30)
    rows = [
        _row(now - timedelta(hours=2), status="ok"),
        _row(now - timedelta(hours=1), status="ok"),
        _row(now - timedelta(hours=1), status="rate_limited"),
        _row(now, status="server_error"),
    ]
    w = recent_window(_write(tmp_path, rows), hours=3, now=now)
    assert w["providers"][0]["states"] == ["ok", "part", "bad"]


def test_a_provider_with_no_traffic_this_hour_reads_as_none(tmp_path):
    now = datetime.now().astimezone().replace(minute=30)
    w = recent_window(_write(tmp_path, [_row(now)]), hours=3, now=now)
    assert w["providers"][0]["states"] == ["none", "none", "ok"]


def test_buckets_and_models_are_split_out(tmp_path):
    now = datetime.now().astimezone().replace(minute=30)
    rows = [
        _row(now, tier="quick", model="a"),
        _row(now, tier="quick", model="a"),
        _row(now, tier="deep", model="b"),
    ]
    w = recent_window(_write(tmp_path, rows), now=now)
    assert {b["bucket"]: b["requests"] for b in w["buckets"]} == {"quick": 2, "deep": 1}
    assert w["latency"]["per_model"][0]["model"] == "groq/a"
    assert w["latency"]["per_model"][0]["requests"] == 2


def test_a_malformed_row_is_skipped_not_fatal(tmp_path):
    now = datetime.now().astimezone().replace(minute=30)
    good = _row(now)
    bad = dict(good, timestamp="not-a-date")
    worse = dict(good, latency_ms="")
    w = recent_window(_write(tmp_path, [good, bad, worse]), now=now)
    assert w["requests"] == 2
