import json

from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.client import ProviderError, StreamChunk
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [
            ModelConfig(provider="alpha", model="big", score=99, rpm=60, tpm=60000),
            ModelConfig(provider="beta", model="small", score=40, rpm=60, tpm=60000),
        ]},
        providers={
            "alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"]),
            "beta": ProviderConfig(base_url="https://beta.test/v1", api_keys=["k"]),
        },
        state_dir=str(tmp_path / "state"),
    )


def _run(tmp_path, monkeypatch, fake_stream):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    from flexrouter import app as app_mod
    app_mod.state.router = None
    client = TestClient(create_app(str(tmp_path / "config.yaml")))
    with client.stream("POST", "/v1/chat/completions", json={
            "model": "smart", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}) as r:
        return "".join(r.iter_text())


def _parsed(body):
    return [json.loads(l[6:]) for l in body.splitlines()
            if l.startswith("data: ") and l[6:].strip() != "[DONE]"]


def test_failing_before_the_first_delta_fails_over_to_the_other_model(tmp_path, monkeypatch):
    seen: list = []

    async def fake(self, route, messages, **kwargs):
        seen.append(route.provider)
        if route.provider == "alpha":
            raise ProviderError("alpha/big: upstream fell over")
        yield StreamChunk(content="hello")

    body = _run(tmp_path, monkeypatch, fake)
    assert seen == ["alpha", "beta"]
    assert "hello" in body
    assert '"error"' not in body


def test_failing_after_the_first_delta_never_switches_model(tmp_path, monkeypatch):
    seen: list = []

    async def fake(self, route, messages, **kwargs):
        seen.append(route.provider)
        yield StreamChunk(content="par")
        raise ProviderError("alpha/big: connection dropped mid-answer")

    body = _run(tmp_path, monkeypatch, fake)
    assert seen == ["alpha"], "mid-stream failover is a stated non-goal"
    assert "par" in body


def test_the_error_chunk_is_a_well_formed_chunk(tmp_path, monkeypatch):
    async def fake(self, route, messages, **kwargs):
        yield StreamChunk(content="par")
        raise ProviderError("alpha/big: connection dropped mid-answer")

    last = _parsed(_run(tmp_path, monkeypatch, fake))[-1]
    assert last["object"] == "chat.completion.chunk"
    assert last["id"].startswith("chatcmpl-")
    assert last["choices"][0]["finish_reason"] == "error"
    assert last["choices"][0]["delta"] == {}
    assert "connection dropped mid-answer" in last["error"]["message"]


def test_the_error_chunk_shares_the_id_of_the_content_chunks(tmp_path, monkeypatch):
    async def fake(self, route, messages, **kwargs):
        yield StreamChunk(content="par")
        raise ProviderError("alpha/big: dropped")

    chunks = _parsed(_run(tmp_path, monkeypatch, fake))
    assert len({c["id"] for c in chunks}) == 1


def test_the_stream_ends_with_the_done_marker_exactly_once(tmp_path, monkeypatch):
    async def fake(self, route, messages, **kwargs):
        yield StreamChunk(content="par")
        raise ProviderError("alpha/big: dropped")

    body = _run(tmp_path, monkeypatch, fake)
    assert body.count("data: [DONE]") == 1
    assert body.rstrip().endswith("data: [DONE]")


def test_reasoning_only_empty_completion_never_switches_model(tmp_path, monkeypatch):
    """Reasoning already reached the caller; an empty finish must not retry.

    A stream that spends its whole budget on reasoning and ends with no
    content and no tool calls used to look like "nothing happened yet" and
    fail over to the next provider - even though the caller may already be
    showing the reasoning text on screen. That's the same silent mid-answer
    model swap the content case is forbidden from doing.
    """
    seen: list = []

    async def fake(self, route, messages, **kwargs):
        seen.append(route.provider)
        yield StreamChunk(reasoning="thinking out loud")

    body = _run(tmp_path, monkeypatch, fake)
    assert seen == ["alpha"]
    assert "thinking out loud" in body


def test_reasoning_only_empty_completion_ends_with_one_well_formed_error(tmp_path, monkeypatch):
    async def fake(self, route, messages, **kwargs):
        yield StreamChunk(reasoning="thinking out loud")

    chunks = _parsed(_run(tmp_path, monkeypatch, fake))
    assert len({c["id"] for c in chunks}) == 1
    last = chunks[-1]
    assert last["object"] == "chat.completion.chunk"
    assert last["choices"][0]["delta"] == {}
    assert last["choices"][0]["finish_reason"] == "error"

    body = _run(tmp_path, monkeypatch, fake)
    assert body.count("data: [DONE]") == 1
    assert body.rstrip().endswith("data: [DONE]")


def test_a_provider_that_never_streams_anything_still_fails_over(tmp_path, monkeypatch):
    """End-to-end companion to the pre-first-chunk empty-stream retry.

    Alpha's fake returns before yielding a single chunk, so this exercises
    the pre-first-chunk `StopAsyncIteration` handler in `_router.py`
    (nothing has been attempted yet, let alone committed), not the
    post-commit "empty completion after a yielded chunk" branch — that
    branch (a stream whose one chunk carries no content, reasoning, or tool
    call) is covered directly in
    test_agenerate_stream.py::test_empty_committed_stream_with_no_yields_still_retries_next_provider.
    This test still earns its keep as an end-to-end check that a provider
    which streams nothing at all fails over through the real HTTP path.
    """
    seen: list = []

    async def fake(self, route, messages, **kwargs):
        seen.append(route.provider)
        if route.provider == "alpha":
            return
            yield  # pragma: no cover - makes this an async generator
        yield StreamChunk(content="hello")

    body = _run(tmp_path, monkeypatch, fake)
    assert seen == ["alpha", "beta"]
    assert "hello" in body
    assert '"error"' not in body
