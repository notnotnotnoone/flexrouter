# tests/test_model_facts_e2e.py
#
# `vision` is not part of the OpenAI chat-completions wire format: the HTTP
# handler (`flexrouter/app.py::chat_completions`) reads `model`, `messages`,
# `stream`, and whatever's in `_PASSTHROUGH` (temperature, tools, etc.) from
# the request body, and `vision` is not among them - there is no way for an
# HTTP client to make `router.agenerate(..., vision=True)` happen today. That
# is a fact about pre-existing code, not something this stage is meant to fix
# (this stage is observability-only; wiring a request-level vision signal
# into the wire protocol is its own future feature). So this proves the real,
# reachable path instead: `LocalRouter` directly, the same surface Task 2/3's
# own tests (`tests/test_model_facts_agenerate.py`,
# `tests/test_model_facts_agenerate_stream.py`) already exercise - but here
# end to end, through the on-disk `state/model_facts.json` file rather than
# the in-memory store, proving the whole chain: a real (mocked) provider 400,
# classified `bad_request` by the error brain, recorded as a vision-capability
# strike, and persisted to disk in the shape `ModelFactsStore` promises.
import json

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


def test_a_real_400_on_a_vision_request_is_recorded_as_evidence_on_disk(
        tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def boom(self, route, messages, **kwargs):
        raise ProviderError("alpha rejected the image", status_code=400)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", boom)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart",
                        vision=True, wait=False)
    except Exception:
        pass

    facts_path = tmp_path / "state" / "model_facts.json"
    assert facts_path.exists()
    on_disk = json.loads(facts_path.read_text(encoding="utf-8"))
    entry = on_disk["alpha/big"]
    assert entry["vision"]["status"] == "doubted"
    assert entry["vision"]["strikes"] == 1
    assert entry["vision"]["source"] == "guessed"

    # The store held in memory by the router agrees with what actually made
    # it to disk - the whole point of this being an e2e test rather than a
    # repeat of Task 2/3's own unit tests.
    facts = router._model_facts.get("alpha", "big")
    assert facts.vision.status == "doubted"
    assert facts.vision.strikes == 1
