import json

import pytest

from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError, StreamChunk
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [
            ModelConfig(provider="alpha", model="big", score=99,
                        rpm=60, tpm=60000, context_window=100_000),
            ModelConfig(provider="beta", model="small", score=40,
                        rpm=60, tpm=60000, context_window=100_000),
        ]},
        providers={
            "alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"]),
            "beta": ProviderConfig(base_url="https://beta.test/v1", api_keys=["k"]),
        },
        state_dir=str(tmp_path / "state"),
    )


def _router(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


def _traces(tmp_path):
    p = tmp_path / "state" / "traces.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]


async def _drain(router, messages, tier, **kwargs):
    events = []
    async for ev in router.agenerate_stream(messages, tier, **kwargs):
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_a_successful_stream_writes_one_trace_with_a_first_token_time(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_stream(self, route, messages, **kwargs):
        yield StreamChunk(content="hi")
        yield StreamChunk(usage={"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4})

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    await _drain(router, [{"role": "user", "content": "hi"}], "smart", stream=True)

    traces = _traces(tmp_path)
    assert len(traces) == 1
    t = traces[0]
    assert t["ok"] is True
    assert t["asked"]["stream"] is True
    assert t["answered_by"] == {"provider": "alpha", "model": "big", "key_id": None}
    assert t["tokens"] == {"in": 3, "out": 1}
    assert isinstance(t["ms_to_first_token"], int)
    assert t["ms_to_first_token"] <= t["ms_total"]


@pytest.mark.asyncio
async def test_failure_before_first_delta_retries_and_records_the_failed_attempt(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_stream(self, route, messages, **kwargs):
        if route.provider == "alpha":
            raise ProviderError("alpha/big: down", status_code=500)
        yield StreamChunk(content="hi")

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    await _drain(router, [{"role": "user", "content": "hi"}], "smart", stream=True)

    t = _traces(tmp_path)[0]
    assert t["ok"] is True
    assert len(t["attempts"]) == 1
    assert t["attempts"][0]["provider"] == "alpha"
    assert t["answered_by"]["provider"] == "beta"


@pytest.mark.asyncio
async def test_a_failure_after_the_first_delta_still_writes_a_trace(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_stream(self, route, messages, **kwargs):
        yield StreamChunk(content="par")
        raise ProviderError("alpha/big: connection dropped mid-answer", status_code=500)

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)

    try:
        await _drain(router, [{"role": "user", "content": "hi"}], "smart", stream=True)
    except Exception:
        pass

    traces = _traces(tmp_path)
    assert len(traces) == 1
    t = traces[0]
    assert t["ok"] is False
    assert t["answered_by"] is None
    assert "connection dropped mid-answer" in t["attempts"][-1]["provider_message"]
    assert t["attempts"][-1]["provider"] == "alpha"
