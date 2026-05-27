# tests/test_flexrouter.py
import pytest, respx, httpx, warnings
from flexrouter import FlexRouter, RouterBusy, RouterError, ContextWindowWarning

OK_RESPONSE = {
    "choices": [{"message": {"role": "assistant", "content": "hello"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}

@respx.mock
def test_generate_returns_response(config_file):
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE)
    )
    router = FlexRouter(str(config_file))
    result = router.generate([{"role": "user", "content": "hi"}], tier="low")
    assert result["choices"][0]["message"]["content"] == "hello"

@respx.mock
def test_generate_raises_router_busy_when_no_wait(config_file):
    router = FlexRouter(str(config_file))
    # Exhaust the single model
    router._engine.penalize("groq", "llama-3.1-8b-instant")
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)

@respx.mock
def test_agenerate_works(config_file):
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE)
    )
    import asyncio
    router = FlexRouter(str(config_file))
    result = asyncio.run(router.agenerate([{"role": "user", "content": "hi"}], tier="low"))
    assert "choices" in result

@respx.mock
def test_generate_penalizes_on_429(config_file):
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": {"message": "rate limited"}})
    )
    router = FlexRouter(str(config_file))
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)
    assert router._engine._penalties.is_penalized("groq", "llama-3.1-8b-instant")

@respx.mock
def test_reload_reloads_config(config_file):
    router = FlexRouter(str(config_file))
    router.reload()  # should not raise

@respx.mock
def test_unknown_tier_raises_key_error(config_file):
    router = FlexRouter(str(config_file))
    with pytest.raises(KeyError):
        router.generate([], tier="nuclear", wait=False)
