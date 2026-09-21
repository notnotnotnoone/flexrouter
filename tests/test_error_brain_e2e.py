# tests/test_error_brain_e2e.py
import json

from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def test_a_real_402_through_the_http_surface_is_classified_as_needs_payment_and_persisted(
        tmp_path, monkeypatch):
    async def boom(self, route, messages, **kwargs):
        from flexrouter.client import ProviderError
        raise ProviderError("alpha rejected the request: payment required", status_code=402)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", boom)
    client = _client(tmp_path, monkeypatch)
    client.post("/v1/chat/completions",
               json={"model": "smart", "messages": [{"role": "user", "content": "hi"}]})

    trace_path = tmp_path / "state" / "traces.jsonl"
    entry = json.loads(trace_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["attempts"][0]["verdict"] == "needs_payment"

    brain_path = tmp_path / "state" / "error_brain.json"
    assert brain_path.exists()
    brain = json.loads(brain_path.read_text(encoding="utf-8"))
    assert any(e["verdict"] == "needs_payment" for e in brain.values())
