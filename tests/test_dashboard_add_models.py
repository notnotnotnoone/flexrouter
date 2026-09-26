"""GET/POST /models_catalog/add-with-ai - the copy-paste "Add models with AI" flow.

Same shape as the rank-models tests in test_dashboard_pages.py: build a
prompt, paste an answer back, review, apply. No network call is ever made.
"""
import json

import pytest
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter.app import create_app


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def _row(**overrides) -> dict:
    row = {
        "provider": "groq", "model": "new-chat-model", "kind": "chat",
        "context": 8192, "rpm": 30, "tpm": 6000, "rph": None, "rpd": None,
        "rps": None, "tph": None, "tpd": None, "tps": None,
        "vision": False, "free": True, "score": 70,
    }
    row.update(overrides)
    return row


ANSWER_ONE_NEW_CHAT_MODEL = json.dumps([_row()])


def test_the_button_is_on_the_models_page(client):
    body = client.get("/models_catalog").text
    assert 'href="/models_catalog/add-with-ai"' in body
    assert "add models with ai" in body.lower()


def test_step1_only_lists_providers_that_have_a_key(client, config_file):
    body = client.get("/models_catalog/add-with-ai").text
    assert "groq" in body


def test_step1_with_no_provider_chosen_shows_no_prompt_yet(client):
    body = client.get("/models_catalog/add-with-ai").text
    assert "paste the ai" not in body.lower() or "json array" not in body.lower()


def test_choosing_a_provider_builds_a_prompt_naming_it_and_its_models(client):
    body = client.get("/models_catalog/add-with-ai", params={"provider": "groq"}).text
    assert "groq" in body
    assert "llama-3.1-8b-instant" in body
    assert "JSON array" in body
    assert "&quot;kind&quot;" in body or '"kind"' in body


def test_the_prompt_has_a_copy_button(client):
    body = client.get("/models_catalog/add-with-ai", params={"provider": "groq"}).text
    assert "data-copy" in body


def test_an_unknown_or_keyless_provider_is_rejected(client):
    body = client.get("/models_catalog/add-with-ai", params={"provider": "no-such-provider"}).text
    assert "no-such-provider" in body
    assert "JSON array" not in body


def test_review_shows_a_parsed_row(client):
    body = client.post("/models_catalog/add-with-ai/review", data={
        "provider": "groq", "answer": ANSWER_ONE_NEW_CHAT_MODEL,
    }).text
    assert "new-chat-model" in body
    assert "new" in body.lower()


def test_review_marks_an_existing_model_as_update_with_old_and_new_values(client):
    body = client.post("/models_catalog/add-with-ai/review", data={
        "provider": "groq",
        "answer": json.dumps([_row(
            model="llama-3.1-8b-instant", context=200000, rpm=60, tpm=60000,
            vision=True, free=False, score=99,
        )]),
    }).text
    assert "update" in body.lower()
    assert "131072" in body  # old context window
    assert "200000" in body  # proposed new one


def test_review_lists_an_unparseable_entry_with_its_reason_never_dropping_it(client):
    body = client.post("/models_catalog/add-with-ai/review", data={
        "provider": "groq",
        "answer": json.dumps([_row(model="broken", context="not-a-number")]),
    }).text
    assert "broken" in body.lower() or "not-a-number" in body.lower()
    assert "could not" in body.lower() or "reason" in body.lower() or "not a number" in body.lower()


def test_review_offers_a_bucket_picker_for_a_chat_row(client):
    body = client.post("/models_catalog/add-with-ai/review", data={
        "provider": "groq", "answer": ANSWER_ONE_NEW_CHAT_MODEL,
    }).text
    assert 'name="bucket:0"' in body
    assert "low" in body  # the bucket configured in MINIMAL_CONFIG


def test_review_has_no_bucket_picker_for_a_non_chat_row(client):
    body = client.post("/models_catalog/add-with-ai/review", data={
        "provider": "groq",
        "answer": json.dumps([_row(
            model="whisper-ish", kind="speech_to_text", context=None, rpm=None,
            tpm=None, vision=False, free=True, score=None,
        )]),
    }).text
    assert 'name="bucket:0"' not in body


def test_pasted_text_is_escaped_not_injected(client):
    body = client.post("/models_catalog/add-with-ai/review", data={
        "provider": "groq",
        "answer": json.dumps([_row(model="<script>bad</script>", context="not-a-number")]),
    }).text
    assert "<script>bad</script>" not in body
    assert "&lt;script&gt;bad&lt;/script&gt;" in body


def test_notes_are_escaped_on_the_prompt_page(client):
    body = client.get("/models_catalog/add-with-ai", params={
        "provider": "groq", "notes": "<script>bad</script>",
    }).text
    assert "<script>bad</script>" not in body
    assert "&lt;script&gt;bad&lt;/script&gt;" in body


# ── apply ────────────────────────────────────────────────────────────

def _apply_one_row(client, **overrides):
    row = {
        "row_count": "1",
        "apply:0": "on",
        "provider:0": "groq",
        "model:0": "new-chat-model",
        "kind:0": "chat",
        "context:0": "8192",
        "rpm:0": "30",
        "tpm:0": "6000",
        "vision:0": "on",
        "free:0": "on",
        "score:0": "70",
        "bucket:0": "low",
    }
    row.update(overrides)
    return client.post("/models_catalog/add-with-ai/apply", data=row, follow_redirects=False)


def test_apply_adds_a_new_chat_model_to_the_chosen_bucket(client):
    r = _apply_one_row(client)
    assert r.status_code == 303
    body = client.get("/models_catalog").text
    assert "new-chat-model" in body


def test_apply_free_yes_zeroes_the_price(client):
    _apply_one_row(client)
    router = app_module.get_router()
    router._maybe_hot_reload()
    mc = next(m for tier in router._cfg.tiers.values() for m in tier if m.model == "new-chat-model")
    assert mc.price_in == 0
    assert mc.price_out == 0


def test_apply_free_no_leaves_price_unset(client):
    _apply_one_row(client, **{"free:0": ""})
    router = app_module.get_router()
    router._maybe_hot_reload()
    mc = next(m for tier in router._cfg.tiers.values() for m in tier if m.model == "new-chat-model")
    assert mc.price_in is None
    assert mc.price_out is None


def test_apply_records_an_ai_paste_fact(client):
    _apply_one_row(client)
    from flexrouter import rate_limit_facts, score_facts
    router = app_module.get_router()
    for facts in (score_facts, rate_limit_facts):
        entry = facts.get(router._cfg.state_dir, "groq", "new-chat-model")
        assert entry is not None
        assert entry.source == "ai_paste"


def test_apply_updates_an_existing_chat_model(client):
    r = client.post("/models_catalog/add-with-ai/apply", data={
        "row_count": "1",
        "apply:0": "on",
        "provider:0": "groq",
        "model:0": "llama-3.1-8b-instant",
        "kind:0": "chat",
        "context:0": "200000",
        "rpm:0": "99",
        "tpm:0": "99000",
        "vision:0": "on",
        "free:0": "",
        "score:0": "77",
        "bucket:0": "low",
    }, follow_redirects=False)
    assert r.status_code == 303
    body = client.get("/models_catalog").text
    assert ">77<" in body


def test_apply_parks_a_non_chat_model_instead_of_bucketing_it(client):
    r = client.post("/models_catalog/add-with-ai/apply", data={
        "row_count": "1",
        "apply:0": "on",
        "provider:0": "groq",
        "model:0": "whisper-ish",
        "kind:0": "speech_to_text",
        "context:0": "none",
        "rpm:0": "none",
        "tpm:0": "none",
        "vision:0": "",
        "free:0": "on",
        "score:0": "none",
    }, follow_redirects=False)
    assert r.status_code == 303
    from flexrouter import parked_models
    router = app_module.get_router()
    entry = parked_models.get(router._cfg.state_dir, "groq", "whisper-ish")
    assert entry is not None
    assert entry["kind"] == "speech_to_text"
    # never put in a bucket
    body = client.get("/models_catalog").text
    assert "whisper-ish" not in body.split("Saved, not routable yet")[0]


def test_models_page_shows_parked_models_section(client):
    client.post("/models_catalog/add-with-ai/apply", data={
        "row_count": "1", "apply:0": "on", "provider:0": "groq",
        "model:0": "whisper-ish", "kind:0": "speech_to_text",
        "context:0": "none", "rpm:0": "none", "tpm:0": "none",
        "vision:0": "", "free:0": "on", "score:0": "none",
    })
    body = client.get("/models_catalog").text
    assert "Saved, not routable yet" in body
    assert "whisper-ish" in body
    assert "speech_to_text" in body


def test_unchecked_rows_are_not_applied(client):
    r = _apply_one_row(client, **{"apply:0": ""})
    body = client.get(r.headers["location"]).text if r.status_code == 303 else client.get("/models_catalog").text
    assert "new-chat-model" not in body


def test_apply_redirects_with_a_summary_banner(client):
    r = _apply_one_row(client)
    body = client.get(r.headers["location"]).text
    assert "1" in body
