# tests/test_trace_e2e.py
import json

import pytest
from fastapi.testclient import TestClient

from flexrouter import redact
from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


@pytest.fixture(autouse=True)
def _heuristic_redaction_on():
    """redact_errors defaults to off (2026-09-24) - see test_error_envelope.py."""
    redact.set_enabled(True)
    yield
    redact.set_enabled(False)


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["sk-live-not-a-real-key-ABCDEFGH"])},
        state_dir=str(tmp_path / "state"),
        # The fixture's set_enabled(True) gets overwritten the moment the
        # router builds (LocalRouter._register_known_identifiers reads
        # cfg.redact_errors), so it has to be set here too.
        redact_errors=True,
    )


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def test_a_real_request_through_the_http_surface_leaves_a_trace_with_no_secret_in_it(
        tmp_path, monkeypatch):
    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"

    async def boom_then_ok(self, route, messages, **kwargs):
        raise __import__("flexrouter.client", fromlist=["ProviderError"]).ProviderError(
            f"alpha rejected the key {leaked}", status_code=401)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", boom_then_ok)
    client = _client(tmp_path, monkeypatch)
    client.post("/v1/chat/completions",
               json={"model": "smart", "messages": [{"role": "user", "content": "hi"}]})

    trace_path = tmp_path / "state" / "traces.jsonl"
    assert trace_path.exists()
    on_disk = trace_path.read_text(encoding="utf-8")
    assert leaked not in on_disk
    entry = json.loads(on_disk.strip().splitlines()[-1])
    assert entry["ok"] is False
