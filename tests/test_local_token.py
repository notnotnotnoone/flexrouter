from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path, token=None):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
        auth_token=token,
    )


def _client(tmp_path, monkeypatch, token):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path, token))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


SECRET = "flx-AAAABBBBCCCCDDDDEEEEFFFF1234"


def test_no_token_configured_means_no_guard(tmp_path, monkeypatch):
    assert _client(tmp_path, monkeypatch, None).get("/v1/models").status_code == 200


def test_a_configured_token_is_required(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch, SECRET).get("/v1/models")
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["type"] == "invalid_request_error"
    assert err["code"] == "invalid_api_key"


def test_the_right_token_gets_in(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch, SECRET).get(
        "/v1/models", headers={"Authorization": f"Bearer {SECRET}"})
    assert r.status_code == 200


def test_the_wrong_token_does_not(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch, SECRET).get(
        "/v1/models", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_the_rejection_never_repeats_the_token_back(tmp_path, monkeypatch):
    nearly = SECRET[:-4] + "xxxx"
    r = _client(tmp_path, monkeypatch, SECRET).get(
        "/v1/models", headers={"Authorization": f"Bearer {nearly}"})
    assert SECRET not in r.text
    assert SECRET[:-4] not in r.text


def test_chat_is_guarded_too(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch, SECRET).post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


def test_the_dashboard_stays_open_on_loopback(tmp_path, monkeypatch):
    # A browser pointed at the dashboard cannot carry a bearer header, and the
    # whole surface is bound to 127.0.0.1. ADR 0009.
    assert _client(tmp_path, monkeypatch, SECRET).get("/api/status").status_code == 200
