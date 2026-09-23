"""A provider refusing one model must not take its other models down.

Seen live: Mistral answered 403 "labs_not_enabled" for one Labs model, and
llm7 answered 402 "Insufficient balance" for one paid model. Both used to be
read as facts about the whole account, so a single refusal quarantined every
model on the provider, including ones that had just answered.
"""
import httpx
import pytest
import respx

from flexrouter._router import LocalRouter
from flexrouter.client import AsyncClient, ProviderError
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.engine import RouteResult
from flexrouter.keys import KeyRecord

CHAT_URL = "https://alpha.test/v1/chat/completions"
OK = {"choices": [{"message": {"content": "hello"}}], "usage": {"total_tokens": 3}}


def _router(tmp_path, monkeypatch):
    keys = [KeyRecord(id="k1", secret="secret-1")]
    cfg = FlexConfig(
        tiers={"smart": [
            ModelConfig(provider="alpha", model="refused", score=99, rpm=60, tpm=60000,
                        context_window=100_000),
            ModelConfig(provider="alpha", model="fine", score=10, rpm=60, tpm=60000,
                        context_window=100_000),
        ]},
        providers={"alpha": ProviderConfig(
            base_url="https://alpha.test/v1", api_keys=["secret-1"], keys=keys)},
        state_dir=str(tmp_path / "state"),
    )
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: cfg)
    return LocalRouter(str(tmp_path / "config.yaml"))


def _answer_by_model(status, body):
    def handler(request):
        import json
        model = json.loads(request.content)["model"]
        if model == "refused":
            return httpx.Response(status, json=body)
        return httpx.Response(200, json=OK)
    return handler


@pytest.mark.parametrize("status,body", [
    (403, {"message": "Model labs-x is a Labs model.", "type": "labs_not_enabled"}),
    (402, {"error": "Insufficient balance."}),
])
@respx.mock
def test_one_refused_model_leaves_the_key_and_the_other_models_working(
        tmp_path, monkeypatch, status, body):
    respx.post(CHAT_URL).mock(side_effect=_answer_by_model(status, body))
    router = _router(tmp_path, monkeypatch)

    result = router.generate([{"role": "user", "content": "hi"}], tier="smart")

    assert result["choices"][0]["message"]["content"] == "hello"
    assert router._key_states.get("alpha", "k1").status == "live"
    assert router._penalties.is_quarantined("alpha", "refused") is True
    assert router._penalties.is_quarantined("alpha", "fine") is False


@pytest.mark.asyncio
@respx.mock
async def test_a_streamed_403_names_the_reason_and_is_model_level():
    respx.post(CHAT_URL).mock(return_value=httpx.Response(
        403, json={"message": "Labs models are not enabled", "type": "labs_not_enabled"}))
    route = RouteResult(provider="alpha", model="refused", api_key="secret-1",
                        base_url="https://alpha.test/v1", tier="smart")
    async with AsyncClient() as client:
        with pytest.raises(ProviderError, match="Labs models are not enabled") as info:
            async for _ in client.stream_chat(route, [{"role": "user", "content": "hi"}]):
                pass
    assert info.value.status_code == 403


@pytest.mark.asyncio
@respx.mock
async def test_the_whole_provider_body_travels_with_the_error():
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                      "message": "Quota exceeded for metric generate_content_free_tier_requests, limit: 0"}}
    respx.post(CHAT_URL).mock(return_value=httpx.Response(429, json=body))
    route = RouteResult(provider="alpha", model="m", api_key="k",
                        base_url="https://alpha.test/v1", tier="smart")
    from flexrouter.client import RateLimitError
    async with AsyncClient() as client:
        with pytest.raises(RateLimitError) as info:
            async for _ in client.stream_chat(route, [{"role": "user", "content": "hi"}]):
                pass
    assert "limit: 0" in info.value.body and "RESOURCE_EXHAUSTED" in info.value.body
