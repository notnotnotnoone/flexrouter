# tests/test_agenerate_stream.py
import pytest

from flexrouter import (
    FlexRouter, RouterBusy, AttemptEvent, AttemptFailedEvent, DeltaEvent,
    ReasoningDeltaEvent, ToolCallDeltaEvent, DoneEvent,
)
from flexrouter.client import RateLimitError, ProviderError, StreamChunk
from flexrouter.engine import RouteResult


ROUTE = RouteResult(
    provider="groq",
    model="llama-3.1-8b-instant",
    api_key="test-key",
    base_url="https://api.groq.com/openai/v1",
    tier="low",
)

MESSAGES = [{"role": "user", "content": "hi"}]


def _router(config_file):
    router = FlexRouter(str(config_file))
    router._cfg.retry.backoff_seconds = 0  # keep failed-attempt tests fast
    return router


def _select_sequence(routes):
    """Returns a callable matching RoutingEngine.select's signature that
    hands back routes (or None) from `routes` in order, one per call."""
    it = iter(routes)

    def _select(tier, estimated_tokens, vision, session_id=None):
        return next(it)

    return _select


def _ok_stream(chunks):
    async def stream_chat(route, messages, **kwargs):
        for c in chunks:
            yield StreamChunk(content=c)
    return stream_chat


def _fail_stream(exc):
    async def stream_chat(route, messages, **kwargs):
        raise exc
        yield  # pragma: no cover - makes this an async generator function

    return stream_chat


def _drop_after_one_stream(first_chunk, exc):
    async def stream_chat(route, messages, **kwargs):
        yield StreamChunk(content=first_chunk)
        raise exc

    return stream_chat


def _stream_chat_sequence(behaviors):
    """Returns a callable matching AsyncClient.stream_chat's signature that
    delegates to a different async-generator factory on each call."""
    it = iter(behaviors)

    def _stream_chat(route, messages, **kwargs):
        return next(it)(route, messages, **kwargs)

    return _stream_chat


@pytest.mark.asyncio
async def test_clean_single_attempt_success(config_file, monkeypatch):
    router = _router(config_file)
    monkeypatch.setattr(router._engine, "select", _select_sequence([ROUTE]))
    monkeypatch.setattr(
        router._client, "stream_chat", _stream_chat_sequence([_ok_stream(["Hel", "lo"])])
    )

    events = []
    async for event in router.agenerate_stream(MESSAGES, tier="low"):
        events.append(event)

    assert len(events) == 4
    assert isinstance(events[0], AttemptEvent)
    assert events[0].attempt == 1 and events[0].max_attempts == 4
    assert events[0].provider == "groq" and events[0].model == "llama-3.1-8b-instant"
    assert isinstance(events[1], DeltaEvent) and events[1].text == "Hel"
    assert isinstance(events[2], DeltaEvent) and events[2].text == "lo"
    assert isinstance(events[3], DoneEvent)
    assert events[3].result["choices"][0]["message"]["content"] == "Hello"


@pytest.mark.asyncio
async def test_failed_attempts_then_success_interleaves_events(config_file, monkeypatch):
    router = _router(config_file)
    monkeypatch.setattr(router._engine, "select", _select_sequence([ROUTE, ROUTE, ROUTE]))
    monkeypatch.setattr(
        router._client,
        "stream_chat",
        _stream_chat_sequence([
            _fail_stream(RateLimitError("429")),
            _fail_stream(ProviderError("500")),
            _ok_stream(["ok"]),
        ]),
    )

    events = []
    async for event in router.agenerate_stream(MESSAGES, tier="low"):
        events.append(event)

    kinds = [type(e).__name__ for e in events]
    assert kinds == [
        "AttemptEvent", "AttemptFailedEvent",
        "AttemptEvent", "AttemptFailedEvent",
        "AttemptEvent", "DeltaEvent", "DoneEvent",
    ]
    assert events[1].reason == "rate_limited"
    assert events[1].attempt == 1
    assert events[3].reason == "provider_error"
    assert events[3].attempt == 2
    assert events[4].attempt == 3
    assert events[-1].result["choices"][0]["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_total_exhaustion_raises_router_busy(config_file, monkeypatch):
    router = _router(config_file)
    max_attempts = router._cfg.retry.retries + 1
    monkeypatch.setattr(router._engine, "select", _select_sequence([ROUTE] * max_attempts))
    monkeypatch.setattr(
        router._client,
        "stream_chat",
        _stream_chat_sequence([_fail_stream(RateLimitError("429")) for _ in range(max_attempts)]),
    )

    events = []
    with pytest.raises(RouterBusy):
        async for event in router.agenerate_stream(MESSAGES, tier="low"):
            events.append(event)

    failed = [e for e in events if isinstance(e, AttemptFailedEvent)]
    attempts = [e for e in events if isinstance(e, AttemptEvent)]
    assert len(failed) == max_attempts
    assert len(attempts) == max_attempts


@pytest.mark.asyncio
async def test_mid_stream_failure_propagates_without_retry(config_file, monkeypatch):
    router = _router(config_file)
    # Only one route ever handed out: if a retry were attempted after the
    # mid-stream failure, select() would be called a second time and raise
    # StopIteration from the exhausted iterator, failing the test loudly.
    monkeypatch.setattr(router._engine, "select", _select_sequence([ROUTE]))
    monkeypatch.setattr(
        router._client,
        "stream_chat",
        _stream_chat_sequence([_drop_after_one_stream("Hel", ProviderError("dropped"))]),
    )

    events = []
    with pytest.raises(ProviderError):
        async for event in router.agenerate_stream(MESSAGES, tier="low"):
            events.append(event)

    assert len(events) == 2
    assert isinstance(events[0], AttemptEvent)
    assert isinstance(events[1], DeltaEvent) and events[1].text == "Hel"


@pytest.mark.asyncio
async def test_reasoning_delta_is_yielded_as_separate_event(config_file, monkeypatch):
    router = _router(config_file)
    monkeypatch.setattr(router._engine, "select", _select_sequence([ROUTE]))

    async def _stream(route, messages, **kwargs):
        yield StreamChunk(reasoning="thinking")
        yield StreamChunk(content="answer")

    monkeypatch.setattr(
        router._client, "stream_chat", _stream_chat_sequence([_stream])
    )

    events = [e async for e in router.agenerate_stream(MESSAGES, tier="low")]
    reasoning_events = [e for e in events if isinstance(e, ReasoningDeltaEvent)]
    assert len(reasoning_events) == 1
    assert reasoning_events[0].text == "thinking"


@pytest.mark.asyncio
async def test_tool_call_delta_is_yielded_and_not_accumulated_into_content(config_file, monkeypatch):
    router = _router(config_file)
    monkeypatch.setattr(router._engine, "select", _select_sequence([ROUTE]))

    async def _stream(route, messages, **kwargs):
        yield StreamChunk(tool_call_delta={
            "index": 0, "id": "c1", "function": {"name": "f", "arguments": "{}"},
        })

    monkeypatch.setattr(
        router._client, "stream_chat", _stream_chat_sequence([_stream])
    )

    events = [e async for e in router.agenerate_stream(MESSAGES, tier="low")]
    tc_events = [e for e in events if isinstance(e, ToolCallDeltaEvent)]
    assert len(tc_events) == 1
    assert tc_events[0].name == "f"
    done = [e for e in events if isinstance(e, DoneEvent)][0]
    assert done.result["choices"][0]["message"]["content"] == ""  # tool call didn't add to text


@pytest.mark.asyncio
async def test_multiple_tool_calls_sharing_index_zero_are_kept_separate(config_file, monkeypatch):
    # Gemini's OpenAI-compat endpoint (live-verified) doesn't increment
    # "index" per tool call the way OpenAI/Groq/Cerebras do. Every call in
    # a multi-call turn arrives at index 0, each with its own unique "id"
    # and its *complete* arguments in a single chunk (not an incremental
    # fragment). Keying accumulation on index alone merges them into one
    # entry and concatenates their JSON into an unparseable blob.
    router = _router(config_file)
    monkeypatch.setattr(router._engine, "select", _select_sequence([ROUTE]))

    async def _stream(route, messages, **kwargs):
        yield StreamChunk(tool_call_delta={
            "index": 0, "id": "call-a",
            "function": {"name": "add_memory_note", "arguments": '{"content":"x"}'},
        })
        yield StreamChunk(tool_call_delta={
            "index": 0, "id": "call-b",
            "function": {"name": "add_vice", "arguments": '{"name":"vaping"}'},
        })

    monkeypatch.setattr(router._client, "stream_chat", _stream_chat_sequence([_stream]))

    events = [e async for e in router.agenerate_stream(MESSAGES, tier="low")]
    done = [e for e in events if isinstance(e, DoneEvent)][0]
    tool_calls = done.result["choices"][0]["message"]["tool_calls"]

    assert len(tool_calls) == 2
    assert tool_calls[0]["function"]["name"] == "add_memory_note"
    assert tool_calls[0]["function"]["arguments"] == '{"content":"x"}'
    assert tool_calls[1]["function"]["name"] == "add_vice"
    assert tool_calls[1]["function"]["arguments"] == '{"name":"vaping"}'


@pytest.mark.asyncio
async def test_empty_committed_stream_retries_next_provider_instead_of_terminating(
    config_file, monkeypatch
):
    # A stream that yields a chunk (so it's past the pre-first-chunk retry
    # window) but ends with no content and no tool calls — e.g. a reasoning
    # model that burns its whole token budget on reasoning — must still
    # rotate to the next provider instead of being treated as a committed,
    # terminal success with a silently empty reply.
    router = _router(config_file)
    monkeypatch.setattr(router._engine, "select", _select_sequence([ROUTE, ROUTE]))

    async def _empty_stream(route, messages, **kwargs):
        yield StreamChunk(reasoning="thinking forever")

    monkeypatch.setattr(
        router._client,
        "stream_chat",
        _stream_chat_sequence([_empty_stream, _ok_stream(["real answer"])]),
    )

    events = [e async for e in router.agenerate_stream(MESSAGES, tier="low")]

    kinds = [type(e).__name__ for e in events]
    assert kinds == [
        "AttemptEvent", "ReasoningDeltaEvent", "AttemptFailedEvent",
        "AttemptEvent", "DeltaEvent", "DoneEvent",
    ]
    assert events[2].reason == "provider_error"
    assert events[2].attempt == 1
    assert events[-1].result["choices"][0]["message"]["content"] == "real answer"


@pytest.mark.asyncio
async def test_final_usage_chunk_populates_done_event_usage(config_file, monkeypatch):
    router = _router(config_file)
    monkeypatch.setattr(router._engine, "select", _select_sequence([ROUTE]))

    async def _stream(route, messages, **kwargs):
        yield StreamChunk(content="hi")
        yield StreamChunk(usage={"prompt_tokens": 3, "completion_tokens": 1})

    monkeypatch.setattr(
        router._client, "stream_chat", _stream_chat_sequence([_stream])
    )

    events = [e async for e in router.agenerate_stream(MESSAGES, tier="low")]
    done = [e for e in events if isinstance(e, DoneEvent)][0]
    assert done.result["usage"] == {"prompt_tokens": 3, "completion_tokens": 1}
