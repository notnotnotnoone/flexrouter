import json

from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path, **extra_models):
    models = [ModelConfig(provider="alpha", model="big", score=99,
                          rpm=60, tpm=60000, context_window=100_000,
                          vision=True)]
    return FlexConfig(
        tiers={"smart": models},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _router(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


def _traces(tmp_path):
    p = tmp_path / "state" / "traces.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]


def test_a_successful_request_writes_one_trace(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    router.generate([{"role": "user", "content": "hi"}], "smart")

    traces = _traces(tmp_path)
    assert len(traces) == 1
    t = traces[0]
    assert t["id"].startswith("req_")
    assert t["ok"] is True
    assert t["asked"] == {"bucket": "smart", "stream": False, "needs": [],
                          "approx_input_tokens": 0}
    assert t["answered_by"] == {"provider": "alpha", "model": "big", "key_id": None}
    assert t["tokens"] == {"in": 5, "out": 2}
    assert t["cost_usd"] == 0.0
    assert t["ms_to_first_token"] is None
    assert isinstance(t["ms_total"], int)
    assert t["skipped"] == []
    assert t["attempts"] == []


def test_needs_lists_vision_when_the_request_asked_for_it(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    router.generate([{"role": "user", "content": "hi"}], "smart", vision=True)

    assert _traces(tmp_path)[0]["asked"]["needs"] == ["vision"]


def test_a_provider_failure_is_recorded_as_an_attempt(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)
    calls = {"n": 0}

    async def fake_chat(self, route, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError("alpha/big: upstream fell over", status_code=500)
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    router.generate([{"role": "user", "content": "hi"}], "smart")

    t = _traces(tmp_path)[0]
    assert len(t["attempts"]) == 1
    a = t["attempts"][0]
    assert a["n"] == 1
    assert a["provider"] == "alpha"
    assert a["model"] == "big"
    assert a["status"] == 500
    assert "upstream fell over" in a["provider_message"]
    assert a["key_id"] is None
    assert isinstance(a["ms"], int)
    assert t["ok"] is True  # the retry succeeded


def test_a_model_that_needs_more_context_than_the_prompt_shows_up_in_skipped(tmp_path, monkeypatch):
    small = ModelConfig(provider="beta", model="tiny", score=50,
                        rpm=60, tpm=60000, context_window=10)
    big = ModelConfig(provider="alpha", model="big", score=99,
                      rpm=60, tpm=60000, context_window=100_000)
    cfg = FlexConfig(
        tiers={"smart": [big, small]},
        providers={
            "alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"]),
            "beta": ProviderConfig(base_url="https://beta.test/v1", api_keys=["k"]),
        },
        state_dir=str(tmp_path / "state"),
        hooks=["estimate_tokens"],
    )
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: cfg)
    router = LocalRouter(str(tmp_path / "config.yaml"))

    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    # estimated_tokens set high enough via a long message to exceed `tiny`'s window.
    router.generate([{"role": "user", "content": "x" * 100}], "smart")

    t = _traces(tmp_path)[0]
    skipped = {(s["provider"], s["model"]): s for s in t["skipped"]}
    assert ("beta", "tiny") in skipped
    assert skipped[("beta", "tiny")]["reason"] == "context_too_small"
    assert ("alpha", "big") not in skipped


def test_a_request_that_exhausts_every_retry_still_writes_a_trace(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_fails(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: gone", status_code=404)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_fails)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    traces = _traces(tmp_path)
    assert len(traces) == 1
    assert traces[0]["ok"] is False
    assert traces[0]["answered_by"] is None
