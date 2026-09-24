import pytest
from fastapi.testclient import TestClient

from flexrouter import redact
from flexrouter.app import create_app
from flexrouter.client import ProviderError
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
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
    )


def _client(tmp_path, monkeypatch, raises):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

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
