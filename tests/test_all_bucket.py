"""The built-in `all` bucket: every model in every bucket, as one bucket.

An app that wants each of the owner's models once (Agora's one vote per
model) asks `all` and excludes the models it has had; flexrouter still picks
and fails over, across everything rather than inside one bucket (ADR 0025).
"""
import json

import httpx
import respx
from fastapi.testclient import TestClient

from flexrouter._router import LocalRouter
from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.wire import ALL_BUCKET, Target, resolve


def _cfg(tmp_path, own_all=False):
    tiers = {
        "smart": [
            ModelConfig(provider="alpha", model="big", score=99, rpm=60, tpm=60000),
            ModelConfig(provider="alpha", model="mid", score=70, rpm=60, tpm=60000),
        ],
        "fast": [
            ModelConfig(provider="beta", model="small", score=40, rpm=60, tpm=60000),
            # In two buckets; `all` holds it once.
            ModelConfig(provider="alpha", model="mid", score=70, rpm=60, tpm=60000),
        ],
    }
    if own_all:
        tiers["all"] = [ModelConfig(provider="beta", model="small", score=40, rpm=60, tpm=60000)]
    return FlexConfig(
        tiers=tiers,
        providers={
            "alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"]),
            "beta": ProviderConfig(base_url="https://beta.test/v1", api_keys=["k"]),
        },
        state_dir=str(tmp_path / "state"),
    )


def _echo_model(request):
    body = json.loads(request.content)
    return httpx.Response(200, json={
        "choices": [{"message": {"content": body["model"]}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}})


def _client(tmp_path, monkeypatch, own_all=False):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path, own_all))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def _ask(client, exclude=()):
    headers = {"X-Flexrouter-Exclude": ",".join(exclude)} if exclude else {}
    return client.post("/v1/chat/completions", headers=headers, json={
        "model": "all", "messages": [{"role": "user", "content": "hi"}]})


def test_all_is_a_bucket_name_resolve_accepts():
    assert resolve(Target("bucket", ALL_BUCKET), ["smart", "fast"], "smart") == "all"


@respx.mock
def test_asking_all_reaches_a_model_outside_the_best_bucket(tmp_path, monkeypatch):
    respx.post("https://alpha.test/v1/chat/completions").mock(side_effect=_echo_model)
    respx.post("https://beta.test/v1/chat/completions").mock(side_effect=_echo_model)
    client = _client(tmp_path, monkeypatch)

    res = _ask(client, exclude=["alpha/big", "alpha/mid"])

    assert res.status_code == 200
    assert res.json()["choices"][0]["message"]["content"] == "small"


@respx.mock
def test_each_model_answers_all_once_when_the_caller_excludes_what_it_had(tmp_path, monkeypatch):
    respx.post("https://alpha.test/v1/chat/completions").mock(side_effect=_echo_model)
    respx.post("https://beta.test/v1/chat/completions").mock(side_effect=_echo_model)
    client = _client(tmp_path, monkeypatch)

    ids = {"big": "alpha/big", "mid": "alpha/mid", "small": "beta/small"}
    had: list[str] = []
    for _ in range(3):
        res = _ask(client, exclude=had)
        assert res.status_code == 200
        had.append(ids[res.json()["choices"][0]["message"]["content"]])

    assert sorted(had) == ["alpha/big", "alpha/mid", "beta/small"]
    assert _ask(client, exclude=had).status_code == 503


def test_v1_models_lists_all_with_every_model_once(tmp_path, monkeypatch):
    data = _client(tmp_path, monkeypatch).get("/v1/models").json()["data"]
    ids = [e["id"] for e in data]
    assert ids[:4] == ["smart", "fast", "auto", "all"]
    entry = data[3]["flexrouter"]
    assert entry["kind"] == "bucket"
    assert entry["models"] == ["alpha/big", "alpha/mid", "beta/small"]


def test_a_bucket_the_owner_named_all_wins(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path, own_all=True))
    router = LocalRouter(str(tmp_path / "config.yaml"))
    assert router._engine_for("all") is router._engine
    route = router._engine_for("all").select("all", 10, False)
    assert (route.provider, route.model) == ("beta", "small")


def test_all_follows_the_buckets_on_reload(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    router = LocalRouter(str(tmp_path / "config.yaml"))
    new = _cfg(tmp_path)
    new.tiers["fast"].append(ModelConfig(provider="beta", model="extra", score=30, rpm=60, tpm=60000))
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: new)
    router.reload()
    names = [f"{m.provider}/{m.model}" for m in router._engine_for("all")._cfg.tiers["all"]]
    assert "beta/extra" in names


def test_all_counts_requests_against_the_same_windows_as_the_buckets(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    router = LocalRouter(str(tmp_path / "config.yaml"))
    router._engine.record_request("alpha", "big", 100)
    assert router._engine_for("all")._windows["alpha/big"] is router._engine._windows["alpha/big"]
