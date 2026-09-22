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
           "completion_tokens", "cost_usd", "latency_ms", "status"]


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
        "latency_ms": latency, "status": status,
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
    assert 'class="tally-big">7<' in body


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
