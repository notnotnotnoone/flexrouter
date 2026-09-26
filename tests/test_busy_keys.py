"""A key at its concurrency cap is busy, not broken.

Pinging many models at once used to push every provider's keys to the cap;
the router then penalized each queued model for 30 seconds and spent a
retry sleeping it off, so most of the burst failed without a single call
reaching the provider.
"""
import asyncio
import json

import pytest

from flexrouter._router import LocalRouter
from flexrouter.client import StreamChunk
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.keys import KeyRecord


def _cfg(tmp_path):
    keys = [KeyRecord(id="k1", secret="secret-1")]
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=600, tpm=600000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(
            base_url="https://alpha.test/v1", api_keys=[r.secret for r in keys], keys=keys)},
        state_dir=str(tmp_path / "state"),
        key_concurrency_cap=1,
    )


def _router(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


def _traces(tmp_path):
    lines = (tmp_path / "state" / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


async def _drain(router):
    return [ev async for ev in router.agenerate_stream(
        [{"role": "user", "content": "hi"}], "smart")]


@pytest.mark.asyncio
async def test_a_burst_beyond_the_key_cap_queues_instead_of_failing_in_streaming(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_stream(self, route, messages, **kwargs):
        await asyncio.sleep(0.05)
        yield StreamChunk(content="hi")
        yield StreamChunk(usage={"total_tokens": 1})

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    await asyncio.wait_for(asyncio.gather(*(_drain(router) for _ in range(5))), timeout=10)

    traces = _traces(tmp_path)
    assert [t["ok"] for t in traces] == [True] * 5
    assert all(len(t["attempts"]) == 0 for t in traces)  # attempts list holds failures only
    assert router._status.get("alpha", "big").value == "ready"


@pytest.mark.asyncio
async def test_a_burst_beyond_the_key_cap_queues_instead_of_failing(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_chat(self, route, messages, **kwargs):
        await asyncio.sleep(0.05)
        return {"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    results = await asyncio.wait_for(asyncio.gather(*(
        router.agenerate([{"role": "user", "content": "hi"}], "smart") for _ in range(5))),
        timeout=10)

    assert len(results) == 5
    assert router._status.get("alpha", "big").value == "ready"


def test_in_flight_counts_do_not_survive_a_restart(tmp_path):
    from flexrouter.key_state import KeyStateStore

    store = KeyStateStore(str(tmp_path))
    store.begin_request("alpha", "k1")
    assert KeyStateStore(str(tmp_path)).get("alpha", "k1").active_requests == 0


@pytest.mark.asyncio
async def test_a_stream_given_up_on_while_queued_still_leaves_a_trace(tmp_path, monkeypatch):
    # The playground's deadline cancels a request still waiting for a key.
    # A cancelled generator used to exit without writing its trace, so the
    # request vanished from the Requests page.
    router = _router(tmp_path, monkeypatch)
    router._key_states.begin_request("alpha", "k1")  # the only slot, taken

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_drain(router), timeout=0.3)

    [trace] = _traces(tmp_path)
    assert trace["ok"] is False and trace["asked"]["bucket"] == "smart"
