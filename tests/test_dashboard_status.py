"""The Status page: one list replacing What's broken and Error brain
(PLAN-V2.3.md Session 8, grill-decisions.md §3-§4)."""
import json

import pytest
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter import overrides as ov
from flexrouter.app import create_app
from flexrouter.decider import ErrorVerdict


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def _router():
    return app_module.get_router()


MODEL = ("groq", "llama-3.1-8b-instant")


def test_the_old_pages_redirect_here(client):
    for old in ("/broken", "/brain"):
        r = client.get(old, follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == "/status"


def test_the_menu_has_one_status_entry(client):
    body = client.get("/status").text
    assert 'href="/status"' in body
    assert 'href="/broken"' not in body and 'href="/brain"' not in body


def test_nothing_wrong_says_so_and_folds_ready_away(client):
    body = client.get("/status").text
    assert "Nothing needs you right now." in body
    assert 'data-group="ready"' in body and "data-quiet" in body
    assert "st:groq/llama-3.1-8b-instant" in body


def test_a_did_you_mean_row_has_one_use_button(client):
    _router()._status.set_needs_you(*MODEL, "Groq doesn't know this name. Did you mean llama-3.1-8b?",
                                    kind="did_you_mean", action="use:llama-3.1-8b")
    body = client.get("/status").text
    assert "1 thing needs you" in body
    assert "Use llama-3.1-8b" in body
    assert 'data-st-action="/status/use/groq/llama-3.1-8b-instant"' in body


def test_use_files_the_suggestion_and_turns_the_old_one_off(client):
    _router()._status.set_needs_you(*MODEL, "wrong name", kind="did_you_mean",
                                    action="use:llama-3.1-8b")
    d = client.post("/status/use/groq/llama-3.1-8b-instant").json()
    assert d == {"ok": True, "status": "ready", "message": "Using llama-3.1-8b"}
    data = ov.load_overrides()
    assert data["models"]["groq/llama-3.1-8b-instant"]["enabled"] is False
    assert any(m["model"] == "llama-3.1-8b" for ms in data["new_models"].values() for m in ms)


def test_retry_clears_the_status(client):
    _router()._status.set_needs_you(*MODEL, "balance empty", kind="balance_empty", action="retry")
    assert client.post("/status/retry/groq/llama-3.1-8b-instant").json()["status"] == "ready"
    assert _router()._status.get(*MODEL).value == "ready"


def test_remove_turns_it_off_and_turn_on_brings_it_back(client):
    assert client.post("/status/remove/groq/llama-3.1-8b-instant").json()["status"] == "off"
    body = client.get("/status").text
    assert "You turned it off." in body and "Turn on" in body
    client.post("/status/turn_on/groq/llama-3.1-8b-instant")
    assert "You turned it off." not in client.get("/status").text


def test_a_busy_row_has_a_countdown_and_no_button(client):
    _router()._status.set_busy(*MODEL, 42, "Too many requests")
    body = client.get("/status").text
    assert "Busy, sorting itself out" in body
    assert "data-countdown" in body and "back in" in body
    assert 'data-st-action="/status/' not in body


def test_a_down_provider_links_to_its_page(client):
    _router()._status.set_provider_needs_you("groq", "key rejected")
    body = client.get("/status").text
    assert "key rejected" in body and 'href="/providers/groq"' in body


def test_a_row_opens_onto_the_whole_provider_response(client):
    _router()._status.set_needs_you(*MODEL, "Not on your Groq plan.", kind="not_on_plan",
                                    action="retry", detail='{"error": "END-OF-BODY"}')
    body = client.get("/status").text
    assert "What the provider said" in body and "END-OF-BODY" in body


class _Jev:
    configured = True

    def __init__(self, verdict):
        self.verdict = verdict

    def classify_error(self, text, status):
        return self.verdict

    def describe_model(self, *a, **kw):
        return {}


UNSURE = ErrorVerdict("their_end_temporary", "classifier", 0.3, insight={
    "ok": True, "transport": "decisions", "model": "~typesafe/jev-latest", "latency_ms": 454,
    "cost": 2.1e-05, "probabilities": {"their_end_temporary": 0.3, "bad_key": 0.25}})


def _learn(verdict):
    brain = _router()._error_brain
    brain._decider = _Jev(verdict)
    brain.classify("a novel failure nobody has seen", 418, provider="mistral",
                   model="labs-1", trace_id="req_abc123",
                   body=json.dumps({"error": "teapot END-OF-BODY"}))


def test_an_unsure_error_is_asked_once_with_the_detail_behind_a_click(client):
    _learn(UNSURE)
    body = client.get("/status").text
    assert "Not sure" in body and "mistral/labs-1" in body
    assert 'action="/brain/' in body and "Tell it" in body
    assert 'href="/requests?id=req_abc123"' in body
    assert "30% Their end, temporary" in body


def test_classifier_telemetry_is_one_footer_line(client):
    _learn(UNSURE)
    body = client.get("/status").text
    assert "Error classifier: ~typesafe/jev-latest" in body and "454 ms" in body


def test_without_a_classifier_the_footer_says_rules_only(client):
    assert "Error classifier: off, built-in rules only." in client.get("/status").text


def test_a_correction_comes_back_to_status(client):
    _learn(UNSURE)
    fp = next(iter(_router()._error_brain._entries))
    r = client.post(f"/brain/{fp}/verdict", data={"verdict": "bad_key"}, follow_redirects=False)
    assert r.headers["location"].startswith("/status?ok=1")


def test_the_page_polls_itself(client):
    assert 'hx-get="/status?fragment=1"' in client.get("/status").text


def test_a_reason_is_escaped_not_injected(client):
    _router()._status.set_needs_you(*MODEL, "<script>bad</script>", kind="gone", action="remove")
    body = client.get("/status").text
    assert "<script>bad</script>" not in body


def test_the_menu_badge_counts_what_needs_you(client):
    _router()._status.set_provider_needs_you("groq", "key rejected")
    assert 'class="nav-badge"' in client.get("/models_catalog").text


def test_no_badge_when_nothing_needs_you(client):
    assert 'class="nav-badge"' not in client.get("/models_catalog").text
