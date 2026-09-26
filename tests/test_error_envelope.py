import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from flexrouter import redact
from flexrouter.app import create_app
from flexrouter.client import ProviderError, RateLimitError
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig, RetryConfig
from flexrouter.exceptions import RouterError


@pytest.fixture(autouse=True)
def _heuristic_redaction_on():
    """redact_errors defaults to off (2026-09-24): the heuristic rules this
    file exercises are opt-in now, since they had no way to tell a
    provider/model identifier apart from a real credential. These tests
    check that turning the setting on still protects a credential that
    isn't one of flexrouter's own configured keys - still true, just no
    longer the default."""
    redact.set_enabled(True)
    yield
    redact.set_enabled(False)


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
        # The fixture's set_enabled(True) gets overwritten the moment the
        # router builds (LocalRouter._register_known_identifiers reads
        # cfg.redact_errors), so it has to be set here too.
        redact_errors=True,
        # Every test in this file simulates every attempt failing the same
        # way; a real retry count/backoff would only make the suite slow.
        retry=RetryConfig(retries=1, backoff_seconds=0),
    )


def _cfg_two_models(tmp_path):
    """Two models in one bucket, so a failure on the first doesn't leave the
    second attempt starved of a route by the first's own penalty."""
    return FlexConfig(
        tiers={"smart": [
            ModelConfig(provider="alpha", model="big", score=99, rpm=60, tpm=60000),
            ModelConfig(provider="alpha", model="small", score=90, rpm=60, tpm=60000),
        ]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
        redact_errors=True,
        retry=RetryConfig(retries=1, backoff_seconds=0),
    )


def _client(tmp_path, monkeypatch, raises, cfg=None):
    monkeypatch.setattr("flexrouter._router.load_config",
                        lambda _p: (cfg or _cfg(tmp_path)))

    async def boom(self, route, messages, **kwargs):
        raise raises

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", boom)
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def test_the_providers_own_words_survive(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch,
                     RouterError("context length 8192 exceeded by 40 tokens"))
    r = client.post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    assert "context length 8192 exceeded by 40 tokens" in r.json()["error"]["message"]


def test_a_key_echoed_by_the_provider_never_reaches_the_client(tmp_path, monkeypatch):
    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"
    client = _client(tmp_path, monkeypatch,
                     RouterError(f"alpha rejected the key {leaked}"))
    r = client.post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    assert leaked not in r.text
    assert "…1234" in r.json()["error"]["message"]


def test_a_key_echoed_mid_stream_never_reaches_the_client(tmp_path, monkeypatch):
    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"
    client = _client(tmp_path, monkeypatch, ProviderError(f"alpha: {leaked} rejected"))
    with client.stream("POST", "/v1/chat/completions", json={
            "model": "smart", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}) as r:
        body = "".join(r.iter_text())
    assert leaked not in body


# --- request ID + per-attempt detail (flexrouter's own envelope) ---

def test_the_request_id_header_matches_the_body_on_failure(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch,
                     RateLimitError("429 from alpha/big: quota exceeded"))
    r = client.post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    header_id = r.headers["x-flexrouter-request-id"]
    assert header_id.startswith("req_")
    assert r.json()["error"]["flexrouter"]["request_id"] == header_id


def test_every_attempt_is_listed_in_the_error_body(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch,
                     RateLimitError("429 from alpha: quota exceeded"),
                     cfg=_cfg_two_models(tmp_path))
    r = client.post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    attempts = r.json()["error"]["flexrouter"]["attempts"]
    # Session 3 (grill-decisions.md §2): no fixed retry count any more - the
    # router tries every model in the bucket until it gives up around the
    # 30s budget, so both models show up at least once rather than exactly
    # once each.
    assert len(attempts) >= 2
    assert {a["model"] for a in attempts} == {"alpha/big", "alpha/small"}
    for attempt in attempts:
        assert attempt["status"] == 429
        assert "quota exceeded" in attempt["provider_message"]
        assert isinstance(attempt["ms"], int)
        assert isinstance(attempt["waited_ms"], int)
        assert attempt["verdict"]


@respx.mock
def test_a_real_fake_upstream_404_then_402_keeps_the_real_text(tmp_path, monkeypatch):
    """No monkeypatched exception this time: a real HTTP mock plays the two
    models' real responses, exercising the actual client.py status-code
    branches, not a stand-in exception.

    Both responses are permanent statuses (404, 402) so both models
    quarantine outright and the bucket is exhausted deterministically - no
    dependency on the ~30s failover budget or a wait-for-capacity retry."""
    monkeypatch.setattr("flexrouter._router.load_config",
                        lambda _p: _cfg_two_models(tmp_path))
    respx.post("https://alpha.test/v1/chat/completions").mock(side_effect=[
        httpx.Response(404, json={"error": {"message": "model not found: big"}}),
        httpx.Response(402, json={"error": {"message": "insufficient balance"}}),
    ])
    from flexrouter import app as app_mod
    app_mod.state.router = None
    client = TestClient(app_mod.create_app(str(tmp_path / "config.yaml")))

    r = client.post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})

    body = r.json()
    attempts = body["error"]["flexrouter"]["attempts"]
    assert len(attempts) == 2
    by_status = {a["status"]: a["provider_message"] for a in attempts}
    assert "model not found: big" in by_status[404]
    assert "insufficient balance" in by_status[402]
    assert r.headers["x-flexrouter-request-id"] == body["error"]["flexrouter"]["request_id"]


def test_the_request_id_header_is_present_on_success(tmp_path, monkeypatch):
    ok_result = {
        "choices": [{"message": {"role": "assistant", "content": "hi"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    async def chat(self, route, messages, **kwargs):
        return ok_result

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", chat)
    from flexrouter import app as app_mod
    app_mod.state.router = None
    client = TestClient(app_mod.create_app(str(tmp_path / "config.yaml")))

    r = client.post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert r.headers["x-flexrouter-request-id"].startswith("req_")


# --- openai_error's scrub_message parameter ---
#
# Scrubbing exists for text that might carry a provider's or a request's own
# words, which can include an echoed key. A fixed string this codebase wrote
# itself never carries one, and scrubbing it anyway only mangles cue words
# like "Authorization" and "token" that happen to sit near each other in
# plain English. `scrub_message=False` opts a single call out of that; the
# default stays True so a call site that omits the parameter is safe by
# accident, not by luck.

def test_openai_error_scrubs_by_default():
    from flexrouter.app import openai_error

    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"
    resp = openai_error(f"provider said: api_key {leaked} is invalid")
    body = resp.body.decode()
    assert leaked not in body
    assert "…1234" in body


def test_openai_error_leaves_the_message_alone_when_told_to():
    from flexrouter.app import openai_error

    message = ("This flexrouter needs a key. Send it as an Authorization "
               "header: Bearer <your key>. It is the auth_token line in "
               "your settings.")
    resp = openai_error(message, "invalid_request_error", "invalid_api_key",
                        401, scrub_message=False)
    body = resp.body.decode()
    assert message in body
