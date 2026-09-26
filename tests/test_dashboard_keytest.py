import pytest

from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError, RateLimitError
from flexrouter.dashboard import keytest
from flexrouter.exceptions import RouterError


@pytest.fixture
def router(config_file):
    r = LocalRouter(str(config_file))
    yield r
    r.close()


@pytest.mark.asyncio
async def test_unknown_provider_reports_a_message(router):
    result = await keytest.test_key(router, "no-such-provider", "whatever")
    assert result.ok is False
    assert "no such provider" in result.message


@pytest.mark.asyncio
async def test_unknown_key_reports_a_message(router):
    result = await keytest.test_key(router, "groq", "no-such-key")
    assert result.ok is False
    assert "no such key" in result.message


@pytest.mark.asyncio
async def test_a_working_key_reports_ok(router, monkeypatch):
    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"content": "pong"}}]}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    key_id = router._cfg.providers["groq"].keys[0].id

    result = await keytest.test_key(router, "groq", key_id)
    assert result.ok is True
    assert "llama-3.1-8b-instant" in result.message


@pytest.mark.asyncio
async def test_gives_a_reasoning_model_enough_room_to_answer(router, monkeypatch):
    """§13: max_tokens=1 makes a reasoning model spend its whole budget
    thinking and never answer at all, which looked like a dead key."""
    seen = {}

    async def fake_chat(self, route, messages, **kwargs):
        seen["max_tokens"] = kwargs.get("max_tokens")
        return {"choices": [{"message": {"content": "pong"}}]}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    key_id = router._cfg.providers["groq"].keys[0].id

    await keytest.test_key(router, "groq", key_id)
    assert seen["max_tokens"] == 512


@pytest.mark.asyncio
async def test_a_rejected_key_reports_the_failure_not_an_exception(router, monkeypatch):
    async def fake_chat(self, route, messages, **kwargs):
        raise RouterError("Auth failure for provider 'groq': 401")

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    key_id = router._cfg.providers["groq"].keys[0].id

    result = await keytest.test_key(router, "groq", key_id)
    assert result.ok is False
    assert "401" in result.message


@pytest.mark.asyncio
async def test_a_rate_limited_key_says_so_without_condemning_the_key(router, monkeypatch):
    async def fake_chat(self, route, messages, **kwargs):
        raise RateLimitError("429 from groq/llama-3.1-8b-instant")

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    key_id = router._cfg.providers["groq"].keys[0].id

    result = await keytest.test_key(router, "groq", key_id)
    assert result.ok is False
    assert "rate limited" in result.message


@pytest.mark.asyncio
async def test_the_cerebras_case_a_valid_key_that_fails_on_a_real_request(router, monkeypatch):
    """The whole reason this module exists: a key that a model-list lookup
    would call fine, but that fails on the request that actually matters."""
    async def fake_chat(self, route, messages, **kwargs):
        raise ProviderError("groq/llama-3.1-8b-instant: payment required", status_code=402)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    key_id = router._cfg.providers["groq"].keys[0].id

    result = await keytest.test_key(router, "groq", key_id)
    assert result.ok is False
    assert result.status_code == 402


@pytest.mark.asyncio
async def test_no_model_configured_for_the_key_is_reported_not_crashed(router, monkeypatch):
    from flexrouter.keys import KeyRecord
    pcfg = router._cfg.providers["groq"]
    narrow_key = KeyRecord(id="narrow", secret="secret", allow_models=["no-match-*"])
    pcfg.keys.append(narrow_key)

    result = await keytest.test_key(router, "groq", "narrow")
    assert result.ok is False
    assert "no model" in result.message
