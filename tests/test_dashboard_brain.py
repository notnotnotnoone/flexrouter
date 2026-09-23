"""The Error brain page shows everything flexrouter knows about an error,
and what the classifier (JEV) is doing."""
import json

import pytest
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter.app import create_app
from flexrouter.decider import ErrorVerdict


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


class _Jev:
    configured = True

    def __init__(self, verdict):
        self.verdict = verdict

    def classify_error(self, text, status):
        return self.verdict

    def describe_model(self, *a, **kw):
        return {}


ANSWERED = ErrorVerdict("model_gone", "classifier", 0.95, insight={
    "ok": True, "transport": "decisions", "model": "~typesafe/jev-latest",
    "served_model": "typesafe/jev-1.13-20260917", "latency_ms": 454, "cost": 2.1e-05,
    "raw_confidence": 0.99, "generation_id": "gen-dec-1",
    "probabilities": {"model_gone": 0.91, "bad_key": 0.06, "bad_request": 0.03}})

FAILED = ErrorVerdict("unknown", "classifier", 0.0, insight={
    "ok": False, "transport": "chat/completions", "model": "~typesafe/jev-latest",
    "http_status": 400, "latency_ms": 118,
    "error": '{"error":{"message":"~typesafe/jev-latest is a decisions model and cannot be used '
             'with the chat/completions endpoint. Use the /api/alpha/decisions endpoint instead.","code":400}}'})

BODY = json.dumps({"error": {"message": "Model labs-leanstral-1-5 is a Labs model. " + "more " * 150
                             + "END-OF-BODY", "type": "labs_not_enabled", "code": "1913"}})


def _learn(verdict, text="403 from mistral/labs-leanstral-1-5: a Labs model", status=403, body=BODY):
    brain = app_module.get_router()._error_brain
    brain._decider = _Jev(verdict)
    brain.classify(text, status, provider="mistral", model="labs-leanstral-1-5",
                   trace_id="req_abc123", body=body)


def test_a_card_shows_the_whole_provider_response(client):
    _learn(ANSWERED)
    page = client.get("/brain").text
    assert "END-OF-BODY" in page
    assert "labs_not_enabled" in page
    assert "403" in page


def test_a_card_says_where_it_happened_and_links_the_request(client):
    _learn(ANSWERED)
    page = client.get("/brain").text
    assert "mistral/labs-leanstral-1-5" in page
    assert 'href="/requests?id=req_abc123"' in page


def test_a_card_shows_every_probability_jev_gave(client):
    _learn(ANSWERED)
    page = client.get("/brain").text
    assert "91%" in page and "6%" in page and "3%" in page
    assert "overturned" in page.lower()


def test_the_classifier_panel_reports_its_health_and_recent_calls(client):
    _learn(ANSWERED)
    page = client.get("/brain").text
    assert "~typesafe/jev-latest" in page
    assert "454" in page  # latency
    assert "$0.000021" in page  # cost


def test_a_failing_classifier_shows_its_whole_error_on_the_page(client):
    _learn(FAILED, text="some novel provider failure", status=None)
    page = client.get("/brain").text
    assert "cannot be used with the chat/completions endpoint" in page
    assert "/api/alpha/decisions" in page
    assert "HTTP 400" in page


def test_with_no_classifier_the_page_says_rules_only(client):
    page = client.get("/brain").text
    assert "no classifier" in page.lower()
