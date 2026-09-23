"""TypeSafe's Jev on OpenRouter: a decision model, not a chat model.

Seen live: every chat/completions call answered 400 "~typesafe/jev-latest is
a decisions model and cannot be used with the chat/completions endpoint. Use
the /api/alpha/decisions endpoint instead." The decider logged only "HTTP
400; no opinion", so it was silently off for every error it was asked about.
"""
import json
from types import SimpleNamespace

import httpx
import respx

from flexrouter.decider import DecisionsDecider, HttpDecider, VERDICTS, build_decider

URL = "https://openrouter.ai/api/alpha/decisions"

# Shape captured from a live call, 2026-09-23.
LIVE_REPLY = {
    "model": "typesafe/jev-1.13-20260917",
    "answers": {"verdict": {
        "type": "choice", "choice": "model_gone",
        "probabilities": {"unknown": 0, "needs_payment": 0, "their_end_temporary": 0,
                          "model_gone": 1, "too_fast": 0, "message_too_long": 0,
                          "bad_request": 0, "bad_key": 0},
        "confidence": 0.99}},
    "usage": {"input_tokens": 499, "output_tokens": 89, "cost": 2.0958e-05},
    "id": "gen-dec-1790202587-zOGgdhg4WYxHUzLtZ3RQ",
    "provider": "TypeSafe",
}


def _cfg(model="~typesafe/jev-latest"):
    return SimpleNamespace(enabled=True, base_url="https://openrouter.ai/api/v1", model=model,
                           timeout_seconds=3.0, confidence_ceiling=0.95)


def test_a_typesafe_model_gets_the_decisions_transport():
    assert isinstance(build_decider(_cfg(), "k"), DecisionsDecider)
    assert isinstance(build_decider(_cfg("typesafe/jev-1.13"), "k"), DecisionsDecider)
    assert isinstance(build_decider(_cfg("openai/gpt-4o-mini"), "k"), HttpDecider)


@respx.mock
def test_asks_one_choice_question_over_the_fixed_verdicts():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=LIVE_REPLY))
    DecisionsDecider("https://openrouter.ai/api/v1", "~typesafe/jev-latest", "secret-k") \
        .classify_error("403 from mistral/labs-x: a Labs model", 403)

    sent = json.loads(route.calls.last.request.content)
    assert route.calls.last.request.headers["authorization"] == "Bearer secret-k"
    assert sent["model"] == "~typesafe/jev-latest"
    assert sent["state"]["http_status"] == 403
    assert "Labs model" in sent["state"]["error_text"]
    question = sent["questions"]["verdict"]
    assert question["type"] == "choice"
    assert set(question["criteria"]) == set(VERDICTS)


@respx.mock
def test_keeps_everything_jev_said():
    respx.post(URL).mock(return_value=httpx.Response(200, json=LIVE_REPLY))
    v = DecisionsDecider("https://openrouter.ai/api/v1", "~typesafe/jev-latest", "k") \
        .classify_error("403 ...", 403)

    assert (v.verdict, v.source, v.confidence) == ("model_gone", "classifier", 0.95)  # ceiling
    assert v.insight["ok"] is True
    assert v.insight["served_model"] == "typesafe/jev-1.13-20260917"
    assert v.insight["raw_confidence"] == 0.99
    assert v.insight["probabilities"]["model_gone"] == 1
    assert v.insight["cost"] == 2.0958e-05
    assert v.insight["generation_id"] == LIVE_REPLY["id"]
    assert v.insight["latency_ms"] >= 0


@respx.mock
def test_a_failure_keeps_the_whole_reply_body():
    body = {"error": {"message": "x" * 900 + " the actual reason at the end", "code": 400}}
    respx.post(URL).mock(return_value=httpx.Response(400, json=body))
    v = DecisionsDecider("https://openrouter.ai/api/v1", "~typesafe/jev-latest", "k") \
        .classify_error("boom", 500)

    assert (v.verdict, v.confidence) == ("unknown", 0.0)
    assert v.insight["ok"] is False and v.insight["http_status"] == 400
    assert "the actual reason at the end" in v.insight["error"]


@respx.mock
def test_the_chat_decider_also_reports_why_it_had_no_opinion():
    msg = ("~typesafe/jev-latest is a decisions model and cannot be used with the "
           "chat/completions endpoint. Use the /api/alpha/decisions endpoint instead.")
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": msg, "code": 400}}))
    v = HttpDecider("https://openrouter.ai/api/v1", "some/model", "k").classify_error("boom", 500)

    assert v.insight["ok"] is False and v.insight["http_status"] == 400
    assert "/api/alpha/decisions" in v.insight["error"]
