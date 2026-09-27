"""The Get started card and Test all (PLAN-V2.3.md Session 18, §12-§13)."""
import json

import pytest
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter.app import create_app
from flexrouter.dashboard import quickstart


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


@pytest.fixture
def fake_hi(monkeypatch):
    seen = {}

    async def fake_chat(self, route, messages, **kwargs):
        seen["max_tokens"] = kwargs.get("max_tokens")
        seen["model"] = route.model
        return {"choices": [{"message": {"content": "hello"}}]}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    return seen


def _write_trace(**over):
    import os
    state = app_module.get_router()._cfg.state_dir
    os.makedirs(state, exist_ok=True)
    t = {"id": "req_1", "at": "2026-09-26T10:00:00.000Z", "asked": {"bucket": "low"},
         "skipped": [], "attempts": [], "answered_by": {"provider": "groq", "model": "m"},
         "tokens": {"in": 1, "out": 1}, "ms_total": 90, "ok": True}
    t.update(over)
    with open(os.path.join(state, "traces.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(t) + "\n")


def _steps():
    router = app_module.get_router()
    return {s.n: s for s in quickstart.steps(router, "http://localhost:4891/v1")}


def test_a_configured_provider_and_model_tick_steps_one_and_three(client):
    s = _steps()
    assert s[1].done and s[3].done
    assert not s[2].done and not s[4].done and not s[5].done


def test_the_card_is_on_top_of_overview_with_every_step(client):
    body = client.get("/").text
    assert "Get started" in body and "2 of 5 done" in body
    for title in ("Add a provider", "Paste its key", "Add models with AI",
                  "Say hi to every model", "Point your app at flexrouter"):
        assert title in body
    assert body.index("Get started") < body.index("verdict")


def test_hiding_it_keeps_it_hidden_while_a_model_works(client):
    router = app_module.get_router()
    client.post("/settings/show_quickstart", data={"value": "false"})
    router._maybe_hot_reload()
    assert quickstart.should_show(router, model_works=True) is False
    # ...but it comes back by itself when nothing works (§13).
    assert quickstart.should_show(router, model_works=False) is True


def test_test_all_says_hi_with_room_to_think_and_ticks_step_four(client, fake_hi):
    r = client.post("/test-model/groq/llama-3.1-8b-instant")
    assert r.json()["ok"] is True
    assert fake_hi["max_tokens"] == 512
    s = _steps()
    assert s[4].done and s[4].sub.startswith("1 of 1 answered")


def test_a_failed_hi_says_why(client, monkeypatch):
    from flexrouter.client import ProviderError

    async def boom(self, route, messages, **kwargs):
        raise ProviderError("402: balance is empty", status_code=402)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", boom)
    d = client.post("/test-model/groq/llama-3.1-8b-instant").json()
    assert d["ok"] is False and "balance" in d["message"]
    four = _steps()[4]
    assert not four.done and "None of the 1 answered" in four.sub


def test_a_passing_key_test_ticks_step_two(client, fake_hi):
    router = app_module.get_router()
    key_id = router._cfg.providers["groq"].keys[0].id
    client.post(f"/providers/groq/keys/{key_id}/test")
    assert _steps()[2].done


def test_the_playground_does_not_count_as_your_app(client):
    _write_trace(asked={"bucket": "low", "client": "playground"})
    assert not _steps()[5].done
    _write_trace(id="req_2")
    assert _steps()[5].done


def test_step_five_gives_a_copyable_python_and_curl(client):
    body = client.get("/").text
    assert "Copy Python" in body and "Copy curl" in body
    assert "localhost:" in body and "/v1" in body


def test_test_all_is_on_the_status_page_too(client):
    body = client.get("/status").text
    assert 'data-test-all="#status-list"' in body
    assert 'data-model="llama-3.1-8b-instant"' in body
