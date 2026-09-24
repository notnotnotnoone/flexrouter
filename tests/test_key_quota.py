"""A key's own rps/rph/rpd/tps/tph/tpd cap (KeyRecord.quotas), independent
of whatever the model it's calling allows - e.g. a provider that gives each
key its own daily request allowance regardless of which model it's used
for. Once one key hits its cap, routing rotates to another live key for the
same model instead of failing or double-spending the capped key's quota.
"""
import json

import pytest

from flexrouter._router import LocalRouter
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.keys import KeyRecord


def _cfg(tmp_path, quotas):
    keys = [
        KeyRecord(id="k1", secret="secret-1", quotas=quotas),
        KeyRecord(id="k2", secret="secret-2"),
    ]
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=600, tpm=600000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(
            base_url="https://alpha.test/v1", api_keys=[r.secret for r in keys], keys=keys)},
        state_dir=str(tmp_path / "state"),
        key_concurrency_cap=5,
    )


def _router(tmp_path, monkeypatch, quotas):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path, quotas))
    return LocalRouter(str(tmp_path / "config.yaml"))


def _traces(tmp_path):
    lines = (tmp_path / "state" / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.mark.asyncio
async def test_a_key_at_its_own_request_cap_is_skipped_in_favour_of_another_key(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch, quotas={"rpd": 1})

    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)

    used_keys = []
    for _ in range(2):
        route, key_id = await router._route_with_key("smart", 10, False, None)
        assert route is not None
        used_keys.append(key_id)
        await router._client.chat(route, [{"role": "user", "content": "hi"}])
        router._quota_tracker.record_key(route.provider, key_id, 1)

    assert used_keys == ["k1", "k2"]


@pytest.mark.asyncio
async def test_a_key_s_own_cap_does_not_throttle_a_key_with_no_cap_of_its_own(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch, quotas={"rpd": 1})

    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)

    # Exhaust k1's cap.
    route, key_id = await router._route_with_key("smart", 10, False, None)
    assert key_id == "k1"
    router._quota_tracker.record_key(route.provider, key_id, 1)

    # k2 has no cap of its own, so it keeps answering indefinitely.
    for _ in range(5):
        route, key_id = await router._route_with_key("smart", 10, False, None)
        assert key_id == "k2"
