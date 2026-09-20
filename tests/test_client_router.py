import json

import httpx
import pytest

from flexrouter import FlexRouter, RouterError, ServiceNotRunning
from flexrouter._router import DeltaEvent, DoneEvent, ToolCallDeltaEvent

BASE = "http://127.0.0.1:4891"


def _router(handler):
    r = FlexRouter(base_url=BASE)
    r._http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                base_url=BASE)
    return r


def _sse(lines):
    return httpx.Response(200, text="\n\n".join(lines) + "\n\n",
                          headers={"content-type": "text/event-stream"})


async def test_agenerate_returns_what_the_service_returned():
    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert body["model"] == "smart"
        assert body["stream"] is False
        return httpx.Response(200, json={"choices": [
            {"message": {"role": "assistant", "content": "hi"}}]})

    out = await _router(handler).agenerate([{"role": "user", "content": "yo"}], "smart")
    assert out["choices"][0]["message"]["content"] == "hi"


async def test_the_tier_argument_becomes_the_model_name():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": []})

    await _router(handler).agenerate([], "fast")
    assert seen["model"] == "fast"


async def test_a_pinned_model_passes_straight_through():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": []})

    await _router(handler).agenerate([], "groq/llama-3.3")
    assert seen["model"] == "groq/llama-3.3"


async def test_extra_arguments_are_forwarded():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": []})

    await _router(handler).agenerate([], "smart", temperature=0.2,
                                     tools=[{"type": "function"}])
    assert seen["temperature"] == 0.2
    assert seen["tools"] == [{"type": "function"}]


async def test_a_service_error_is_raised_with_the_services_words():
    def handler(request):
        return httpx.Response(404, json={"error": {
            "message": "There is no bucket named 'nope'.",
            "type": "invalid_request_error", "code": "model_not_found"}})

    with pytest.raises(RouterError) as exc:
        await _router(handler).agenerate([], "nope")
    assert "no bucket named 'nope'" in str(exc.value)


async def test_a_dead_service_raises_one_clear_error():
    def handler(request):
        raise httpx.ConnectError("nothing listening")

    with pytest.raises(ServiceNotRunning) as exc:
        await _router(handler).agenerate([], "smart")
    message = str(exc.value)
    assert "flexrouter isn't running" in message
    assert "flexrouter serve" in message
    assert BASE in message


async def test_a_dead_service_never_routes_locally(monkeypatch):
    called = []
    monkeypatch.setattr("flexrouter._router.LocalRouter.agenerate",
                        lambda *a, **k: called.append(1))

    def handler(request):
        raise httpx.ConnectError("nothing listening")

    with pytest.raises(ServiceNotRunning):
        await _router(handler).agenerate([], "smart")
    assert called == [], "a silent local fallback resurrects per-project state"


async def test_stream_events_are_rebuilt_from_the_wire():
    lines = [
        'data: {"id":"c1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{"content":"he"},"finish_reason":null}]}',
        'data: {"id":"c1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{"content":"llo"},"finish_reason":null}]}',
        'data: {"id":"c1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]

    def handler(request):
        assert json.loads(request.content)["stream"] is True
        return _sse(lines)

    events = [e async for e in _router(handler).agenerate_stream([], "smart")]
    assert [e.text for e in events if isinstance(e, DeltaEvent)] == ["he", "llo"]
    assert isinstance(events[-1], DoneEvent)
    assert events[-1].result["choices"][0]["message"]["content"] == "hello"


async def test_tool_call_deltas_keep_the_providers_dictionary():
    raw = {"index": 0, "id": "call_1", "type": "function",
           "function": {"name": "f", "arguments": "{}"}}
    lines = [
        'data: {"id":"c1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{"tool_calls":[' + json.dumps(raw) + ']},'
        '"finish_reason":null}]}',
        'data: {"id":"c1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
        "data: [DONE]",
    ]

    def handler(request):
        return _sse(lines)

    events = [e async for e in _router(handler).agenerate_stream([], "smart")]
    tc = next(e for e in events if isinstance(e, ToolCallDeltaEvent))
    assert tc.raw == raw
    assert tc.name == "f"
    done = events[-1]
    assert done.result["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "f"


async def test_an_error_chunk_mid_stream_raises_after_the_deltas():
    lines = [
        'data: {"id":"c1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{"content":"par"},"finish_reason":null}]}',
        'data: {"id":"c1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{},"finish_reason":"error"}],'
        '"error":{"message":"dropped","type":"server_error"}}',
        "data: [DONE]",
    ]

    def handler(request):
        return _sse(lines)

    seen = []
    with pytest.raises(RouterError) as exc:
        async for e in _router(handler).agenerate_stream([], "smart"):
            seen.append(e)
    assert [e.text for e in seen if isinstance(e, DeltaEvent)] == ["par"]
    assert "dropped" in str(exc.value)


async def test_the_client_sends_the_local_key_when_one_is_set():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": []})

    router = _router(handler)
    router._token = "flx-secret-token-value-here"
    await router.agenerate([], "smart")
    assert seen["auth"] == "Bearer flx-secret-token-value-here"


def test_reload_is_a_no_op_that_does_not_explode():
    FlexRouter(base_url=BASE).reload()
