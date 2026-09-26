"""What a real provider failure does to a model's status, end to end.

grill-decisions.md §3: broken things (404, 402, 403, a rejected key) are
Needs you at once and never clear on a timer; busy things (429, 5xx) are
Busy and clear on their own. Before v2.3 a deleted model was retried every
24h forever - the real audit log showed 239 penalties against 235
recoveries with no progress.
"""
import asyncio
import csv
import time

import httpx
import pytest
import respx

from flexrouter import LocalRouter, RouterBusy
from flexrouter.client import ProviderError
from flexrouter import status as st

CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.1-8b-instant"


# --- ProviderError classification -------------------------------------------

@pytest.mark.parametrize("status,permanent", [
    (404, True),    # model deleted
    (410, True),    # model retired
    (500, False),   # provider having a bad day
    (503, False),
    (400, False),   # ambiguous — could be our malformed request, don't sideline
])
def test_permanence_classification(status, permanent):
    assert ProviderError("boom", status_code=status).is_permanent is permanent


def test_error_without_status_is_not_permanent():
    # Connection resets and timeouts arrive with no status at all.
    assert ProviderError("connection reset").is_permanent is False


# --- End-to-end through the router ------------------------------------------

def _events(config_file):
    import yaml
    state = yaml.safe_load(config_file.read_text())["settings"]["state_dir"]
    from pathlib import Path
    p = Path(state) / "events.csv"
    if not p.exists():
        return []
    with p.open() as f:
        return list(csv.DictReader(f))


@respx.mock
def test_404_makes_the_model_need_you(config_file):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(
        404, json={"error": {"message": "model not found"}}))
    router = LocalRouter(str(config_file))
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)
    s = router._status.get("groq", MODEL)
    assert s.value == st.NEEDS_YOU and s.kind == "gone" and s.until is None


@respx.mock
def test_500_is_only_busy(config_file):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500, text="upstream exploded"))
    router = LocalRouter(str(config_file))
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)
    s = router._status.get("groq", MODEL)
    assert s.value == st.BUSY
    assert s.until is not None and s.until - time.time() <= 61


@respx.mock
def test_provider_message_is_recorded_not_discarded(config_file):
    # The whole point: 239 failures in the real log carried zero explanation.
    respx.post(CHAT_URL).mock(return_value=httpx.Response(
        500, text="upstream exploded spectacularly"))
    router = LocalRouter(str(config_file))
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)

    errors = [e for e in _events(config_file) if e["event_type"] == "server_error"]
    assert errors, "no server_error event recorded"
    assert all(e["detail"].strip() for e in errors), \
        f"server_error logged with empty detail: {errors}"
    assert any("500" in e["detail"] for e in errors)


@respx.mock
def test_rate_limit_message_is_recorded(config_file):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(429, json={"error": "slow down"}))
    router = LocalRouter(str(config_file))
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)

    limited = [e for e in _events(config_file) if e["event_type"] == "rate_limited"]
    assert limited, "no rate_limited event recorded"
    assert all(e["detail"].strip() for e in limited), \
        f"rate_limited logged with empty detail: {limited}"


@respx.mock
def test_needs_you_event_is_emitted(config_file):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(404, text="no such model"))
    router = LocalRouter(str(config_file))
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)

    evs = _events(config_file)
    assert any(e["event_type"] == "needs_you" for e in evs), \
        f"expected a needs_you event, got {[e['event_type'] for e in evs]}"
    detail = " ".join(e["detail"] for e in evs if e["event_type"] == "server_error")
    assert "needs_you" in detail


# --- auth failures sideline the provider, not the request -------------------

TWO_PROVIDER_CONFIG = {
    "tiers": {
        "low": [
            {"provider": "groq", "model": "llama-3.1-8b-instant", "score": 99,
             "rpm": 60, "tpm": 60000, "context_window": 131072},
            {"provider": "cerebras", "model": "gpt-oss-120b", "score": 40,
             "rpm": 60, "tpm": 60000, "context_window": 131072},
        ]
    },
    "providers": {
        "groq": {"base_url": "https://api.groq.com/openai/v1",
                 "api_keys": [{"key": "dead-key"}]},
        "cerebras": {"base_url": "https://api.cerebras.ai/v1",
                     "api_keys": [{"key": "good-key"}]},
    },
    "settings": {"state_dir": "", "window_seconds": 60,
                 "penalty_base_seconds": 30, "penalty_max_seconds": 1800,
                 "session_ttl_minutes": 30, "dashboard_port": 7352,
                 "retry_policy": "balanced"},
}

OK_RESPONSE = {
    "choices": [{"message": {"role": "assistant", "content": "hello"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}


@pytest.fixture
def two_provider_config(tmp_path):
    import copy
    import yaml
    cfg = copy.deepcopy(TWO_PROVIDER_CONFIG)
    cfg["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    return p


@respx.mock
def test_auth_failure_rotates_to_a_healthy_provider(two_provider_config):
    """One dead key must not take down a tier that has a working provider.

    This is the reported "flexrouter is really broken": an expired Groq key
    aborted every request outright, even though other providers in the same
    tier answered fine.
    """
    respx.post(CHAT_URL).mock(return_value=httpx.Response(
        401, json={"error": {"message": "Invalid API Key"}}))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE))

    router = LocalRouter(str(two_provider_config))
    result = router.generate([{"role": "user", "content": "hi"}], tier="low")

    assert result["choices"][0]["message"]["content"] == "hello"
    # The only groq key was rejected, so the provider needs you...
    assert router._status.get("groq", "llama-3.1-8b-instant").value == st.NEEDS_YOU
    assert router._key_states.get("groq", router._cfg.providers["groq"].keys[0].id).status \
        == "needs_you"
    assert router._status.get("cerebras", "gpt-oss-120b").value == st.READY


@respx.mock
def test_auth_failure_is_recorded_with_its_reason(two_provider_config):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(401, json={"error": "nope"}))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE))

    router = LocalRouter(str(two_provider_config))
    router.generate([{"role": "user", "content": "hi"}], tier="low")

    import yaml
    from pathlib import Path
    state = yaml.safe_load(two_provider_config.read_text())["settings"]["state_dir"]
    with (Path(state) / "events.csv").open() as f:
        rows = list(csv.DictReader(f))
    auth = [r for r in rows if "rejected" in r["detail"].lower()]
    assert auth, f"auth failure not explained in events: {rows}"
    assert any("401" in r["detail"] for r in auth)


@respx.mock
def test_every_provider_dead_still_raises_a_clear_auth_error(two_provider_config):
    # When nothing is left, the caller should learn it's a credentials
    # problem — not a vague "all retries exhausted".
    respx.post(CHAT_URL).mock(return_value=httpx.Response(401, json={"error": "nope"}))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": "nope"}))

    from flexrouter.exceptions import RouterError
    router = LocalRouter(str(two_provider_config))
    with pytest.raises(RouterError, match="Auth failure"):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)


def test_402_is_permanent():
    # A billing problem doesn't resolve on a timer (seen live on cerebras),
    # so 402 is permanent. But it is not always account-wide: llm7 answers
    # 402 for its paid models while its free ones keep working.
    assert ProviderError("nope", status_code=402).is_permanent is True


@respx.mock
def test_payment_required_sidelines_the_model_and_rotates(two_provider_config):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(
        402, json={"message": "Payment required to access this resource."}))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE))

    router = LocalRouter(str(two_provider_config))
    result = router.generate([{"role": "user", "content": "hi"}], tier="low")

    assert result["choices"][0]["message"]["content"] == "hello"
    s = router._status.get("groq", "llama-3.1-8b-instant")
    assert s.value == st.NEEDS_YOU and s.kind == "balance_empty"
    assert "Payment required" in s.detail
    assert router._status.get("groq", "some-free-model").value == st.READY


@respx.mock
def test_bucket_that_all_needs_you_fails_fast_instead_of_waiting(two_provider_config):
    """A dead tier must error, not hang.

    wait=True is correct for a rate limit (a slot opens in seconds) and wrong
    for a deleted model (still deleted tomorrow). Sleeping on the latter is
    indistinguishable from a crash, which is how this was reported.
    """
    respx.post(CHAT_URL).mock(return_value=httpx.Response(404, text="model gone"))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(404, text="model gone"))

    router = LocalRouter(str(two_provider_config))
    started = time.monotonic()
    with pytest.raises(RouterBusy, match="needs you"):
        router.generate([{"role": "user", "content": "hi"}], tier="low")
    assert time.monotonic() - started < 30, "router waited instead of failing fast"


@respx.mock
def test_needs_you_failure_names_the_dead_models(two_provider_config):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(
        404, text="Model llama-3.1-8b-instant is archived"))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(404, text="Model gpt-oss-120b does not exist"))

    router = LocalRouter(str(two_provider_config))
    with pytest.raises(RouterBusy) as excinfo:
        router.generate([{"role": "user", "content": "hi"}], tier="low")
    message = str(excinfo.value)
    assert "groq/llama-3.1-8b-instant" in message
    assert "cerebras/gpt-oss-120b" in message
    assert "archived" in message


@respx.mock
def test_streaming_bucket_that_all_needs_you_fails_fast(two_provider_config):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(404, text="gone"))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(404, text="gone"))

    router = LocalRouter(str(two_provider_config))

    async def drain():
        async for _ in router.agenerate_stream(
                [{"role": "user", "content": "hi"}], tier="low"):
            pass

    started = time.monotonic()
    with pytest.raises(RouterBusy, match="needs you"):
        asyncio.run(drain())
    assert time.monotonic() - started < 30, "stream waited instead of failing fast"


@respx.mock
def test_empty_reply_makes_the_model_struggling(two_provider_config):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}],
        "usage": {}}))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE))
    router = LocalRouter(str(two_provider_config))
    router.generate([{"role": "user", "content": "hi"}], tier="low")
    s = router._status.get("groq", "llama-3.1-8b-instant")
    assert s.value == st.STRUGGLING and s.action == "try_now"


@respx.mock
def test_429_uses_retry_after_and_the_key_is_busy_too(two_provider_config):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(
        429, headers={"Retry-After": "42"}, json={"error": "slow down"}))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE))
    router = LocalRouter(str(two_provider_config))
    router.generate([{"role": "user", "content": "hi"}], tier="low")
    s = router._status.get("groq", "llama-3.1-8b-instant")
    assert s.value == st.BUSY
    assert 40 <= s.until - time.time() <= 42
    key = router._key_states.get("groq", router._cfg.providers["groq"].keys[0].id)
    assert key.status == "busy"


@respx.mock
def test_429_with_zero_quota_is_not_on_your_plan(two_provider_config):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(429, json=[{"error": {
        "code": 429, "message": "Quota exceeded for metric: free_tier_requests, limit: 0"}}]))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE))
    router = LocalRouter(str(two_provider_config))
    router.generate([{"role": "user", "content": "hi"}], tier="low")
    s = router._status.get("groq", "llama-3.1-8b-instant")
    assert s.value == st.NEEDS_YOU and s.kind == "not_on_plan"
    # The key is fine: it's this model that isn't on the plan.
    key = router._key_states.get("groq", router._cfg.providers["groq"].keys[0].id)
    assert key.status == "ready"


def test_pinned_busy_model_fails_at_once_with_the_countdown(two_provider_config):
    router = LocalRouter(str(two_provider_config))
    router._status.set_busy("groq", "llama-3.1-8b-instant", 40, "Too many requests",
                            status_code=429)
    started = time.monotonic()
    with pytest.raises(RouterBusy, match=r"429: .*retry in \d+s"):
        router.generate([{"role": "user", "content": "hi"}],
                        tier="groq/llama-3.1-8b-instant")
    assert time.monotonic() - started < 1


def test_model_whose_provider_is_not_set_up_needs_you(two_provider_config):
    import warnings
    import yaml
    cfg = yaml.safe_load(two_provider_config.read_text())
    cfg["tiers"]["low"].append({"provider": "zhipu", "model": "glm-5-2", "score": 50,
                                "rpm": 60, "tpm": 60000})
    two_provider_config.write_text(yaml.dump(cfg))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        router = LocalRouter(str(two_provider_config))
    rows = {(r["provider"], r["model"]): r for r in router._engine.explain_unavailable("low")}
    zhipu = rows[("zhipu", "glm-5-2")]
    assert zhipu["available"] is False
    assert zhipu["reason"] == "needs_you"
    assert "No zhipu provider set up" in zhipu["detail"]
