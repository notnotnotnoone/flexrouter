from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={
            "smart": [ModelConfig(provider="alpha", model="big", score=99, rpm=60, tpm=60000)],
            "fast": [ModelConfig(provider="beta", model="small", score=40, rpm=60, tpm=60000)],
        },
        providers={
            "alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"]),
            "beta": ProviderConfig(base_url="https://beta.test/v1", api_keys=["k"]),
        },
        state_dir=str(tmp_path / "state"),
    )


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def test_buckets_are_listed_by_their_plain_names(tmp_path, monkeypatch):
    data = _client(tmp_path, monkeypatch).get("/v1/models").json()["data"]
    ids = [e["id"] for e in data]
    assert ids[:3] == ["smart", "fast", "auto"]
    assert "auto-smart" not in ids


def test_every_model_is_listed_as_provider_slash_model(tmp_path, monkeypatch):
    data = _client(tmp_path, monkeypatch).get("/v1/models").json()["data"]
    ids = [e["id"] for e in data]
    assert "alpha/big" in ids
    assert "beta/small" in ids
    assert not any("::" in i for i in ids)


def test_a_bucket_entry_says_it_is_a_bucket(tmp_path, monkeypatch):
    data = _client(tmp_path, monkeypatch).get("/v1/models").json()["data"]
    smart = next(e for e in data if e["id"] == "smart")
    assert smart["object"] == "model"
    assert smart["flexrouter"]["kind"] == "bucket"
    assert smart["flexrouter"]["models"] == ["alpha/big"]


def test_a_model_entry_carries_its_routing_facts(tmp_path, monkeypatch):
    data = _client(tmp_path, monkeypatch).get("/v1/models").json()["data"]
    big = next(e for e in data if e["id"] == "alpha/big")
    fx = big["flexrouter"]
    assert fx["kind"] == "model"
    assert fx["provider"] == "alpha"
    assert fx["score"] == 99
    assert fx["buckets"] == ["smart"]
    assert fx["status"]["value"] == "ready"


def test_get_one_bucket(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch).get("/v1/models/smart")
    assert r.status_code == 200
    assert r.json()["id"] == "smart"


def test_get_one_model_with_a_slash_in_its_name(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch).get("/v1/models/alpha/big")
    assert r.status_code == 200
    assert r.json()["id"] == "alpha/big"


def test_get_auto(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch).get("/v1/models/auto")
    assert r.status_code == 200
    assert r.json()["flexrouter"]["resolves_to"] == "smart"


def test_get_an_unknown_model_is_a_404_in_the_openai_envelope(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch).get("/v1/models/nope")
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["type"] == "invalid_request_error"
    assert err["code"] == "model_not_found"
    assert "nope" in err["message"]


def test_legacy_ids_still_resolve_on_get(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.get("/v1/models/auto-smart").json()["id"] == "smart"
    assert c.get("/v1/models/smart::alpha/big").json()["id"] == "alpha/big"
