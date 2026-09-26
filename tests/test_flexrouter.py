# tests/test_flexrouter.py
import pytest, respx, httpx, warnings
from flexrouter import LocalRouter, RouterBusy, RouterError, ContextWindowWarning

OK_RESPONSE = {
    "choices": [{"message": {"role": "assistant", "content": "hello"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}

@respx.mock
def test_generate_returns_response(config_file):
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE)
    )
    router = LocalRouter(str(config_file))
    result = router.generate([{"role": "user", "content": "hi"}], tier="low")
    assert result["choices"][0]["message"]["content"] == "hello"

@respx.mock
def test_generate_raises_router_busy_when_no_wait(config_file):
    router = LocalRouter(str(config_file))
    # Exhaust the single model
    router._status.set_busy("groq", "llama-3.1-8b-instant", 60, "Too many requests")
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)

@respx.mock
def test_agenerate_works(config_file):
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE)
    )
    import asyncio
    router = LocalRouter(str(config_file))
    result = asyncio.run(router.agenerate([{"role": "user", "content": "hi"}], tier="low"))
    assert "choices" in result

@respx.mock
def test_generate_marks_busy_on_429(config_file):
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": {"message": "rate limited"}})
    )
    router = LocalRouter(str(config_file))
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)
    assert router._status.get("groq", "llama-3.1-8b-instant").value == "busy"

@respx.mock
def test_reload_reloads_config(config_file):
    router = LocalRouter(str(config_file))
    router.reload()  # should not raise

@respx.mock
def test_unknown_tier_raises_key_error(config_file):
    router = LocalRouter(str(config_file))
    with pytest.raises(KeyError):
        router.generate([], tier="nuclear", wait=False)

def test_flexrouter_creates_rate_limit_store(config_file):
    """LocalRouter wires RateLimitStore to client and engine."""
    from flexrouter.rate_limits import RateLimitStore
    router = LocalRouter(str(config_file))
    assert router._rate_limit_store is not None
    assert isinstance(router._rate_limit_store, RateLimitStore)
    assert router._client._rate_limit_store is router._rate_limit_store
    assert router._engine._rate_limit_store is router._rate_limit_store
    router.close()


def test_remaining_capacity_returns_headroom_for_configured_tier(config_file):
    router = LocalRouter(str(config_file))
    cap = router.remaining_capacity("low")
    assert cap["groq/llama-3.1-8b-instant"]["tpm_remaining"] == 60000
    assert cap["groq/llama-3.1-8b-instant"]["rpm_remaining"] == 60


def test_remaining_capacity_unknown_tier_raises_key_error(config_file):
    router = LocalRouter(str(config_file))
    with pytest.raises(KeyError):
        router.remaining_capacity("nonexistent")
