import json

from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.exceptions import RouterError


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _router(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


def _traces(tmp_path):
    p = tmp_path / "state" / "traces.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]


def test_a_404_is_classified_as_model_gone_in_the_trace(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_404(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: model archived", status_code=404)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_404)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "model_gone"


def test_an_auth_failure_is_classified_as_bad_key(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_401(self, route, messages, **kwargs):
        raise RouterError("Auth failure for provider 'alpha': 401")

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_401)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "bad_key"


def test_a_429_is_classified_as_too_fast(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)
    calls = {"n": 0}

    from flexrouter.client import RateLimitError

    async def rate_limited_then_ok(self, route, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimitError("429 from alpha/big")
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", rate_limited_then_ok)
    router.generate([{"role": "user", "content": "hi"}], "smart")

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "too_fast"


def test_an_unrecognized_status_falls_back_to_unknown(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def status_418(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: I'm a teapot", status_code=418)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", status_418)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "unknown"


def test_error_brain_state_file_is_written(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_402(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: payment required", status_code=402)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_402)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    assert (tmp_path / "state" / "error_brain.json").exists()
