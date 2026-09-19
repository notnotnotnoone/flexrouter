from fastapi.testclient import TestClient

from flexrouter.app import create_app
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


def _client(tmp_path, monkeypatch, record):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    async def fake_chat(self, route, messages, **kwargs):
        record.append((route.provider, route.model))
        return {"choices": [{"message": {"role": "assistant", "content": "hi"},
                             "finish_reason": "stop"}],
                "usage": {"total_tokens": 3}}

    # respx and TestClient collide in this repo; patch the client method.
    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def test_a_pinned_request_always_reaches_that_model(tmp_path, monkeypatch):
    record: list = []
    client = _client(tmp_path, monkeypatch, record)
    for _ in range(10):
        r = client.post("/v1/chat/completions", json={
            "model": "beta/small", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
    assert set(record) == {("beta", "small")}


def test_a_bucket_request_uses_the_high_scorer(tmp_path, monkeypatch):
    record: list = []
    client = _client(tmp_path, monkeypatch, record)
    client.post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    assert record == [("alpha", "big")]


def test_pinning_a_model_that_is_not_configured_is_a_clear_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, [])
    r = client.post("/v1/chat/completions", json={
        "model": "alpha/ghost", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "model_not_found"
    assert "alpha/ghost" in err["message"]


def test_the_legacy_double_colon_id_now_pins(tmp_path, monkeypatch):
    record: list = []
    client = _client(tmp_path, monkeypatch, record)
    for _ in range(10):
        client.post("/v1/chat/completions", json={
            "model": "smart::beta/small",
            "messages": [{"role": "user", "content": "hi"}]})
    assert set(record) == {("beta", "small")}
