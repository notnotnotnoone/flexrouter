import json

from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError, StreamChunk
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
    router = LocalRouter(str(tmp_path / "config.yaml"))
    router._cfg.retry.backoff_seconds = 0  # keep failed-attempt tests fast
    return router


def _traces(tmp_path):
    p = tmp_path / "state" / "traces.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]


async def test_a_404_is_classified_as_model_gone_in_the_streaming_trace(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_404(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: model archived", status_code=404)
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", always_404)
    try:
        async for _ in router.agenerate_stream([{"role": "user", "content": "hi"}], "smart"):
            pass
    except Exception:
        pass

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "model_gone"


async def test_a_failure_after_the_first_delta_is_classified(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_stream(self, route, messages, **kwargs):
        yield StreamChunk(content="par")
        raise ProviderError("alpha/big: connection dropped", status_code=500)

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    try:
        async for _ in router.agenerate_stream([{"role": "user", "content": "hi"}], "smart"):
            pass
    except Exception:
        pass

    t = _traces(tmp_path)[0]
    assert t["attempts"][-1]["verdict"] == "their_end_temporary"


async def test_an_empty_stream_is_classified_as_unknown(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def empty_stream(self, route, messages, **kwargs):
        return
        yield  # pragma: no cover

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", empty_stream)
    try:
        async for _ in router.agenerate_stream([{"role": "user", "content": "hi"}], "smart"):
            pass
    except Exception:
        pass

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "unknown"
