from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError
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


def test_a_400_on_a_vision_request_records_a_contradicting_failure(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_400(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: malformed image input", status_code=400)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_400)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", vision=True, wait=False)
    except Exception:
        pass

    facts = router._model_facts.get("alpha", "big")
    assert facts.vision.status == "doubted"
    assert facts.vision.strikes == 1


def test_a_non_vision_request_does_not_record_vision_evidence(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_400(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: bad input", status_code=400)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_400)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    facts = router._model_facts.get("alpha", "big")
    assert facts.vision is None


def test_a_successful_vision_request_records_success(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def ok(self, route, messages, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", ok)
    router._model_facts.record_contradicting_failure("alpha", "big", "vision", "req_prior")
    router.generate([{"role": "user", "content": "hi"}], "smart", vision=True)

    facts = router._model_facts.get("alpha", "big")
    assert facts.vision.status == "yes"
    assert facts.vision.strikes == 0


def test_a_500_on_a_vision_request_does_not_count_as_capability_evidence(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_500(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: upstream error", status_code=500)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_500)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", vision=True, wait=False)
    except Exception:
        pass

    facts = router._model_facts.get("alpha", "big")
    assert facts.vision is None  # their_end_temporary is not bad_request/model_gone
