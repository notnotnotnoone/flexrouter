import json
import pytest
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter.app import create_app
from flexrouter.dashboard.render import AREAS


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def test_overview_is_served_at_the_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_every_area_has_a_page(client):
    for slug, _, _, _ in AREAS:
        path = "/" if slug == "overview" else f"/{slug}"
        assert client.get(path).status_code == 200, slug


def test_every_page_carries_the_whole_menu(client):
    body = client.get("/").text
    for _, label, _, _ in AREAS:
        assert label.replace("&", "&amp;").replace("'", "&#x27;") in body


def test_the_stylesheet_is_served(client):
    r = client.get("/static/app.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]


def test_overview_shows_the_real_counts(client):
    body = client.get("/").text
    assert "Providers" in body
    assert "Models" in body


def test_broken_is_no_longer_a_stub(client):
    body = client.get("/broken").text
    assert "not built yet" not in body.lower()
    assert "needs you" in body.lower()


def test_overview_says_nothing_needs_you_when_nothing_does(client):
    body = client.get("/").text
    assert "nothing needs you" in body.lower()


def test_overview_points_at_broken_once_something_does(client):
    router = app_module.get_router()
    router._engine._penalties.quarantine_provider("groq", "key rejected")
    body = client.get("/").text
    assert "need" in body.lower()
    assert 'href="/broken"' in body


def test_a_benched_key_reason_is_escaped_not_injected(client):
    router = app_module.get_router()
    pcfg = router._cfg.providers["groq"]
    router._key_states.mark_benched(
        "groq", pcfg.keys[0].id, "<script>bad</script> said the provider")
    body = client.get("/broken").text
    assert "<script>bad</script>" not in body
    assert "&lt;script&gt;bad&lt;/script&gt;" in body


def test_providers_is_no_longer_a_stub(client):
    body = client.get("/providers").text
    assert "not built yet" not in body.lower()
    assert "groq" in body


def test_providers_list_links_to_the_detail_page(client):
    body = client.get("/providers").text
    assert 'href="/providers/groq"' in body


def test_provider_detail_page_shows_its_keys(client):
    body = client.get("/providers/groq").text
    assert "GROQ_API_KEY" in body
    assert "live" in body.lower()


def test_provider_detail_page_404s_for_an_unknown_provider(client):
    r = client.get("/providers/no-such-provider")
    assert r.status_code == 404


def test_provider_detail_shows_a_test_form_per_key(client):
    body = client.get("/providers/groq").text
    assert 'action="/providers/groq/keys/env:GROQ_API_KEY/test"' in body


def test_testing_a_key_redirects_back_with_the_outcome(client, monkeypatch):
    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"content": "pong"}}]}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    r = client.post("/providers/groq/keys/env:GROQ_API_KEY/test", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/providers/groq?")

    body = client.get(r.headers["location"]).text
    assert "answered" in body.lower()


def test_a_failed_key_test_shows_the_real_reason(client, monkeypatch):
    from flexrouter.exceptions import RouterError

    async def fake_chat(self, route, messages, **kwargs):
        raise RouterError("Auth failure for provider 'groq': 401")

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    r = client.post("/providers/groq/keys/env:GROQ_API_KEY/test", follow_redirects=True)
    assert "401" in r.text


def test_models_is_no_longer_a_stub(client):
    body = client.get("/models").text
    assert "not built yet" not in body.lower()
    assert "llama-3.1-8b-instant" in body


def test_models_shows_a_quarantined_model_as_gone_and_why(client):
    router = app_module.get_router()
    router._engine._penalties.quarantine("groq", "llama-3.1-8b-instant", "model gone")
    body = client.get("/models").text
    assert "model gone" in body


def test_pending_catalogue_says_nothing_pending_when_empty(client):
    router = app_module.get_router()
    from flexrouter.store import write_json
    write_json(router._cfg.state_dir + "/catalog_pending.json", {})
    body = client.get("/models").text
    assert "nothing pending" in body.lower()


def test_pending_catalogue_shows_what_a_refresh_found(client):
    router = app_module.get_router()
    from flexrouter.store import write_json
    write_json(router._cfg.state_dir + "/catalog_pending.json", {
        "groq": {"checked_at": "now", "appeared": [{"model": "brand-new-model"}],
                 "vanished": [], "changed": []},
    })
    body = client.get("/models").text
    assert "brand-new-model" in body


def test_buckets_is_no_longer_a_stub(client):
    body = client.get("/buckets").text
    assert "not built yet" not in body.lower()
    assert "low" in body
    assert "llama-3.1-8b-instant" in body


def test_buckets_shows_the_would_answer_verdict(client):
    body = client.get("/buckets").text
    assert "would answer" in body.lower()


def test_buckets_shows_why_a_model_is_skipped(client):
    router = app_module.get_router()
    router._engine._penalties.quarantine("groq", "llama-3.1-8b-instant", "model gone")
    body = client.get("/buckets").text
    assert "model gone" in body


def test_requests_is_no_longer_a_stub(client):
    body = client.get("/requests").text
    assert "not built yet" not in body.lower()
    assert "nothing has come through" in body.lower()


def test_requests_shows_a_written_trace(client):
    import json, os
    router = app_module.get_router()
    os.makedirs(router._cfg.state_dir, exist_ok=True)
    with open(router._cfg.state_dir + "/traces.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "id": "req_1", "at": "2026-09-21T00:00:00Z",
            "asked": {"bucket": "low"}, "skipped": [],
            "answered_by": {"provider": "groq", "model": "llama-3.1-8b-instant"},
            "tokens": {"in": 10, "out": 5}, "ms_total": 123, "ok": True,
        }) + "\n")
    body = client.get("/requests").text
    assert "groq/llama-3.1-8b-instant" in body


def test_brain_is_no_longer_a_stub(client):
    body = client.get("/brain").text
    assert "not built yet" not in body.lower()
    assert "nothing learned yet" in body.lower()


def test_brain_shows_a_learned_entry_and_flags_it_for_review(client):
    router = app_module.get_router()
    router._error_brain.classify("a completely unrecognized provider message", None)
    body = client.get("/brain").text
    assert "needs review" in body.lower()


def test_a_brain_sample_is_escaped_not_injected(client):
    router = app_module.get_router()
    router._error_brain.classify("<script>bad</script> unrecognized text", None)
    body = client.get("/brain").text
    assert "<script>bad</script>" not in body
    assert "&lt;script&gt;bad&lt;/script&gt;" in body


def test_allowance_is_no_longer_a_stub(client):
    body = client.get("/allowance").text
    assert "not built yet" not in body.lower()
    assert "groq" in body


def test_a_key_test_message_is_escaped_not_injected(client, monkeypatch):
    async def fake_chat(self, route, messages, **kwargs):
        from flexrouter.client import ProviderError
        raise ProviderError("<script>bad</script>", status_code=500)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    r = client.post("/providers/groq/keys/env:GROQ_API_KEY/test", follow_redirects=True)
    assert "<script>bad</script>" not in r.text


def test_an_unknown_path_is_a_404_not_the_dashboard(client):
    r = client.get("/no-such-page")
    assert r.status_code == 404


def test_the_openai_surface_still_works(client):
    assert client.get("/v1/models").status_code == 200


def test_the_private_api_still_works(client):
    assert client.get("/api/providers").status_code == 200


def test_a_healthy_provider_shows_state_ok(client):
    # The colored state marker is the first thing the owner's eye goes to;
    # the config_file fixture's one provider has a live, unquarantined key,
    # so it must render as "ok".
    body = client.get("/").text
    assert "state-ok" in body


def test_a_quarantined_provider_shows_state_bad_and_why(client):
    # Same router the app is already using - not a second LocalRouter - so
    # the quarantine is visible to the request the test client makes.
    router = app_module.get_router()
    router._engine._penalties.quarantine_provider("groq", "key rejected")
    body = client.get("/").text
    assert "state-bad" in body
    assert "key rejected" in body


def test_a_provider_name_is_escaped_not_injected(client, config_file):
    # Guards the whole rendering approach: a provider named with markup must
    # not reach the browser as markup.
    import yaml
    raw = yaml.safe_load(config_file.read_text())
    raw["providers"]["<script>bad</script>"] = {
        "base_url": "https://example.invalid/v1",
        "api_keys": [{"env": "GROQ_API_KEY"}],
    }
    config_file.write_text(yaml.dump(raw))
    with TestClient(create_app(str(config_file))) as c:
        body = c.get("/").text
    assert "<script>bad</script>" not in body


def test_a_provider_base_url_is_escaped_not_injected(client, config_file):
    # A provider's base address is externally sourced (the owner types it,
    # or it comes from a saved config) the same way its name is - same
    # pattern, same guard.
    import yaml
    raw = yaml.safe_load(config_file.read_text())
    raw["providers"]["groq"]["base_url"] = "https://x/<script>bad</script>"
    config_file.write_text(yaml.dump(raw))
    with TestClient(create_app(str(config_file))) as c:
        body = c.get("/").text
    assert "<script>bad</script>" not in body
    assert "&lt;script&gt;bad&lt;/script&gt;" in body


def test_a_quarantine_reason_is_escaped_not_injected(client):
    # The reason text originates from a provider's own error response, so it
    # is exactly as untrusted as a provider's name or address.
    router = app_module.get_router()
    router._engine._penalties.quarantine_provider(
        "groq", "<script>bad</script> said the provider")
    body = client.get("/").text
    assert "<script>bad</script>" not in body
    assert "&lt;script&gt;bad&lt;/script&gt;" in body


def test_nothing_still_tells_the_owner_to_build_the_front_end():
    from pathlib import Path
    import flexrouter
    root = Path(flexrouter.__file__).parent
    offenders = [
        path.name
        for path in root.rglob("*.py")
        if "npm run build" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


# --- Stage 8 sub-plan 7: Settings and writing back -------------------------


def test_settings_is_no_longer_a_stub(client):
    body = client.get("/settings").text
    assert "not built yet" not in body.lower()
    assert "window_seconds" in body


def test_settings_field_can_be_saved_and_shows_up_as_changed_here(client):
    client.post("/settings/window_seconds", data={"value": "999"}, follow_redirects=False)
    body = client.get("/settings").text
    assert "999" in body
    assert "changed here" in body.lower()


def test_settings_field_can_be_put_back(client):
    client.post("/settings/window_seconds", data={"value": "999"})
    client.post("/settings/window_seconds/clear")
    body = client.get("/settings").text
    assert "60" in body  # back to the config file's own value


def test_settings_rejects_a_field_outside_the_allowed_set(client):
    r = client.post("/settings/auth_token", data={"value": "x"}, follow_redirects=False)
    assert r.status_code == 303
    body = client.get(r.headers["location"]).text
    assert "cannot be changed here" in body.lower()


def test_settings_rejects_a_non_numeric_value_for_a_numeric_field(client):
    r = client.post("/settings/window_seconds", data={"value": "not-a-number"},
                    follow_redirects=False)
    body = client.get(r.headers["location"]).text
    assert "state-bad" in body


def test_service_key_starts_out_not_set(client):
    body = client.get("/settings").text
    assert "not set" in body.lower()
    assert "artificial analysis" in body.lower()


def test_service_key_can_be_saved_and_is_masked(client):
    client.post("/settings/keys/aa", data={"secret": "aa-secretvalue1234"})
    body = client.get("/settings").text
    assert "aa-secretvalue1234" not in body
    assert "…1234" in body


def test_service_key_can_be_removed(client):
    client.post("/settings/keys/aa", data={"secret": "aa-secretvalue1234"})
    client.post("/settings/keys/aa/remove")
    body = client.get("/settings").text
    assert "…1234" not in body


def test_service_key_rejects_an_unknown_service(client):
    r = client.post("/settings/keys/nosuchservice", data={"secret": "x"},
                    follow_redirects=False)
    body = client.get(r.headers["location"]).text
    assert "unknown service" in body.lower()


def test_service_key_rejects_an_empty_secret(client):
    r = client.post("/settings/keys/aa", data={"secret": ""}, follow_redirects=False)
    body = client.get(r.headers["location"]).text
    assert "state-bad" in body


def test_saving_a_service_key_replaces_the_old_one_not_appends(client):
    client.post("/settings/keys/aa", data={"secret": "first-keyaaaa"})
    client.post("/settings/keys/aa", data={"secret": "second-keybbbb"})
    body = client.get("/settings").text
    assert "…aaaa" not in body
    assert "…bbbb" in body


def test_add_a_provider(client):
    client.post("/providers", data={"name": "newprov", "base_url": "https://x/v1"})
    body = client.get("/providers").text
    assert "newprov" in body


def test_add_a_provider_without_a_base_url_fails_cleanly(client):
    r = client.post("/providers", data={"name": "newprov"}, follow_redirects=False)
    body = client.get(r.headers["location"]).text
    assert "state-bad" in body


def test_onboarding_a_provider_key_and_model_together(client):
    r = client.post("/providers", data={
        "name": "onboardprov", "base_url": "https://onboard/v1",
        "key_secret": "sk-abcd1234", "key_label": "main",
    }, follow_redirects=False)
    assert r.headers["location"].startswith("/providers/onboardprov?")

    detail_body = client.get(r.headers["location"]).text
    assert "and its key added" in detail_body
    assert "sk-abcd1234" not in detail_body
    assert "…1234" in detail_body

    client.post("/providers/onboardprov/models", data={
        "bucket": "low", "model": "onboard-model", "score": "80",
        "rpm": "10", "tpm": "1000",
    })
    models_body = client.get("/models").text
    assert "onboard-model" in models_body
    buckets_body = client.get("/buckets").text
    assert "onboard-model" in buckets_body


def test_onboarding_a_provider_with_no_key_and_no_models_still_creates_it(client):
    client.post("/providers", data={
        "name": "bareprov", "base_url": "https://bare/v1",
    })
    body = client.get("/providers").text
    assert "bareprov" in body
    detail_body = client.get("/providers/bareprov").text
    assert "No keys configured" in detail_body


def test_onboarding_a_malformed_model_row_does_not_discard_the_provider_or_key(client):
    client.post("/providers", data={
        "name": "partialprov", "base_url": "https://partial/v1",
        "key_secret": "sk-keepme0",
    })
    r = client.post("/providers/partialprov/models", data={
        "bucket": "low", "model": "half-baked", "score": "80", "rpm": "10",
        # tpm deliberately missing
    }, follow_redirects=False)
    body = client.get(r.headers["location"]).text
    assert "state-bad" in body
    assert "partialprov/half-baked" in body
    # the provider and its key both survived the failed model row
    assert "sk-keepme0" not in body
    assert "…pme0" in body
    models_body = client.get("/models").text
    assert "half-baked" not in models_body


def test_edit_a_provider_and_see_the_new_value(client):
    client.post("/providers/groq/edit", data={"base_url": "http://localhost:9999/v1"})
    body = client.get("/providers/groq").text
    assert "http://localhost:9999/v1" in body


def test_put_a_provider_edit_back(client):
    client.post("/providers/groq/edit", data={"base_url": "http://localhost:9999/v1"})
    client.post("/providers/groq/clear")
    body = client.get("/providers/groq").text
    assert "https://api.groq.com/openai/v1" in body


def test_add_a_bucket(client):
    client.post("/buckets", data={"name": "experimental"})
    body = client.get("/buckets").text
    assert "experimental" in body


def test_add_a_bucket_without_a_name_fails_cleanly(client):
    r = client.post("/buckets", data={"name": ""}, follow_redirects=False)
    body = client.get(r.headers["location"]).text
    assert "state-bad" in body


def test_add_a_model_to_a_bucket(client):
    client.post("/buckets/low/models", data={
        "provider": "groq", "model": "brand-new", "score": "70",
        "rpm": "30", "tpm": "6000",
    })
    body = client.get("/buckets").text
    assert "brand-new" in body


def test_add_a_model_with_missing_required_fields_fails_cleanly(client):
    r = client.post("/buckets/low/models", data={"provider": "groq", "model": "brand-new"},
                    follow_redirects=False)
    body = client.get(r.headers["location"]).text
    assert "state-bad" in body


def test_edit_a_model_and_see_the_new_score(client):
    client.post("/models/groq/llama-3.1-8b-instant", data={
        "action": "edit", "score": "42", "rpm": "60", "tpm": "60000",
        "context_window": "131072",
    })
    body = client.get("/models").text
    assert ">42<" in body


def test_rank_page_lists_current_models_in_the_prompt(client):
    body = client.get("/models/rank").text
    assert "groq" in body
    assert "llama-3.1-8b-instant" in body
    assert "current score 85" in body
    assert "provider | model | proposed_score" in body


def test_rank_proposal_shows_current_vs_proposed(client):
    body = client.post("/models/rank/proposal", data={
        "answer": "groq | llama-3.1-8b-instant | 99",
    }).text
    assert ">85<" in body
    assert ">99<" in body


def test_rank_apply_updates_the_score(client):
    client.post("/models/rank/apply", data={
        "apply:groq/llama-3.1-8b-instant": "on",
        "score:groq/llama-3.1-8b-instant": "99",
    })
    body = client.get("/models").text
    assert ">99<" in body


def test_rank_apply_with_nothing_checked_changes_nothing(client):
    r = client.post("/models/rank/apply", data={}, follow_redirects=False)
    body = client.get(r.headers["location"]).text
    assert "nothing was checked" in body.lower()
    assert ">85<" in client.get("/models").text


def test_rank_skips_a_score_pinned_by_hand(client):
    client.post("/models/groq/llama-3.1-8b-instant", data={
        "action": "edit", "score": "42", "rpm": "60", "tpm": "60000",
        "context_window": "131072",
    })
    body = client.post("/models/rank/proposal", data={
        "answer": "groq | llama-3.1-8b-instant | 99",
    }).text
    assert "pinned by hand" in body.lower()
    assert "groq/llama-3.1-8b-instant" in body
    assert ">99<" not in body


def test_rank_answer_for_an_unconfigured_model_is_ignored(client):
    body = client.post("/models/rank/proposal", data={
        "answer": "nosuchprovider | nosuchmodel | 50",
    }).text
    assert "nothing to propose" in body.lower()


def test_disable_a_model_removes_it_from_the_live_table(client):
    client.post("/models/groq/llama-3.1-8b-instant", data={"action": "disable"})
    body = client.get("/models").text
    assert "llama-3.1-8b-instant" not in body.split("Disabled models")[0]
    assert "groq/llama-3.1-8b-instant" in body


def test_a_disabled_model_can_be_put_back(client):
    client.post("/models/groq/llama-3.1-8b-instant", data={"action": "disable"})
    client.post("/models/groq/llama-3.1-8b-instant", data={"action": "clear"})
    body = client.get("/models").text
    assert "No disabled models" in body


def test_accept_an_appeared_pending_model(client):
    router = app_module.get_router()
    from flexrouter.store import write_json
    write_json(router._cfg.state_dir + "/catalog_pending.json", {
        "groq": {"checked_at": "now",
                 "appeared": [{"model": "brand-new", "score": 70, "rpm": 30, "tpm": 6000}],
                 "vanished": [], "changed": []},
    })
    client.post("/models/pending/groq/appeared/brand-new",
               data={"action": "accept", "bucket": "low"})
    body = client.get("/models").text
    assert "brand-new" in body
    assert "brand-new appeared" not in body


def test_reject_an_appeared_pending_model(client):
    router = app_module.get_router()
    from flexrouter.store import write_json
    write_json(router._cfg.state_dir + "/catalog_pending.json", {
        "groq": {"checked_at": "now",
                 "appeared": [{"model": "brand-new", "score": 70, "rpm": 30, "tpm": 6000}],
                 "vanished": [], "changed": []},
    })
    client.post("/models/pending/groq/appeared/brand-new", data={"action": "reject"})
    body = client.get("/models").text
    assert "brand-new appeared" not in body


def test_accept_a_vanished_pending_model_disables_it(client):
    router = app_module.get_router()
    from flexrouter.store import write_json
    write_json(router._cfg.state_dir + "/catalog_pending.json", {
        "groq": {"checked_at": "now", "appeared": [],
                 "vanished": ["llama-3.1-8b-instant"], "changed": []},
    })
    client.post("/models/pending/groq/vanished/llama-3.1-8b-instant", data={"action": "accept"})
    body = client.get("/models").text
    assert "groq/llama-3.1-8b-instant" in body.split("Pending catalogue")[0]


def test_accept_a_changed_pending_field(client):
    router = app_module.get_router()
    from flexrouter.store import write_json
    write_json(router._cfg.state_dir + "/catalog_pending.json", {
        "groq": {"checked_at": "now", "appeared": [], "vanished": [],
                 "changed": [{"model": "llama-3.1-8b-instant", "field": "rpm",
                             "old": 60, "new": 999}]},
    })
    client.post("/models/pending/groq/changed/llama-3.1-8b-instant",
               data={"action": "accept", "field": "rpm"})
    body = client.get("/models").text
    assert 'name="rpm" value="999"' in body


def test_toast_trigger_is_an_htmx_event():
    from flexrouter.dashboard.pages import toast_trigger
    h = toast_trigger("Key saved")
    assert json.loads(h["HX-Trigger"]) == {"toast": {"message": "Key saved", "kind": "ok"}}


def test_a_success_message_becomes_a_toast_seed(client):
    body = client.get("/providers?ok=1&message=Saved").text
    assert 'class="toast-seed"' in body and "Saved" in body
    assert 'class="state-bad"' not in body


def test_a_failure_message_stays_on_the_page(client):
    body = client.get("/providers?ok=0&message=Nope").text
    assert 'class="state-bad">Nope<' in body
