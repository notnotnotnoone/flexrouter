from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError, StreamChunk
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000, vision=True)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _router(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


async def test_a_400_on_a_streamed_vision_request_records_a_contradicting_failure(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_400(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: malformed image input", status_code=400)
        yield  # pragma: no cover

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", always_400)
    try:
        async for _ in router.agenerate_stream(
                [{"role": "user", "content": "hi"}], "smart", vision=True):
            pass
    except Exception:
        pass

    facts = router._model_facts.get("alpha", "big")
    assert facts.vision.status == "doubted"


async def test_a_successful_streamed_vision_request_records_success(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def ok(self, route, messages, **kwargs):
        yield StreamChunk(content="hi")
        yield StreamChunk(usage={"total_tokens": 1})

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", ok)
    router._model_facts.record_contradicting_failure("alpha", "big", "vision", "req_prior")
    async for _ in router.agenerate_stream([{"role": "user", "content": "hi"}], "smart", vision=True):
        pass

    facts = router._model_facts.get("alpha", "big")
    assert facts.vision.status == "yes"
