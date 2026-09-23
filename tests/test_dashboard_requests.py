"""The Requests page: the log, its filters, and one request's journey."""
import json

import pytest
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter.app import create_app
from flexrouter.dashboard import facts, ui


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def _trace(**over):
    t = {
        "id": "req_ok", "at": "2026-09-21T10:00:00.000Z",
        "asked": {"bucket": "low", "stream": False},
        "skipped": [], "attempts": [],
        "answered_by": {"provider": "groq", "model": "llama-3.1-8b-instant"},
        "tokens": {"in": 10, "out": 5}, "ms_total": 123, "ok": True,
    }
    t.update(over)
    return t


def _write(*traces):
    import os
    router = app_module.get_router()
    os.makedirs(router._cfg.state_dir, exist_ok=True)
    with open(router._cfg.state_dir + "/traces.jsonl", "a", encoding="utf-8") as f:
        for t in traces:
            f.write(json.dumps(t) + "\n")


FAILOVER = _trace(
    id="req_fo", bucket=None,
    skipped=[{"provider": "cerebras", "model": "qwen", "reason": "cooling_down",
              "detail": "key resting for 40s"}],
    attempts=[{"n": 1, "provider": "mistral", "model": "large", "status": 429,
               "provider_message": "Too many requests", "verdict": "rate_limited", "ms": 88}],
    answered_by={"provider": "groq", "model": "llama-3.3-70b"}, ms_total=640,
)
FAILED = _trace(id="req_bad", ok=False, answered_by=None,
                attempts=[{"n": 1, "provider": "groq", "model": "m", "status": 500,
                           "provider_message": "boom", "verdict": "their_end_temporary", "ms": 12}])


# ── the row facts ──────────────────────────────────────────────────────

def test_a_row_knows_it_was_a_failover(client):
    _write(_trace(), FAILOVER)
    rows = {r.id: r for r in facts.recent_requests(app_module.get_router())}
    assert rows["req_fo"].outcome == "failover"
    assert rows["req_ok"].outcome == "ok"


def test_a_failed_row_says_so(client):
    _write(FAILED)
    [row] = facts.recent_requests(app_module.get_router())
    assert row.outcome == "failed"


def test_the_journey_lists_skips_then_attempts_then_the_answer(client):
    _write(FAILOVER)
    j = facts.request_journey(app_module.get_router(), "req_fo")
    kinds = [step["kind"] for step in j["steps"]]
    assert kinds == ["skipped", "failed", "answered"]
    assert j["steps"][1]["status"] == 429
    assert j["steps"][2]["provider"] == "groq"


def test_an_unknown_journey_is_none(client):
    assert facts.request_journey(app_module.get_router(), "nope") is None


# ── the page ───────────────────────────────────────────────────────────

def test_rows_carry_a_status_block_in_words(client):
    _write(_trace(), FAILOVER, FAILED)
    body = client.get("/requests").text
    for word in ("OK", "FAILOVER", "FAILED"):
        assert f">{word}<" in body


def test_filters_narrow_the_rows(client):
    _write(_trace(), FAILOVER, FAILED)
    body = client.get("/requests?result=failover").text
    assert "req_fo" in body
    assert "req_bad" not in body and "req_ok" not in body


def test_search_matches_the_model(client):
    _write(_trace(), FAILOVER)
    body = client.get("/requests?q=llama-3.3").text
    assert "req_fo" in body and 'data-row="req:req_ok"' not in body


def test_opening_a_request_renders_its_journey_in_a_side_panel(client):
    _write(FAILOVER)
    body = client.get("/requests?id=req_fo").text
    assert 'class="sheet' in body and 'role="dialog"' in body
    assert "key resting for 40s" in body
    assert "Too many requests" in body


def test_the_journey_fragment_is_just_the_panel(client):
    _write(FAILOVER)
    r = client.get("/requests/req_fo/journey")
    assert r.status_code == 200
    assert "<nav" not in r.text and 'role="dialog"' in r.text


def test_an_unknown_journey_is_a_404(client):
    assert client.get("/requests/nope/journey").status_code == 404


def test_provider_text_in_a_journey_is_escaped(client):
    evil = dict(FAILOVER, attempts=[dict(FAILOVER["attempts"][0],
                                         provider_message="<script>x</script>")])
    _write(evil)
    body = client.get("/requests/req_fo/journey").text
    assert "<script>x</script>" not in body


def test_the_log_polls_for_new_rows(client):
    body = client.get("/requests").text
    assert 'hx-swap="morph:innerHTML"' in body and "data-live" in body


def test_sheet_helper_has_a_close_link():
    html = ui.sheet("Title", "<p>x</p>", close_href="/requests")
    assert 'href="/requests"' in html and 'aria-label="Close"' in html
