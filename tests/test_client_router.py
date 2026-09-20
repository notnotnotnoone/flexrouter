import asyncio
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


# -- the failure envelope around the happy path ---------------------------


@pytest.mark.parametrize("failure", [
    httpx.ConnectTimeout("took too long to connect"),
    httpx.ReadTimeout("listening, but never answered"),
    httpx.ReadError("reset mid-request"),
    httpx.RemoteProtocolError("the service hung up"),
])
async def test_every_way_of_not_getting_an_answer_is_the_same_clear_error(failure):
    """A refused connection was the only one that produced the clear message.

    A connect timeout, a service that listens but never answers, and a reset
    mid-request all escaped as raw httpx errors, which are not RouterError
    either -- so a caller wrapping `except RouterError` caught none of them.
    """
    def handler(request):
        raise failure

    with pytest.raises(ServiceNotRunning) as exc:
        await _router(handler).agenerate([], "smart")
    message = str(exc.value)
    assert "flexrouter isn't running" in message
    assert "flexrouter serve" in message
    assert BASE in message


@pytest.mark.parametrize("failure", [
    httpx.ConnectTimeout("took too long to connect"),
    httpx.ReadTimeout("listening, but never answered"),
    httpx.RemoteProtocolError("the service hung up"),
])
async def test_the_stream_reports_a_silent_service_the_same_way(failure):
    def handler(request):
        raise failure

    with pytest.raises(ServiceNotRunning) as exc:
        async for _ in _router(handler).agenerate_stream([], "smart"):
            pass
    assert "flexrouter serve" in str(exc.value)


async def test_a_transport_failure_is_catchable_as_a_router_error():
    def handler(request):
        raise httpx.ReadTimeout("listening, but never answered")

    with pytest.raises(RouterError):
        await _router(handler).agenerate([], "smart")


def test_close_releases_the_pool_when_only_agenerate_was_used():
    """`close()` used to be a no-op unless `generate()` had made a loop, so an
    async caller's connection pool stayed open until garbage collection."""
    def handler(request):
        return httpx.Response(200, json={"choices": []})

    router = _router(handler)
    asyncio.run(router.agenerate([], "smart"))
    assert not router._http.is_closed
    router.close()
    assert router._http.is_closed


def test_close_is_safe_to_call_twice():
    def handler(request):
        return httpx.Response(200, json={"choices": []})

    router = _router(handler)
    router.close()
    router.close()
    assert router._http.is_closed


def test_close_still_works_after_the_synchronous_generate():
    def handler(request):
        return httpx.Response(200, json={"choices": []})

    router = _router(handler)
    router.generate([], "smart")
    router.close()
    assert router._http.is_closed
    assert router._loop is None


async def test_async_with_releases_the_client():
    def handler(request):
        return httpx.Response(200, json={"choices": []})

    router = _router(handler)
    async with router as r:
        await r.agenerate([], "smart")
        assert not r._http.is_closed
    assert router._http.is_closed


async def test_close_inside_a_running_loop_says_what_to_do_instead():
    """Better than the silent leak it replaces."""
    def handler(request):
        return httpx.Response(200, json={"choices": []})

    router = _router(handler)
    with pytest.raises(RuntimeError, match="aclose"):
        router.close()
    await router.aclose()
    assert router._http.is_closed


def test_a_broken_settings_file_is_not_reported_as_a_dead_service(tmp_path):
    """Swallowing it guessed port 4891, so an owner running the service on a
    configured port was told it was not running -- the one case where that
    message is wrong. A broken settings file is its own problem and says so."""
    from flexrouter.exceptions import ConfigError

    broken = tmp_path / "broken.yaml"
    broken.write_text("settings: {port: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        FlexRouter(str(broken))
    assert "broken.yaml" in str(exc.value)
    assert not isinstance(exc.value, ServiceNotRunning)


def test_settings_that_parse_but_name_no_port_still_fall_back_quietly(monkeypatch):
    from types import SimpleNamespace

    import flexrouter._client_router as mod

    monkeypatch.setattr("flexrouter.config.load_config",
                        lambda p=None: SimpleNamespace(port=None, auth_token=None))
    router = mod.FlexRouter()
    try:
        assert router._base_url == "http://127.0.0.1:4891"
    finally:
        router.close()


# -- the connection dying while the service is answering ------------------


def _dying_stream(failure, deltas=("par", "tial")):
    """A stream that delivers real deltas and then drops the connection."""
    chunks = [
        ('data: {"id":"c1","object":"chat.completion.chunk","choices":'
         '[{"index":0,"delta":{"content":"' + d + '"},"finish_reason":null}]}'
         "\n\n").encode()
        for d in deltas
    ]

    async def stream():
        for chunk in chunks:
            yield chunk
        raise failure

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=stream())

    return handler


@pytest.mark.parametrize("failure", [
    httpx.ReadError("the connection dropped"),
    httpx.RemoteProtocolError("the service hung up mid-answer"),
])
async def test_a_drop_part_way_through_is_a_router_error_after_the_deltas(failure):
    """The one failure the pre-response catch can never see, on the path that
    matters most: a long answer whose connection dies half-way. It used to
    surface as a raw httpx error, so a caller streaming inside
    `except RouterError` got an unhandled library exception and no DoneEvent."""
    seen = []
    with pytest.raises(RouterError) as exc:
        async for e in _router(_dying_stream(failure)).agenerate_stream([], "smart"):
            seen.append(e)

    assert [e.text for e in seen if isinstance(e, DeltaEvent)] == ["par", "tial"]
    assert not isinstance(exc.value, httpx.HTTPError)
    assert not isinstance(exc.value, ServiceNotRunning), (
        "the service did answer; the connection died while it was answering")
    assert "cut off part-way through" in str(exc.value)
    assert BASE in str(exc.value)


async def test_a_drop_part_way_through_yields_no_done_event():
    seen = []
    with pytest.raises(RouterError):
        async for e in _router(
                _dying_stream(httpx.ReadError("gone"))).agenerate_stream([], "smart"):
            seen.append(e)
    assert not any(isinstance(e, DoneEvent) for e in seen)


# -- the home and the settings, when the caller named the address ---------


def test_an_unreachable_home_is_a_configuration_error_not_a_raw_oserror(monkeypatch):
    """Dropping the blanket guard uncovered failures that are not read
    failures: a home that cannot be created raised a bare FileExistsError out
    of the constructor, which is not a project error type."""
    from flexrouter.exceptions import ConfigError

    monkeypatch.setattr("flexrouter.home.ensure_home",
                        lambda: (_ for _ in ()).throw(FileExistsError("in the way")))
    with pytest.raises(ConfigError) as exc:
        FlexRouter()
    assert "shared settings folder" in str(exc.value)
    assert "FileExistsError" in str(exc.value)


def test_an_explicit_base_url_survives_an_unrelated_broken_settings_file(tmp_path):
    """The caller has already said where the service is. An unrelated broken
    settings file must not stop them."""
    broken = tmp_path / "broken.yaml"
    broken.write_text("settings: {port: [unclosed", encoding="utf-8")
    router = FlexRouter(str(broken), base_url="http://127.0.0.1:9999")
    try:
        assert router._base_url == "http://127.0.0.1:9999"
        assert router._token is None
    finally:
        router.close()


def test_without_an_explicit_base_url_a_broken_settings_file_still_raises(tmp_path):
    from flexrouter.exceptions import ConfigError

    broken = tmp_path / "broken.yaml"
    broken.write_text("settings: {port: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError):
        FlexRouter(str(broken))
