# tests/test_key_selection_e2e.py
import json

from fastapi.testclient import TestClient

from flexrouter.app import create_app
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
    )


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def test_a_real_401_through_the_http_surface_benches_one_key_and_the_next_request_still_works(
        tmp_path, monkeypatch):
    calls = {"n": 0}

    async def fake_chat(self, route, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RouterError("Auth failure for provider 'alpha': 401")
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    client = _client(tmp_path, monkeypatch)

    r1 = client.post("/v1/chat/completions",
                     json={"model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    assert r1.status_code == 200

    key_state_path = tmp_path / "state" / "key_state.json"
    on_disk = json.loads(key_state_path.read_text(encoding="utf-8"))
    statuses = {k: v["status"] for k, v in on_disk.items()}
    assert statuses.get("alpha:k1") == "needs_you"
    assert statuses.get("alpha:k2", "ready") == "ready"
