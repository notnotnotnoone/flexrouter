import json

from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.exceptions import RouterError
from flexrouter.keys import KeyRecord


def _cfg(tmp_path, key_strategy="round_robin"):
    keys = [
        KeyRecord(id="k1", secret="secret-1", weight=1),
        KeyRecord(id="k2", secret="secret-2", weight=1),
    ]
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(
            base_url="https://alpha.test/v1", api_keys=[r.secret for r in keys],
            keys=keys, key_strategy=key_strategy)},
        state_dir=str(tmp_path / "state"),
        key_concurrency_cap=4,
    )


def _router(tmp_path, monkeypatch, key_strategy="round_robin"):
    monkeypatch.setattr("flexrouter._router.load_config",
                        lambda _p: _cfg(tmp_path, key_strategy))
    return LocalRouter(str(tmp_path / "config.yaml"))


def test_round_robin_alternates_keys_across_calls(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch, "round_robin")
    used_keys = []

    async def fake_chat(self, route, messages, **kwargs):
        used_keys.append(route.api_key)
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    for _ in range(4):
        router.generate([{"role": "user", "content": "hi"}], "smart")
    assert used_keys == ["secret-1", "secret-2", "secret-1", "secret-2"]


def test_an_auth_failure_benches_only_the_used_key(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch, "round_robin")
    calls = {"n": 0}

    async def fake_chat(self, route, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RouterError("Auth failure for provider 'alpha': 401")
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    router.generate([{"role": "user", "content": "hi"}], "smart")

    assert router._key_states.get("alpha", "k1").status == "benched"
    assert router._key_states.get("alpha", "k2").status == "live"
    # The provider itself must NOT be quarantined — k2 still works.
    assert not router._penalties.is_quarantined("alpha", "big")


def test_when_every_key_is_benched_the_provider_is_quarantined(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch, "round_robin")

    async def always_bad_key(self, route, messages, **kwargs):
        raise RouterError("Auth failure for provider 'alpha': 401")

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_bad_key)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    assert router._key_states.get("alpha", "k1").status == "benched"
    assert router._key_states.get("alpha", "k2").status == "benched"
    assert router._penalties.is_quarantined("alpha", "big")


def test_a_cooling_key_is_skipped_but_the_other_key_still_works(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch, "round_robin")
    router._key_states.mark_cooling("alpha", "k1", 9999, "too_fast")
    used_keys = []

    async def fake_chat(self, route, messages, **kwargs):
        used_keys.append(route.api_key)
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    for _ in range(3):
        router.generate([{"role": "user", "content": "hi"}], "smart")
    assert set(used_keys) == {"secret-2"}


def test_key_id_appears_in_the_trace(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch, "round_robin")

    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    router.generate([{"role": "user", "content": "hi"}], "smart")

    trace_path = tmp_path / "state" / "traces.jsonl"
    entry = json.loads(trace_path.read_text(encoding="utf-8").strip())
    assert entry["answered_by"]["key_id"] == "k1"


def test_mark_success_is_called_with_real_usage(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch, "round_robin")

    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 42}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    router.generate([{"role": "user", "content": "hi"}], "smart")
    assert router._key_states.get("alpha", "k1").tokens_today == 42
    assert router._key_states.get("alpha", "k1").requests_today == 1


def test_a_keyless_provider_is_unaffected(tmp_path, monkeypatch):
    cfg = FlexConfig(
        tiers={"smart": [ModelConfig(provider="local", model="m", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"local": ProviderConfig(base_url="http://localhost:11434/v1",
                                           api_keys=[], keys=[])},
        state_dir=str(tmp_path / "state"),
    )
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: cfg)
    router = LocalRouter(str(tmp_path / "config.yaml"))

    async def fake_chat(self, route, messages, **kwargs):
        assert route.api_key == ""
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    router.generate([{"role": "user", "content": "hi"}], "smart")  # must not raise
