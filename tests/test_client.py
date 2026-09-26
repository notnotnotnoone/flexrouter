# tests/test_client.py
import pytest
import respx
import httpx
from flexrouter.client import AsyncClient, StreamChunk
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
async def test_inline_think_tag_is_split_into_reasoning_content():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "choices": [{"message": {
                "role": "assistant",
                "content": "<think>let me consider this</think>the answer is 4",
            }}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        })
    )
    async with AsyncClient() as client:
        result = await client.chat(ROUTE, MESSAGES)
    message = result["choices"][0]["message"]
    assert message["content"] == "the answer is 4"
    assert message["reasoning_content"] == "let me consider this"

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
async def test_429_message_keeps_the_providers_quota_text():
    quota_text = "Quota exceeded for quota metric 'Generate Content API requests per day'"
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": {"message": quota_text}})
    )
    from flexrouter.client import RateLimitError
    async with AsyncClient() as client:
        with pytest.raises(RateLimitError) as excinfo:
            await client.chat(ROUTE, MESSAGES)
    assert quota_text in str(excinfo.value)


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
async def test_403_is_a_refusal_of_this_model_not_a_bad_key():
    # Seen live: Mistral answers 403 "labs_not_enabled" for one Labs model
    # while the same key serves every other model fine.
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(403, json={"error": {"message": "Labs models not enabled"}})
    )
    from flexrouter.client import ProviderError
    async with AsyncClient() as client:
        with pytest.raises(ProviderError, match="Labs models not enabled") as info:
            await client.chat(ROUTE, MESSAGES)
    assert info.value.status_code == 403
    assert info.value.is_permanent

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


# Duration parsing lives in flexrouter/headers.py now — see tests/test_headers.py.


@pytest.mark.asyncio
@respx.mock
async def test_google_header_parser_uses_goog_prefixed_headers(tmp_path):
    google_route = RouteResult(
        provider="google",
        model="gemini-pro",
        api_key="test-key",
        base_url="https://api.groq.com/openai/v1",
        tier="low",
        header_parser="google",
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json=OK_RESPONSE,
            headers={"x-goog-ratelimit-remaining-requests": "3"},
        )
    )
    store = RateLimitStore(str(tmp_path))
    async with AsyncClient(rate_limit_store=store) as client:
        await client.chat(google_route, MESSAGES)
    assert store._data["google/gemini-pro"]["remaining_requests"] == 3


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
    assert [c.content for c in chunks] == ["Hello", ", ", "world!"]
    assert "".join(c.content for c in chunks) == "Hello, world!"


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
async def test_stream_chat_429_message_keeps_the_providers_quota_text():
    quota_text = "Quota exceeded for quota metric 'Generate Content API requests per day'"
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": {"message": quota_text}})
    )
    from flexrouter.client import RateLimitError
    async with AsyncClient() as client:
        with pytest.raises(RateLimitError) as excinfo:
            async for _ in client.stream_chat(ROUTE, MESSAGES):
                pass
    assert quota_text in str(excinfo.value)


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
    assert [c.content for c in collected] == ["Hel"]


@pytest.mark.asyncio
@respx.mock
async def test_stream_chat_yields_content_reasoning_tool_call_and_usage():
    sse_body = (
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        b'data: {"choices":[{"delta":{"reasoning_content":"thinking..."}}]}\n\n'
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"get_weather","arguments":"{\\"city\\":"}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":5}}\n\n'
        b'data: [DONE]\n\n'
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=sse_body)
    )
    chunks = []
    async with AsyncClient() as client:
        async for chunk in client.stream_chat(ROUTE, MESSAGES):
            chunks.append(chunk)

    assert chunks[0].content == "Hel"
    assert chunks[1].reasoning == "thinking..."
    assert chunks[2].tool_call_delta == {
        "index": 0, "id": "c1",
        "function": {"name": "get_weather", "arguments": '{"city":'},
    }
    assert chunks[3].usage == {"prompt_tokens": 10, "completion_tokens": 5}


@pytest.mark.asyncio
@respx.mock
async def test_stream_chat_accepts_reasoning_key_variant():
    # Cerebras (zai-glm-4.7, live-verified) sends the delta's reasoning text
    # under "reasoning", not the OpenAI-standard "reasoning_content" key.
    # Before this, those chunks matched no `if content or reasoning or usage`
    # branch and were silently dropped — invisible to callers, and on a
    # model that spends its whole token budget on reasoning, this made
    # stream_chat() look like it produced nothing at all.
    sse_body = (
        b'data: {"choices":[{"delta":{"reasoning":"thinking..."}}]}\n\n'
        b'data: [DONE]\n\n'
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=sse_body)
    )
    chunks = []
    async with AsyncClient() as client:
        async for chunk in client.stream_chat(ROUTE, MESSAGES):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].reasoning == "thinking..."


@pytest.mark.asyncio
@respx.mock
async def test_stream_chat_raises_on_inband_error_event():
    # Groq (live-verified) reports a malformed tool-call generation as an
    # HTTP-200 stream containing an SSE "event: error" line whose data has
    # no "choices" key at all. Before this, that data line parsed as valid
    # JSON, choices defaulted to [], delta/usage were both None, and the
    # whole chunk was silently `continue`d past — indistinguishable from a
    # model that generated nothing. That masked a real, named failure
    # ("tool_use_failed") as a generic empty response.
    sse_body = (
        b'event: error\n'
        b'data: {"error":{"message":"Failed to call a function.",'
        b'"type":"invalid_request_error","code":"tool_use_failed",'
        b'"status_code":400}}\n\n'
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=sse_body)
    )
    from flexrouter.client import ProviderError
    async with AsyncClient() as client:
        with pytest.raises(ProviderError, match="tool_use_failed"):
            async for _ in client.stream_chat(ROUTE, MESSAGES):
                pass


GOOGLE_ROUTE = RouteResult(
    provider="googleai",
    model="gemini-3.8-flash",
    api_key="test-key",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    tier="low",
)


@pytest.mark.asyncio
@respx.mock
async def test_presence_and_frequency_penalty_are_dropped_for_google_ai_studio():
    # Live-verified: Google AI Studio's OpenAI-compat endpoint hard-400s on
    # either field ("Invalid JSON payload received. Unknown name
    # 'frequency_penalty': Cannot find field."), so a caller who set either
    # one failed on every single Gemini model it was routed to.
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    ).mock(return_value=httpx.Response(200, json=OK_RESPONSE))
    async with AsyncClient() as client:
        await client.chat(GOOGLE_ROUTE, MESSAGES, presence_penalty=0.5, frequency_penalty=0.2,
                          temperature=0.7)
    sent = __import__("json").loads(route.calls.last.request.content)
    assert "presence_penalty" not in sent
    assert "frequency_penalty" not in sent
    assert sent["temperature"] == 0.7  # an unrelated param is untouched


@pytest.mark.asyncio
@respx.mock
async def test_presence_and_frequency_penalty_are_kept_for_other_providers():
    route = respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE)
    )
    async with AsyncClient() as client:
        await client.chat(ROUTE, MESSAGES, presence_penalty=0.5, frequency_penalty=0.2)
    sent = __import__("json").loads(route.calls.last.request.content)
    assert sent["presence_penalty"] == 0.5
    assert sent["frequency_penalty"] == 0.2
