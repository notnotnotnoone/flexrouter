# tests/test_client.py
import pytest
import respx
import httpx
from flexrouter.client import AsyncClient
from flexrouter.engine import RouteResult
from flexrouter.rate_limits import RateLimitStore

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
async def test_200_missing_choices_raises_provider_error():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"usage": {"total_tokens": 8}})
    )
    from flexrouter.client import ProviderError
    async with AsyncClient() as client:
        with pytest.raises(ProviderError, match="malformed response"):
            await client.chat(ROUTE, MESSAGES)

@pytest.mark.asyncio
@respx.mock
async def test_200_empty_choices_raises_provider_error():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    from flexrouter.client import ProviderError
    async with AsyncClient() as client:
        with pytest.raises(ProviderError, match="malformed response"):
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

@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_headers_stored_on_success(tmp_path):
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json=OK_RESPONSE,
            headers={
                "x-ratelimit-limit-requests": "30",
                "x-ratelimit-limit-tokens": "6000",
            },
        )
    )
    store = RateLimitStore(str(tmp_path))
    async with AsyncClient(rate_limit_store=store) as client:
        await client.chat(ROUTE, MESSAGES)
    assert store.get_rpm("groq", "llama-8b", default=0) == 30
    assert store.get_tpm("groq", "llama-8b", default=0) == 6000


@pytest.mark.asyncio
@respx.mock
async def test_missing_rate_limit_headers_no_error(tmp_path):
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE)
    )
    store = RateLimitStore(str(tmp_path))
    async with AsyncClient(rate_limit_store=store) as client:
        result = await client.chat(ROUTE, MESSAGES)
    assert result["choices"][0]["message"]["content"] == "hi"
    assert store.get_rpm("groq", "llama-8b", default=-1) == -1
    assert store.get_tpm("groq", "llama-8b", default=-1) == -1


@pytest.mark.asyncio
@respx.mock
async def test_no_store_still_works():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json=OK_RESPONSE,
            headers={"x-ratelimit-limit-requests": "30"},
        )
    )
    async with AsyncClient() as client:
        result = await client.chat(ROUTE, MESSAGES)
    assert result["choices"][0]["message"]["content"] == "hi"


import pytest
from flexrouter.client import _parse_duration_ms

@pytest.mark.parametrize("s,expected", [
    ("45s", 45000),
    ("1m30s", 90000),
    ("12ms", 12),
    ("2", 2000),       # bare number = seconds
    ("", None),
    ("garbage", None),
])
def test_parse_duration_ms(s, expected):
    assert _parse_duration_ms(s) == expected


# --- stream_chat() ---

class _DropAfterOneChunk(httpx.AsyncByteStream):
    """Yields one real SSE delta, then blows up mid-stream (simulates a
    dropped connection after content has already started arriving)."""
    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        raise httpx.ReadError("connection dropped mid-stream")


@pytest.mark.asyncio
@respx.mock
async def test_stream_chat_yields_multiple_chunks_and_stops_at_done():
    sse_body = (
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":", "}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"world!"}}]}\n\n'
        b'data: [DONE]\n\n'
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=sse_body)
    )
    from flexrouter.client import AsyncClient as _AC
    chunks = []
    async with _AC() as client:
        async for delta in client.stream_chat(ROUTE, MESSAGES):
            chunks.append(delta)
    assert chunks == ["Hello", ", ", "world!"]
    assert "".join(chunks) == "Hello, world!"


@pytest.mark.asyncio
@respx.mock
async def test_stream_chat_429_raises_rate_limit_error_before_any_content():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": {"message": "rate limited"}})
    )
    from flexrouter.client import RateLimitError
    collected = []
    async with AsyncClient() as client:
        with pytest.raises(RateLimitError):
            async for delta in client.stream_chat(ROUTE, MESSAGES):
                collected.append(delta)
    assert collected == []


@pytest.mark.asyncio
@respx.mock
async def test_stream_chat_malformed_sse_raises_provider_error():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=b"data: {not valid json}\n\n")
    )
    from flexrouter.client import ProviderError
    collected = []
    async with AsyncClient() as client:
        with pytest.raises(ProviderError):
            async for delta in client.stream_chat(ROUTE, MESSAGES):
                collected.append(delta)
    assert collected == []


@pytest.mark.asyncio
@respx.mock
async def test_stream_chat_drop_after_first_delta_raises_not_swallowed():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, stream=_DropAfterOneChunk())
    )
    from flexrouter.client import ProviderError
    collected = []
    async with AsyncClient() as client:
        with pytest.raises(ProviderError):
            async for delta in client.stream_chat(ROUTE, MESSAGES):
                collected.append(delta)
    # We must have actually received the real delta before the failure —
    # a silently truncated (empty) stream would also "pass" a bare
    # pytest.raises check, so assert forward progress explicitly.
    assert collected == ["Hel"]
