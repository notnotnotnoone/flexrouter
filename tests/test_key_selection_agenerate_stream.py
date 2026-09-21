import json

import pytest

from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError, StreamChunk
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.exceptions import RouterError
from flexrouter.keys import KeyRecord


def _cfg(tmp_path):
    keys = [KeyRecord(id="k1", secret="secret-1"), KeyRecord(id="k2", secret="secret-2")]
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(
            base_url="https://alpha.test/v1", api_keys=[r.secret for r in keys],
            keys=keys, key_strategy="round_robin")},
        state_dir=str(tmp_path / "state"),
        key_concurrency_cap=4,
    )


def _router(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


async def _drain(router, **kwargs):
    events = []
    async for ev in router.agenerate_stream([{"role": "user", "content": "hi"}], "smart", **kwargs):
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_round_robin_alternates_keys_in_the_streaming_path(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)
    used_keys = []

    async def fake_stream(self, route, messages, **kwargs):
        used_keys.append(route.api_key)
        yield StreamChunk(content="hi")
        yield StreamChunk(usage={"total_tokens": 1})

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    for _ in range(4):
        await _drain(router)
    assert used_keys == ["secret-1", "secret-2", "secret-1", "secret-2"]


@pytest.mark.asyncio
async def test_an_auth_failure_benches_only_the_used_key_in_streaming(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)
    calls = {"n": 0}

    async def fake_stream(self, route, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RouterError("Auth failure for provider 'alpha': 401")
        yield StreamChunk(content="hi")
        yield StreamChunk(usage={"total_tokens": 1})

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    await _drain(router)

    assert router._key_states.get("alpha", "k1").status == "benched"
    assert router._key_states.get("alpha", "k2").status == "live"


@pytest.mark.asyncio
async def test_key_id_appears_in_the_streaming_trace(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_stream(self, route, messages, **kwargs):
        yield StreamChunk(content="hi")
        yield StreamChunk(usage={"total_tokens": 1})

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    await _drain(router)

    trace_path = tmp_path / "state" / "traces.jsonl"
    entry = json.loads(trace_path.read_text(encoding="utf-8").strip())
    assert entry["answered_by"]["key_id"] == "k1"


@pytest.mark.asyncio
async def test_active_requests_is_decremented_after_a_mid_stream_failure(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_stream(self, route, messages, **kwargs):
        yield StreamChunk(content="par")
        raise ProviderError("alpha/big: dropped", status_code=500)

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    try:
        await _drain(router)
    except Exception:
        pass
    assert router._key_states.get("alpha", "k1").active_requests == 0
