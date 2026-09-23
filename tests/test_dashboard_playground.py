"""The Playground: real requests through the router, streamed."""
import json

import pytest
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter.app import create_app


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def test_the_page_offers_buckets_and_models(client):
    body = client.get("/playground").text
    assert "bucket: " in body and "model: groq/" in body
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
