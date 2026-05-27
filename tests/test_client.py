# tests/test_client.py
import pytest
import respx
import httpx
from flexrouter.client import AsyncClient
from flexrouter.engine import RouteResult

ROUTE = RouteResult(
    provider="groq",
    model="llama-8b",
    api_key="test-key",
    base_url="https://api.groq.com/openai/v1",
    tier="low",
)

MESSAGES = [{"role": "user", "content": "hello"}]

OK_RESPONSE = {
    "choices": [{"message": {"role": "assistant", "content": "hi"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}

@pytest.mark.asyncio
@respx.mock
async def test_successful_call_returns_dict():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE)
    )
    async with AsyncClient() as client:
        result = await client.chat(ROUTE, MESSAGES)
    assert result["choices"][0]["message"]["content"] == "hi"

@pytest.mark.asyncio
@respx.mock
async def test_returns_tokens_used():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE)
    )
    async with AsyncClient() as client:
        result = await client.chat(ROUTE, MESSAGES)
    assert result["usage"]["total_tokens"] == 8

@pytest.mark.asyncio
@respx.mock
async def test_429_raises_rate_limit_error():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": {"message": "rate limited"}})
    )
    from flexrouter.client import RateLimitError
    async with AsyncClient() as client:
        with pytest.raises(RateLimitError):
            await client.chat(ROUTE, MESSAGES)

@pytest.mark.asyncio
@respx.mock
async def test_401_raises_router_error():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "unauthorized"}})
    )
    from flexrouter.exceptions import RouterError
    async with AsyncClient() as client:
        with pytest.raises(RouterError, match="Auth failure"):
            await client.chat(ROUTE, MESSAGES)

@pytest.mark.asyncio
@respx.mock
async def test_500_raises_provider_error():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": {"message": "server error"}})
    )
    from flexrouter.client import ProviderError
    async with AsyncClient() as client:
        with pytest.raises(ProviderError):
            await client.chat(ROUTE, MESSAGES)

@pytest.mark.asyncio
@respx.mock
async def test_passes_extra_kwargs():
    captured = {}
    def capture(request, *args, **kwargs):
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=OK_RESPONSE)
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=capture)
    async with AsyncClient() as client:
        await client.chat(ROUTE, MESSAGES, max_tokens=512, temperature=0.2)
    assert captured["max_tokens"] == 512
    assert captured["temperature"] == 0.2

@pytest.mark.asyncio
@respx.mock
async def test_403_raises_router_error():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(403, json={"error": {"message": "forbidden"}})
    )
    from flexrouter.exceptions import RouterError
    async with AsyncClient() as client:
        with pytest.raises(RouterError, match="Auth failure"):
            await client.chat(ROUTE, MESSAGES)
