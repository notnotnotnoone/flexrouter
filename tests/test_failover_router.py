import time

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig, RetryConfig


def _cfg_three_models(tmp_path):
    return FlexConfig(
        tiers={"smart": [
            ModelConfig(provider="alpha", model="a", score=99, rpm=60, tpm=60000),
            ModelConfig(provider="alpha", model="b", score=90, rpm=60, tpm=60000),
            ModelConfig(provider="alpha", model="c", score=80, rpm=60, tpm=60000),
        ]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
        retry=RetryConfig(retries=2, backoff_seconds=5),
    )


@pytest.mark.real_clock
@respx.mock
def test_a_fake_upstream_404_then_503_then_ok_answers_fast_and_reports_both_failures(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr("flexrouter._router.load_config",
                        lambda _p: _cfg_three_models(tmp_path))
    respx.post("https://alpha.test/v1/chat/completions").mock(side_effect=[
        httpx.Response(404, json={"error": {"message": "model not found: a"}}),
        httpx.Response(503, json={"error": {"message": "currently overloaded"}}),
        httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }),
    ])
    from flexrouter import app as app_mod
    app_mod.state.router = None
    client = TestClient(app_mod.create_app(str(tmp_path / "config.yaml")))

    started = time.monotonic()
    r = client.post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "hi"


def _cfg_one_model(tmp_path):
    return FlexConfig(
        tiers={"smart": [
            ModelConfig(provider="alpha", model="big", score=99, rpm=60, tpm=60000),
        ]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
        retry=RetryConfig(retries=2, backoff_seconds=5),
    )


@pytest.mark.real_clock
@respx.mock
def test_a_pinned_model_that_is_busy_fails_immediately_with_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config",
                        lambda _p: _cfg_one_model(tmp_path))
    route = respx.post("https://alpha.test/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": {"message": "quota exceeded, retry in 40s"}}))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    client = TestClient(app_mod.create_app(str(tmp_path / "config.yaml")))

    started = time.monotonic()
    r = client.post("/v1/chat/completions", json={
        "model": "alpha/big", "messages": [{"role": "user", "content": "hi"}]})
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert route.call_count == 1  # no fallback retry on a pinned model
    assert "quota exceeded" in r.json()["error"]["message"]


def _cfg_small_then_big_context(tmp_path):
    return FlexConfig(
        tiers={"smart": [
            # Scores far enough apart that _pick()'s 80%-of-best threshold
            # excludes big-ctx from the first selection (random.choice would
            # otherwise sometimes pick it first, since RoutingEngine picks
            # randomly among every model within 80% of the top score) - the
            # test needs small-ctx tried first, deterministically, every run.
            ModelConfig(provider="gamma", model="small-ctx", score=99, rpm=60, tpm=60000,
                        context_window=1000),
            ModelConfig(provider="gamma", model="big-ctx", score=50, rpm=60, tpm=60000,
                        context_window=1_000_000),
        ]},
        providers={"gamma": ProviderConfig(base_url="https://gamma.test/v1",
                                           api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


@respx.mock
def test_message_too_long_fails_over_to_a_bigger_context_model(tmp_path, monkeypatch):
    """Uses the virtual clock (no @real_clock): this test is about routing
    to a bigger-context model, not about timing, and any incidental wait
    for bucket capacity should resolve instantly either way."""
    monkeypatch.setattr("flexrouter._router.load_config",
                        lambda _p: _cfg_small_then_big_context(tmp_path))
    respx.post("https://gamma.test/v1/chat/completions").mock(side_effect=[
        httpx.Response(400, json={
            "error": {"message": "maximum context length is 1000 tokens"}}),
        httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }),
    ])
    from flexrouter import app as app_mod
    app_mod.state.router = None
    client = TestClient(app_mod.create_app(str(tmp_path / "config.yaml")))

    r = client.post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})

    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "hi"
