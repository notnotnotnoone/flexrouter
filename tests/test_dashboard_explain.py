"""'Explain errors with AI': the copy-paste prompt (PLAN-V2.3.md Session 12, §8)."""
import re
import json
import os

import pytest
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter import redact, status as st
from flexrouter.app import create_app
from flexrouter.dashboard import explain

MODEL = "llama-3.1-8b-instant"
REPLY = '{"error": {"message": "The model `llama-3.1-8b-instant` does not exist", "code": "model_not_found"}}'


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def _gone(detail=REPLY):
    router = app_module.get_router()
    router._status.set_needs_you("groq", MODEL, "Groq says this model doesn't exist (404)",
                                 kind="model_gone", action=st.REMOVE, status_code=404,
                                 detail=detail)
    os.makedirs(router._cfg.state_dir, exist_ok=True)
    with open(router._cfg.state_dir + "/traces.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "id": "req_1", "at": "2026-09-26T10:00:00.000+00:00Z", "ok": False,
            "attempts": [{"provider": "groq", "model": MODEL, "status": 404,
                          "verdict": "model_gone"}], "answered_by": None}) + "\n")
    return router


def test_nothing_wrong_means_no_prompt(client):
    assert explain.build_prompt(app_module.get_router()) == ""
    assert "Explain errors with AI" not in client.get("/broken").text


def test_the_prompt_has_everything_a_chatbot_needs(client):
    prompt = explain.build_prompt(_gone())
    assert prompt.startswith("I use flexrouter")
    assert f"Problem 1 of 1 ━━  groq/{MODEL}   Needs you" in prompt
    assert "doesn't exist (404)" in prompt
    assert "model_not_found" in prompt            # the full, unmangled reply
    assert 'bucket "low", 60 requests/min' in prompt
    assert "Its last few requests:" in prompt and "HTTP 404" in prompt
    assert "[Use another ID]" in prompt and "[Remove]" in prompt


def test_keys_are_masked_in_the_prompt(client):
    secret = "gsk_abcdefghijklmnopqrstuvwxyz0123456789"
    redact.set_known_secrets([secret])
    try:
        assert secret not in explain.build_prompt(_gone(detail=f"bad key {secret}"))
    finally:
        redact.set_known_secrets([])


def test_one_row_narrows_the_prompt(client):
    _gone()
    router = app_module.get_router()
    assert explain.build_prompt(router, only=f"groq/{MODEL}")
    assert explain.build_prompt(router, only="groq/other") == ""


def test_the_page_has_the_top_button_and_a_row_button(client):
    _gone()
    body = client.get("/status").text
    assert "Explain errors with AI" in body and 'data-copy="#explain-all"' in body
    assert "Explain with AI" in body and re.search(r'id="explain-\d+"', body)
