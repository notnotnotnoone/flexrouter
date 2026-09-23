"""The Playground: real requests through the router, streamed."""
import json

import pytest
import yaml
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter.app import create_app


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


@pytest.fixture
def two_provider_config_file(tmp_path, minimal_config):
    """Two buckets, two providers, so Compare has something to fan out to."""
    cfg = minimal_config
    cfg["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    cfg["tiers"]["high"] = [
        {"provider": "openai", "model": "gpt-4o-mini", "score": 90,
         "rpm": 60, "tpm": 60000, "context_window": 128000},
    ]
    cfg["providers"]["openai"] = {
        "base_url": "https://api.openai.com/v1",
        "api_keys": [{"env": "OPENAI_API_KEY"}],
    }
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    return p


@pytest.fixture
def compare_client(two_provider_config_file):
    with TestClient(create_app(str(two_provider_config_file))) as c:
        yield c


def test_the_page_offers_buckets_and_models(client):
    body = client.get("/playground").text
    assert '<optgroup label="Buckets">' in body and '<optgroup label="groq">' in body
    assert 'value="groq/' in body
    assert 'id="pg-input"' in body


def test_the_playground_is_in_the_menu(client):
    assert 'href="/playground"' in client.get("/").text


def test_an_empty_conversation_is_refused(client):
    r = client.post("/playground/chat", json={"target": "auto", "messages": []})
    assert r.status_code == 400


def test_an_unknown_target_is_a_404(client):
    r = client.post("/playground/chat", json={"target": "nosuchbucket",
                                              "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404


def test_bad_numbers_are_refused(client):
    r = client.post("/playground/chat", json={"target": "auto", "temperature": "hot",
                                              "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 400


def test_a_turn_streams_and_ends_with_who_answered(client, monkeypatch):
    """The model call itself is faked at the router; everything between the
    browser and the router is real."""
    from flexrouter._router import DeltaEvent, DoneEvent

    async def fake_stream(self, messages, tier, **kw):
        yield DeltaEvent("hello")
        yield DoneEvent(result={"usage": {"prompt_tokens": 3, "completion_tokens": 1}})

    router = app_module.get_router()
    monkeypatch.setattr(type(router), "agenerate_stream", fake_stream)
    r = client.post("/playground/chat", json={"target": "auto",
                                              "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "hello" in r.text


def _sent_kwargs(client, monkeypatch, **settings) -> dict:
    from flexrouter._router import DeltaEvent, DoneEvent

    seen = {}

    async def fake_stream(self, messages, tier, **kw):
        seen.update(kw)
        yield DeltaEvent("hello")
        yield DoneEvent(result={"usage": {}})

    monkeypatch.setattr(type(app_module.get_router()), "agenerate_stream", fake_stream)
    client.post("/playground/chat", json={"target": "auto", **settings,
                                          "messages": [{"role": "user", "content": "hi"}]})
    return seen


def test_penalties_left_at_zero_are_not_sent(client, monkeypatch):
    # Gemini's OpenAI endpoint rejects the whole request over an unknown
    # "frequency_penalty" field, and 0 is every provider's default anyway.
    sent = _sent_kwargs(client, monkeypatch, frequency_penalty="0", presence_penalty="0")
    assert "frequency_penalty" not in sent and "presence_penalty" not in sent


def test_a_penalty_someone_set_is_sent(client, monkeypatch):
    sent = _sent_kwargs(client, monkeypatch, frequency_penalty="0.5", presence_penalty="-1")
    assert sent["frequency_penalty"] == 0.5 and sent["presence_penalty"] == -1.0


def test_the_page_offers_a_compare_group(compare_client):
    body = compare_client.get("/playground").text
    assert '<optgroup label="Compare">' in body
    assert 'value="compare:all"' in body
    assert 'value="compare:bucket:high"' in body
    assert 'value="compare:provider:openai"' in body


def test_an_unknown_compare_bucket_is_a_404(compare_client):
    r = compare_client.post("/playground/chat", json={"target": "compare:bucket:nosuch",
                                                       "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404


def test_an_unknown_compare_provider_is_a_404(compare_client):
    r = compare_client.post("/playground/chat", json={"target": "compare:provider:nosuch",
                                                       "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404


def test_compare_all_fans_out_and_tags_each_reply_by_model(compare_client, monkeypatch):
    """Each concurrent target answers with its own tier name so the test can
    tell the two apart; the merged stream must carry both, plus one `targets`
    event listing both up front and one `flexrouter` event per target."""
    from flexrouter._router import DeltaEvent, DoneEvent

    async def fake_stream(self, messages, tier, **kw):
        yield DeltaEvent(f"reply from {tier}")
        yield DoneEvent(result={"usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    router = app_module.get_router()
    monkeypatch.setattr(type(router), "agenerate_stream", fake_stream)
    r = compare_client.post("/playground/chat", json={"target": "compare:all",
                                                       "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    text = r.text
    assert "event: targets" in text
    assert "groq/llama-3.1-8b-instant" in text
    assert "openai/gpt-4o-mini" in text
    assert "reply from groq/llama-3.1-8b-instant" in text
    assert "reply from openai/gpt-4o-mini" in text
    assert text.count("event: flexrouter") == 2
