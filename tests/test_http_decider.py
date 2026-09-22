"""The real classifier behind the Decider protocol.

Everything here pins behaviour that keeps a misconfigured or misbehaving
classifier from ever breaking routing: a bad reply degrades to exactly what
NullDecider returns, and a confident-sounding model can never outrank a rule.
"""
import httpx
import pytest
import respx

from flexrouter.decider import HttpDecider, VERDICTS

URL = "https://classifier.example/v1/chat/completions"


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _decider(**kw):
    return HttpDecider(base_url="https://classifier.example/v1",
                       model="some/classifier-1", api_key="k", **kw)


@respx.mock
def test_returns_the_verdict_the_model_chose():
    respx.post(URL).mock(return_value=_reply('{"verdict": "needs_payment", "confidence": 0.9}'))
    v = _decider().classify_error("insufficient credits", 429)
    assert v.verdict == "needs_payment"
    assert v.source == "classifier"
    assert v.confidence == 0.9


@respx.mock
def test_confidence_is_capped_below_a_rules_certainty():
    """A model's self-reported confidence is weakly calibrated. Letting it
    claim 1.0 would put a guess on equal footing with a real rule and let it
    sail past the review threshold unexamined."""
    respx.post(URL).mock(return_value=_reply('{"verdict": "too_fast", "confidence": 1.0}'))
    assert _decider().classify_error("whatever", None).confidence == 0.95


@respx.mock
def test_a_verdict_outside_the_fixed_set_is_not_trusted():
    respx.post(URL).mock(return_value=_reply('{"verdict": "the_vibes_are_off", "confidence": 0.99}'))
    v = _decider().classify_error("whatever", None)
    assert v.verdict == "unknown"
    assert v.confidence == 0.0


@respx.mock
def test_unparseable_reply_degrades_instead_of_raising():
    respx.post(URL).mock(return_value=_reply("I think this is a rate limit, probably!"))
    v = _decider().classify_error("whatever", None)
    assert v.verdict == "unknown"
    assert v.confidence == 0.0


@respx.mock
def test_a_failing_endpoint_degrades_instead_of_raising():
    respx.post(URL).mock(return_value=httpx.Response(500, text="down"))
    v = _decider().classify_error("whatever", None)
    assert v.verdict == "unknown"
    assert v.confidence == 0.0


@respx.mock
def test_a_network_error_degrades_instead_of_raising():
    respx.post(URL).mock(side_effect=httpx.ConnectError("no route"))
    v = _decider().classify_error("whatever", None)
    assert v.verdict == "unknown"
    assert v.confidence == 0.0


@respx.mock
def test_sends_the_key_and_constrains_the_reply_to_the_fixed_verdict_set():
    route = respx.post(URL).mock(return_value=_reply('{"verdict": "unknown", "confidence": 0.1}'))
    _decider().classify_error("some text", 418)
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer k"
    body = req.read().decode()
    for verdict in VERDICTS:
        assert verdict in body, f"{verdict} must be offered to the model"


def test_an_http_decider_counts_as_configured():
    assert _decider().configured is True
