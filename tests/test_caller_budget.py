import pytest

from flexrouter._router import LocalRouter
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.exceptions import RouterBusy


def _cfg_one_model(tmp_path):
    return FlexConfig(
        tiers={"smart": [
            ModelConfig(provider="alpha", model="big", score=99, rpm=60, tpm=60000),
        ]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _cfg_two_models(tmp_path):
    return FlexConfig(
        tiers={"smart": [
            ModelConfig(provider="alpha", model="big", score=99, rpm=60, tpm=60000),
            ModelConfig(provider="alpha", model="small", score=40, rpm=60, tpm=60000),
        ]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _length_no_content_response():
    return {"choices": [{"message": {"role": "assistant", "content": ""},
                         "finish_reason": "length"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}}


def test_pinned_model_that_exhausts_its_budget_thinking_gets_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg_one_model(tmp_path))
    router = LocalRouter(str(tmp_path / "config.yaml"))

    async def fake_chat(self, route, messages, **kwargs):
        return _length_no_content_response()

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    penalized = []
    monkeypatch.setattr(router._engine, "penalize", lambda p, m: penalized.append((p, m)))

    with pytest.raises(RouterBusy) as exc_info:
        router.generate([{"role": "user", "content": "hi"}], "alpha/big", max_tokens=5)

    assert "raise max_tokens" in str(exc_info.value)
    assert "5" in str(exc_info.value)
    assert penalized == []


def test_a_budget_exhausted_model_in_a_bucket_fails_over_without_penalty(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg_two_models(tmp_path))
    router = LocalRouter(str(tmp_path / "config.yaml"))

    calls = {"n": 0}

    async def fake_chat(self, route, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _length_no_content_response()
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 3}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    penalized = []
    monkeypatch.setattr(router._engine, "penalize", lambda p, m: penalized.append((p, m)))

    result = router.generate([{"role": "user", "content": "hi"}], "smart", max_tokens=5)

    assert result["choices"][0]["message"]["content"] == "hi"
    assert penalized == []
