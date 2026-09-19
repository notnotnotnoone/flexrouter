"""Checking a key must say what went wrong, in words.

The old `onboard.discover_models` returned [] for every failure, so "your key
is rejected", "the address is wrong" and "this provider has no free models"
were indistinguishable. That is the whole reason key entry was painful.
"""
import asyncio

import httpx
import pytest
import respx

from flexrouter.probe import ProbeResult, probe_key, stale_models

BASE = "https://api.example.com/v1"
MODELS_URL = f"{BASE}/models"


def run(coro):
    return asyncio.run(coro)


# --- success ----------------------------------------------------------------

@respx.mock
def test_good_key_lists_models():
    respx.get(MODELS_URL).mock(return_value=httpx.Response(200, json={
        "data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}))
    result = run(probe_key(BASE, "sk-good"))
    assert result.ok is True
    assert result.models == ["gpt-4o", "gpt-4o-mini"]
    assert result.error is None
    assert result.as_dict()["model_count"] == 2


@respx.mock
def test_key_is_sent_as_a_bearer_token():
    route = respx.get(MODELS_URL).mock(
        return_value=httpx.Response(200, json={"data": []}))
    run(probe_key(BASE, "sk-secret"))
    assert route.calls.last.request.headers["Authorization"] == "Bearer sk-secret"


@respx.mock
def test_no_key_sends_no_auth_header():
    # Local providers like Ollama take no credential at all.
    route = respx.get(MODELS_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"id": "llama3"}]}))
    result = run(probe_key(BASE, None))
    assert result.ok is True
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
def test_handles_a_bare_list_response():
    # Not every provider wraps its list in {"data": ...}.
    respx.get(MODELS_URL).mock(
        return_value=httpx.Response(200, json=[{"id": "b"}, {"id": "a"}]))
    assert run(probe_key(BASE, "k")).models == ["a", "b"]


@respx.mock
def test_duplicate_ids_are_collapsed():
    respx.get(MODELS_URL).mock(return_value=httpx.Response(
        200, json={"data": [{"id": "x"}, {"id": "x"}, {"id": "y"}]}))
    assert run(probe_key(BASE, "k")).models == ["x", "y"]


# --- failures that must be told apart ---------------------------------------

@respx.mock
def test_rejected_key_says_so_and_suggests_a_fix():
    respx.get(MODELS_URL).mock(return_value=httpx.Response(
        401, json={"error": {"message": "Invalid API Key"}}))
    result = run(probe_key(BASE, "sk-bad"))
    assert result.ok is False
    assert result.status_code == 401
    assert "Invalid API Key" in result.error
    assert "new one" in result.error       # the actionable hint


@respx.mock
def test_billing_failure_points_at_the_billing_page():
    respx.get(MODELS_URL).mock(return_value=httpx.Response(
        402, json={"message": "Payment required to access this resource."}))
    result = run(probe_key(BASE, "k"))
    assert result.ok is False
    assert "Payment required" in result.error
    assert "billing" in result.error


@respx.mock
def test_wrong_address_is_not_reported_as_a_bad_key():
    respx.get(MODELS_URL).mock(return_value=httpx.Response(404, text="Not Found"))
    result = run(probe_key(BASE, "k"))
    assert result.ok is False
    assert "base URL" in result.error


@respx.mock
def test_rate_limited_key_is_not_called_invalid():
    # Important: a 429 must not make someone throw away a working key.
    respx.get(MODELS_URL).mock(return_value=httpx.Response(429, text=""))
    result = run(probe_key(BASE, "k"))
    assert result.ok is False
    assert "still be fine" in result.error


@respx.mock
def test_unreachable_host_explains_itself():
    respx.get(MODELS_URL).mock(side_effect=httpx.ConnectError("nope"))
    result = run(probe_key(BASE, "k"))
    assert result.ok is False
    assert BASE in result.error
    assert result.status_code is None


@respx.mock
def test_timeout_is_reported_as_a_timeout():
    respx.get(MODELS_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    result = run(probe_key(BASE, "k", timeout=2.0))
    assert result.ok is False
    assert "2s" in result.error


@respx.mock
def test_html_page_instead_of_json_blames_the_address():
    respx.get(MODELS_URL).mock(return_value=httpx.Response(
        200, text="<html>hello</html>", headers={"Content-Type": "text/html"}))
    result = run(probe_key(BASE, "k"))
    assert result.ok is False
    assert "base URL" in result.error


# --- stale model detection ---------------------------------------------------

def test_stale_models_are_the_ones_the_provider_no_longer_lists():
    configured = ["gemini-2.0-flash", "gemini-3.6-flash", "gemini-2.5-flash"]
    reachable = ["gemini-3.6-flash", "gemini-3.6-pro"]
    assert stale_models(configured, reachable) == [
        "gemini-2.0-flash", "gemini-2.5-flash"]


def test_nothing_is_stale_when_everything_is_reachable():
    assert stale_models(["a", "b"], ["a", "b", "c"]) == []


def test_an_empty_reachable_list_accuses_nothing():
    # A probe that failed must not report every configured model as dead.
    assert stale_models(["a", "b"], []) == []


def test_probe_result_serializes_for_the_dashboard():
    payload = ProbeResult(ok=True, models=["a"], status_code=200,
                          latency_ms=42).as_dict()
    assert payload == {"ok": True, "models": ["a"], "model_count": 1,
                       "error": None, "status_code": 200, "latency_ms": 42}
