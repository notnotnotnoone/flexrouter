# tests/test_dashboard_overview.py
"""The Overview after the redesign.

The page is a composition rather than a list, so these check the things a
composition can get wrong: an empty install that has never served a
request, a window full of traffic, and the fold that stops a seventh
provider reusing a sixth provider's colour.
"""
import csv
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from flexrouter import app as app_module
from flexrouter.app import create_app
from flexrouter.dashboard.pages import _chart_series

HEADERS = ["timestamp", "tier", "provider", "model", "prompt_tokens",
           "completion_tokens", "cost_usd", "latency_ms", "status",
           "request_id"]


def _seed(state_dir, rows):
    path = state_dir / "audit.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(rows)


def _row(minutes_ago, status="ok", latency=200, tier="low", provider="groq"):
    when = datetime.now().astimezone() - timedelta(minutes=minutes_ago)
    return {
        "timestamp": when.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "tier": tier, "provider": provider, "model": "llama-3.1-8b-instant",
        "prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 0.0,
        "latency_ms": latency, "status": status, "request_id": "",
    }


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


@pytest.fixture
def state_dir():
    """Where the running app keeps its files. Ask for it after `client`, so
    the app - and therefore the router this reads - already exists."""
    from pathlib import Path
    return Path(app_module.get_router()._cfg.state_dir)


# ── a fresh install ────────────────────────────────────────────────────

def test_an_overview_with_no_traffic_still_renders(client):
    body = client.get("/").text
    assert client.get("/").status_code == 200
    # It says so plainly rather than drawing an empty chart.
    assert "nothing has come through" in body.lower()
    # And it still shows what is configured, which is the useful half.
    assert "Providers" in body and "Buckets" in body


def test_an_empty_overview_draws_no_chart(client):
    assert 'class="chart"' not in client.get("/").text


# ── with traffic ───────────────────────────────────────────────────────

def test_traffic_puts_a_chart_on_the_page(client, state_dir):
    _seed(state_dir, [_row(m) for m in (5, 65, 125, 185)])
    body = client.get("/").text
    assert 'class="chart"' in body
    assert "<svg" in body
    # The legend names the series, so colour is never the only identity.
    assert 'class="legend-item"' in body


def test_the_chart_has_a_table_beside_it(client, state_dir):
    _seed(state_dir, [_row(5)])
    body = client.get("/").text
    assert "View as a table" in body
    assert 'class="table-view"' in body


def test_the_headline_counts_what_actually_came_through(client, state_dir):
    _seed(state_dir, [_row(m) for m in range(1, 8)])
    body = client.get("/").text
    assert 'data-stat="requests"' in body
    assert 'data-value="7"' in body


def test_a_failure_shows_up_in_the_answered_rate(client, state_dir):
    _seed(state_dir, [_row(1), _row(2), _row(3), _row(4, status="rate_limited")])
    body = client.get("/").text
    assert "75%" in body


def test_the_provider_row_carries_its_own_traffic(client, state_dir):
    _seed(state_dir, [_row(m) for m in range(1, 6)])
    body = client.get("/").text
    assert 'class="matrix"' in body
    assert 'class="spark"' in body          # its shape over the window
    assert 'class="seg seg-ok"' in body     # and how each hour went


def test_the_feed_shows_the_last_requests(client, state_dir):
    # traces.jsonl, not audit.csv - a different file for a different job.
    import json
    (state_dir).mkdir(parents=True, exist_ok=True)
    (state_dir / "traces.jsonl").write_text(json.dumps({
        "id": "t1",
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "asked": {"bucket": "low"}, "ok": True,
        "answered_by": {"provider": "groq", "model": "llama-3.1-8b-instant"},
        "tokens": {"in": 10, "out": 5}, "ms_total": 321,
        "skipped": [{"model": "a"}, {"model": "b"}],
    }) + "\n", encoding="utf-8")
    body = client.get("/").text
    assert "321 ms" in body
    assert "2 hops" in body


def test_the_page_never_shows_two_different_clocks(client, state_dir):
    """The chart buckets by local hour; the feed must agree with it."""
    import json
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    local = datetime.fromisoformat(stamp).astimezone().strftime("%H:%M:%S")
    (state_dir).mkdir(parents=True, exist_ok=True)
    (state_dir / "traces.jsonl").write_text(json.dumps({
        "id": "t1", "at": stamp, "asked": {"bucket": "low"}, "ok": True,
        "answered_by": {"provider": "groq", "model": "m"},
        "tokens": {"in": 1, "out": 1}, "ms_total": 10, "skipped": [],
    }) + "\n", encoding="utf-8")
    assert local in client.get("/").text


# ── the colour fold ────────────────────────────────────────────────────

def test_six_providers_each_keep_their_own_colour():
    window = {"hours": 2, "providers": [
        {"name": f"p{i}", "values": [1, 1], "requests": 2} for i in range(6)
    ]}
    series = _chart_series(window)
    assert len(series) == 6
    assert [s["name"] for s in series] == [f"p{i}" for i in range(6)]


def test_a_seventh_provider_folds_instead_of_reusing_a_colour():
    # Reusing slot one would quietly claim two providers were the same one.
    window = {"hours": 2, "providers": [
        {"name": f"p{i}", "values": [1, 2], "requests": 3} for i in range(9)
    ]}
    series = _chart_series(window)
    assert len(series) == 6
    assert series[-1]["name"] == "4 others"
    assert series[-1]["requests"] == 12          # the four folded providers
    assert series[-1]["values"] == [4, 8]        # summed hour by hour


def test_the_fold_keeps_every_request(tmp_path):
    window = {"hours": 3, "providers": [
        {"name": f"p{i}", "values": [i, i, i], "requests": i * 3}
        for i in range(1, 11)
    ]}
    before = sum(p["requests"] for p in window["providers"])
    after = sum(s["requests"] for s in _chart_series(window))
    assert before == after


# ── time ranges ────────────────────────────────────────────────────────

def test_every_range_renders(client, state_dir):
    _seed(state_dir, [_row(m) for m in (5, 600, 5000, 40000)])
    for key in ("24h", "7d", "30d", "all"):
        r = client.get(f"/?range={key}")
        assert r.status_code == 200, key
        assert 'class="ov"' in r.text, key


def test_the_range_is_in_the_url_so_it_survives_a_reload(client):
    body = client.get("/?range=7d").text
    assert 'href="/?range=7d"' in body
    assert 'aria-current="true"' in body


def test_an_unknown_range_falls_back_instead_of_raising(client):
    r = client.get("/?range=nonsense")
    assert r.status_code == 200
    assert 'data-range="24h"' in r.text


def test_a_longer_range_uses_coarser_buckets(client, state_dir):
    # 720 points on an 880-wide chart is mush, so a month is bucketed by day.
    _seed(state_dir, [_row(5)])
    day = client.get("/?range=30d").text
    assert "each block is one day" in day
    hour = client.get("/?range=24h").text
    assert "each block is one hour" in hour


def test_a_wider_range_sees_older_traffic(client, state_dir):
    _seed(state_dir, [_row(60 * 24 * 3)])      # three days ago
    idle = "Nothing has come through in the last"   # the verdict's wording
    assert idle in client.get("/?range=24h").text
    assert idle not in client.get("/?range=7d").text


# ── live update ────────────────────────────────────────────────────────

def test_the_fragment_is_the_live_block_and_nothing_else(client, state_dir):
    _seed(state_dir, [_row(5)])
    frag = client.get("/?fragment=1").text
    assert "<!doctype html>" not in frag.lower()
    assert "<nav" not in frag          # no menu, no shell
    assert 'data-stat="requests"' in frag


def test_the_fragment_is_built_by_the_same_code_as_the_page(client, state_dir):
    """No second rendering path: the live view cannot drift from the served
    one, because it is literally the same HTML."""
    _seed(state_dir, [_row(m) for m in range(1, 5)])
    page_body = client.get("/").text
    frag = client.get("/?fragment=1").text
    assert frag in page_body


def test_the_fragment_honours_the_range(client, state_dir):
    _seed(state_dir, [_row(60 * 24 * 3)])
    frag = client.get("/?range=7d&fragment=1").text
    assert "Nothing has come through in the last" not in frag


def test_the_page_tells_the_poller_what_to_ask_for(client):
    body = client.get("/").text
    assert 'id="ov"' in body
    assert 'data-range="24h"' in body
    assert 'data-poll=' in body


def test_the_script_is_served_and_deferred(client):
    assert client.get("/static/app.js").status_code == 200
    assert "app.js?v=" in client.get("/").text
    assert client.get("/wire.js").status_code == 404


# ── failovers and money on the page ────────────────────────────────────

def test_a_failover_is_shown_now_that_it_can_be_counted(client, state_dir):
    _seed(state_dir, [
        dict(_row(3, status="rate_limited"), request_id="r1"),
        dict(_row(2), request_id="r1"),
    ])
    body = client.get("/").text
    assert "Failovers" in body
    assert "changed model mid-flight" in body


def test_unpriced_traffic_says_so_rather_than_claiming_it_was_free(client, state_dir):
    # "$0.00" would be a claim about money nobody has entered yet.
    _seed(state_dir, [_row(2)])
    body = client.get("/").text
    assert "not priced" in body
    assert "$0.00" not in body


def test_the_overview_polls_with_a_morph_not_a_replace(client):
    body = client.get("/").text
    assert 'hx-swap="morph:innerHTML"' in body
    assert 'hx-trigger="every 10s' in body
    assert "data-live" in body


def test_the_poll_interval_follows_the_preference(client):
    from flexrouter import home
    (home.home_dir() / "dashboard.json").write_text('{"refresh_seconds": 30}')
    assert 'hx-trigger="every 30s' in client.get("/").text


def test_five_stats_in_the_approved_order(client):
    body = client.get("/").text
    keys = ["requests", "answered", "failovers", "median", "spent"]
    positions = [body.find(f'data-stat="{k}"') for k in keys]
    assert all(p != -1 for p in positions)
    assert positions == sorted(positions)


def test_buckets_are_drawn_as_block_meters(client, state_dir):
    _seed(state_dir, [_row(1)])
    assert 'class="meter"' in client.get("/").text


def test_provider_state_is_a_word_not_only_a_colour(client, state_dir):
    _seed(state_dir, [_row(1)])
    body = client.get("/").text
    assert any(w in body for w in ("● OK", "◆ ATTENTION", "▲ BROKEN"))
