from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.client import ProviderError
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.exceptions import RouterError


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _client(tmp_path, monkeypatch, raises):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    async def boom(self, route, messages, **kwargs):
        raise raises

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", boom)
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def test_the_providers_own_words_survive(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch,
                     RouterError("context length 8192 exceeded by 40 tokens"))
    r = client.post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    assert "context length 8192 exceeded by 40 tokens" in r.json()["error"]["message"]


def test_a_key_echoed_by_the_provider_never_reaches_the_client(tmp_path, monkeypatch):
    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"
    client = _client(tmp_path, monkeypatch,
                     RouterError(f"alpha rejected the key {leaked}"))
    r = client.post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    assert leaked not in r.text
    assert "…1234" in r.json()["error"]["message"]


def test_a_key_echoed_mid_stream_never_reaches_the_client(tmp_path, monkeypatch):
    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"
    client = _client(tmp_path, monkeypatch, ProviderError(f"alpha: {leaked} rejected"))
    with client.stream("POST", "/v1/chat/completions", json={
            "model": "smart", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}) as r:
        body = "".join(r.iter_text())
    assert leaked not in body
