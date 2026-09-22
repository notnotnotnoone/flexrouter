import os
import pytest
import yaml

from flexrouter._router import LocalRouter
from flexrouter.dashboard import facts


@pytest.fixture
def router(config_file):
    r = LocalRouter(str(config_file))
    yield r
    r.close()


@pytest.fixture
def router_no_credentials(tmp_path, minimal_config):
    """Router with a provider that has no credentials."""
    cfg = minimal_config
    cfg["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    cfg["providers"]["groq"]["api_keys"] = []  # No credentials
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    # Ensure GROQ_API_KEY is not set
    old_key = os.environ.pop("GROQ_API_KEY", None)
    try:
        r = LocalRouter(str(p))
        yield r
        r.close()
    finally:
        if old_key is not None:
            os.environ["GROQ_API_KEY"] = old_key


def test_provider_summaries_lists_every_configured_provider(router):
    names = [p.name for p in facts.provider_summaries(router)]
    assert names == ["groq"]


def test_provider_summary_reports_base_url_and_model_count(router):
    summary = facts.provider_summaries(router)[0]
    assert summary.base_url == "https://api.groq.com/openai/v1"
    assert summary.models_total == 1


def test_an_env_sourced_key_still_gets_a_record_and_counts_as_live(router):
    pcfg = router._cfg.providers["groq"]
    # Environment-sourced credentials get a KeyRecord with id="env:GROQ_API_KEY"
    assert pcfg.keys, "env-sourced credentials should have keys"
    summary = facts.provider_summaries(router)[0]
    assert summary.key_count == 1
    assert summary.keys_live == 1
    assert summary.keys_cooling == 0
    assert summary.keys_parked == 0


def test_healthy_provider_reads_as_ok(router):
    assert facts.provider_summaries(router)[0].state == "ok"


def test_a_provider_whose_only_key_is_resting_reads_as_warn(router):
    pcfg = router._cfg.providers["groq"]
    key_id = pcfg.keys[0].id
    router._key_states.mark_cooling("groq", key_id, 30, "went too fast")
    summary = facts.provider_summaries(router)[0]
    assert summary.keys_cooling == 1
    assert summary.keys_live == 0
    # A resting key comes back on its own, so this is amber, not red.
    assert summary.state == "warn"


def test_a_key_marked_benched_reports_as_parked(router):
    pcfg = router._cfg.providers["groq"]
    key_id = pcfg.keys[0].id
    router._key_states.mark_benched("groq", key_id, "bad key")
    summary = facts.provider_summaries(router)[0]
    assert summary.keys_parked == 1
    assert summary.state == "bad"


def test_a_provider_with_one_benched_and_one_cooling_key_reads_as_warn(router):
    from flexrouter.keys import KeyRecord
    pcfg = router._cfg.providers["groq"]
    # Add a second key to the provider
    second_key = KeyRecord(id="second-key", secret="secret", label="second", source="test")
    pcfg.keys.append(second_key)

    # Bench the first key, cool the second
    router._key_states.mark_benched("groq", pcfg.keys[0].id, "bad key")
    router._key_states.mark_cooling("groq", pcfg.keys[1].id, 30, "went too fast")

    summary = facts.provider_summaries(router)[0]
    assert summary.keys_parked == 1
    assert summary.keys_cooling == 1
    assert summary.keys_live == 0
    # One key will recover (cooling), so this is warn, not bad
    assert summary.state == "warn"


def test_a_key_marked_cooling_reports_as_live_after_cooldown_expires(router):
    import time
    pcfg = router._cfg.providers["groq"]
    key_id = pcfg.keys[0].id
    now = time.time()
    # Mark cooling; mark_cooling enforces minimum 30 second cooldown
    router._key_states.mark_cooling("groq", key_id, 1, "went too fast", now=now)
    # Call with time past the 30-second cooldown
    summary = facts.provider_summaries(router, now=now + 31)[0]
    assert summary.keys_cooling == 0
    assert summary.keys_live == 1
    assert summary.state == "ok"


def test_provider_summaries_does_not_write_when_a_cooldown_has_expired(router):
    """Rendering the Overview must never write to disk.

    is_available() would flip an expired cooling key back to "live" and
    persist that, which means a plain GET rewrites key_state.json (and, on
    Windows, shells out to icacls via harden()) on every render. Reading
    the expiry directly instead must leave the file untouched.
    """
    import time
    pcfg = router._cfg.providers["groq"]
    key_id = pcfg.keys[0].id
    now = time.time()
    router._key_states.mark_cooling("groq", key_id, 1, "went too fast", now=now)

    path = router._key_states._path
    before = path.read_text(encoding="utf-8")

    facts.provider_summaries(router, now=now + 31)

    assert path.read_text(encoding="utf-8") == before


def test_provider_with_no_credentials_reads_as_bad(router_no_credentials):
    summary = facts.provider_summaries(router_no_credentials)[0]
    assert summary.key_count == 0
    assert summary.state == "bad"


def test_quarantined_provider_reads_as_bad_and_carries_its_reason(router):
    router._engine._penalties.quarantine_provider("groq", "key rejected")
    summary = facts.provider_summaries(router)[0]
    assert summary.state == "bad"
    assert summary.quarantined is True
    assert summary.quarantine_reason == "key rejected"


def test_overview_counts_providers_by_state(router):
    out = facts.overview(router)
    assert out["providers"] == {"total": 1, "ok": 1, "warn": 0, "bad": 0}


def test_overview_counts_keys(router):
    out = facts.overview(router)
    assert out["keys"]["total"] == 1
    assert out["keys"]["live"] == 1


def test_overview_counts_models_and_how_many_are_reachable(router):
    out = facts.overview(router)
    assert out["models"]["total"] == 1
    assert out["models"]["available"] == 1


def test_overview_counts_a_quarantined_model_as_unavailable(router):
    router._engine._penalties.quarantine("groq", "llama-3.1-8b-instant", "gone")
    out = facts.overview(router)
    assert out["models"]["total"] == 1
    assert out["models"]["available"] == 0


def test_overview_reports_what_has_been_learned(router):
    out = facts.overview(router)
    assert out["learned"]["error_kinds"] == 0
    assert out["learned"]["awaiting_you"] == 0
    assert out["learned"]["models_with_facts"] == 0


def test_overview_reports_what_has_been_learned_once_something_has(router):
    # Real recording paths, not hand-built store entries: classify() is how
    # ErrorBrain learns a new error kind, and with the router's NullDecider
    # any genuinely unrecognized text comes back at confidence 0.0, which is
    # below the review threshold, so it also counts as "awaiting you".
    router._error_brain.classify("a completely unrecognized provider message", None)
    # record_discovered() is the genuine way a catalogue refresh teaches the
    # store a fact about a model (here: its context window).
    router._model_facts.record_discovered("groq", "llama-3.1-8b-instant", 131072)

    out = facts.overview(router)
    assert out["learned"]["error_kinds"] == 1
    assert out["learned"]["awaiting_you"] == 1
    assert out["learned"]["models_with_facts"] == 1


def test_overview_names_the_buckets_and_the_state_directory(router):
    out = facts.overview(router)
    assert out["service"]["buckets"] == ["low"]
    assert out["service"]["state_dir"].endswith(".flexrouter")


def test_overview_never_returns_a_secret(router):
    import json
    blob = json.dumps(facts.overview(router), default=str)
    assert "test-key" not in blob


def test_broken_is_empty_when_nothing_is_wrong(router):
    out = facts.broken(router)
    assert out["needs_you"] == []
    assert out["handling_itself"] == []


def test_a_benched_key_needs_you(router):
    pcfg = router._cfg.providers["groq"]
    key_id = pcfg.keys[0].id
    router._key_states.mark_benched("groq", key_id, "bad key")
    out = facts.broken(router)
    assert len(out["needs_you"]) == 1
    item = out["needs_you"][0]
    assert item.kind == "key_benched"
    assert item.provider == "groq"
    assert item.reason == "bad key"
    assert out["handling_itself"] == []


def test_a_cooling_key_is_handled_by_the_service(router):
    pcfg = router._cfg.providers["groq"]
    key_id = pcfg.keys[0].id
    router._key_states.mark_cooling("groq", key_id, 30, "went too fast")
    out = facts.broken(router)
    assert out["needs_you"] == []
    assert len(out["handling_itself"]) == 1
    item = out["handling_itself"][0]
    assert item.kind == "key_cooling"
    assert item.reason == "went too fast"


def test_a_cooling_key_stops_appearing_once_its_cooldown_has_passed(router):
    import time
    pcfg = router._cfg.providers["groq"]
    key_id = pcfg.keys[0].id
    now = time.time()
    router._key_states.mark_cooling("groq", key_id, 1, "went too fast", now=now)
    out = facts.broken(router, now=now + 31)
    assert out["handling_itself"] == []


def test_a_provider_wide_quarantine_needs_you(router):
    router._engine._penalties.quarantine_provider("groq", "key rejected")
    out = facts.broken(router)
    assert len(out["needs_you"]) == 1
    item = out["needs_you"][0]
    assert item.kind == "provider_down"
    assert item.provider == "groq"
    assert item.reason == "key rejected"
    assert out["handling_itself"] == []


def test_a_single_model_quarantine_is_handled_by_the_service(router):
    # Distinct from a provider-wide quarantine: only one model is affected,
    # the router keeps routing to the rest of the provider on its own, and
    # the quarantine expires by itself - nothing for the owner to do.
    router._engine._penalties.quarantine("groq", "llama-3.1-8b-instant", "model gone")
    out = facts.broken(router)
    assert out["needs_you"] == []
    assert len(out["handling_itself"]) == 1
    item = out["handling_itself"][0]
    assert item.kind == "model_set_aside"
    assert item.detail == "llama-3.1-8b-instant"
    assert item.reason == "model gone"


def test_a_provider_wide_quarantine_does_not_also_double_count_its_models(router):
    router._engine._penalties.quarantine_provider("groq", "key rejected")
    out = facts.broken(router)
    assert len(out["needs_you"]) == 1
    assert out["handling_itself"] == []


def test_an_unclear_error_needs_you(router):
    router._error_brain.classify("a completely unrecognized provider message", None)
    out = facts.broken(router)
    assert len(out["needs_you"]) == 1
    item = out["needs_you"][0]
    assert item.kind == "unclear_error"


def test_broken_never_returns_a_secret(router):
    import json
    pcfg = router._cfg.providers["groq"]
    router._key_states.mark_benched("groq", pcfg.keys[0].id, "bad key")
    blob = json.dumps(facts.broken(router), default=lambda o: o.__dict__)
    assert "test-key" not in blob


def test_overview_reports_how_many_things_need_you(router):
    assert facts.overview(router)["needs_you"] == 0
    router._engine._penalties.quarantine_provider("groq", "key rejected")
    assert facts.overview(router)["needs_you"] == 1


def test_provider_detail_is_none_for_an_unknown_provider(router):
    assert facts.provider_detail(router, "no-such-provider") is None


def test_provider_detail_reports_the_provider_shape(router):
    detail = facts.provider_detail(router, "groq")
    assert detail.name == "groq"
    assert detail.base_url == "https://api.groq.com/openai/v1"
    assert detail.state == "ok"
    assert detail.quarantined is False
    assert detail.configured_models == ["llama-3.1-8b-instant"]
    assert detail.models_alive == ["llama-3.1-8b-instant"]
    assert detail.models_gone == []


def test_provider_detail_lists_key_readings(router):
    detail = facts.provider_detail(router, "groq")
    assert len(detail.keys) == 1
    k = detail.keys[0]
    assert k.id == "env:GROQ_API_KEY"
    assert k.label == "GROQ_API_KEY"
    assert k.masked.endswith("-key")
    assert "test-key" not in k.masked
    assert k.status == "live"
    assert k.source == "env"


def test_provider_detail_shows_a_quarantined_model_as_gone(router):
    router._engine._penalties.quarantine("groq", "llama-3.1-8b-instant", "model gone")
    detail = facts.provider_detail(router, "groq")
    assert detail.models_gone == ["llama-3.1-8b-instant"]
    assert detail.models_alive == []


def test_provider_detail_never_returns_a_secret(router):
    import json
    blob = json.dumps(facts.provider_detail(router, "groq"), default=lambda o: o.__dict__)
    assert "test-key" not in blob


def test_models_lists_every_configured_model_once(router):
    rows = facts.models(router)
    assert len(rows) == 1
    row = rows[0]
    assert row.provider == "groq"
    assert row.model == "llama-3.1-8b-instant"
    assert row.buckets == ["low"]
    assert row.state == "available"


def test_a_model_in_two_buckets_lists_both(router):
    from flexrouter.config import ModelConfig
    router._cfg.tiers["high"] = [
        ModelConfig(provider="groq", model="llama-3.1-8b-instant",
                   score=90, rpm=60, tpm=60000)
    ]
    rows = facts.models(router)
    assert len(rows) == 1
    assert sorted(rows[0].buckets) == ["high", "low"]


def test_a_quarantined_model_reads_as_gone_with_its_reason(router):
    router._engine._penalties.quarantine("groq", "llama-3.1-8b-instant", "model gone")
    row = facts.models(router)[0]
    assert row.state == "gone"
    assert row.why == "model gone"


def test_models_carries_learned_capability_facts(router):
    router._model_facts.record_success("groq", "llama-3.1-8b-instant", "vision")
    row = facts.models(router)[0]
    assert row.vision is not None
    assert row.vision.status == "yes"


def test_models_reports_unknown_capabilities_as_none(router):
    row = facts.models(router)[0]
    assert row.vision is None
    assert row.tools is None
    assert row.reasoning is None


def test_pending_catalogue_is_empty_when_nothing_has_run(router):
    from flexrouter.store import write_json
    # The router's own startup catalogue refresh (a real network call) may
    # have already written a bucket here - overwrite it with the shape a
    # not-yet-run refresh leaves, so this test is about pending_catalogue()'s
    # own default and not about what a live provider answered with today.
    write_json(router._cfg.state_dir + "/catalog_pending.json", {})
    assert facts.pending_catalogue(router) == {}


def test_buckets_lists_every_bucket(router):
    out = facts.buckets(router)
    assert [b.name for b in out] == ["low"]
    assert len(out[0].models) == 1
    row = out[0].models[0]
    assert row.provider == "groq"
    assert row.available is True
    assert row.in_the_running is True


def test_buckets_sorts_models_by_score_descending(router):
    from flexrouter.config import ModelConfig
    router._cfg.tiers["low"].append(
        ModelConfig(provider="groq", model="a-lower-score-model",
                   score=10, rpm=60, tpm=60000)
    )
    out = facts.buckets(router)
    scores = [row.score for row in out[0].models]
    assert scores == sorted(scores, reverse=True)


def test_a_quarantined_model_is_not_in_the_running_and_says_why(router):
    router._engine._penalties.quarantine("groq", "llama-3.1-8b-instant", "model gone")
    row = facts.buckets(router)[0].models[0]
    assert row.available is False
    assert row.in_the_running is False
    assert row.detail == "model gone"


def test_an_available_model_below_the_pick_threshold_is_not_in_the_running(router):
    from flexrouter.config import ModelConfig
    router._cfg.tiers["low"] = [
        ModelConfig(provider="groq", model="llama-3.1-8b-instant", score=99,
                   rpm=60, tpm=60000, context_window=131072),
        ModelConfig(provider="groq", model="second-model", score=40,
                   rpm=60, tpm=60000, context_window=131072),
    ]
    rows = {row.model: row for row in facts.buckets(router)[0].models}
    assert rows["llama-3.1-8b-instant"].in_the_running is True
    assert rows["second-model"].available is True
    assert rows["second-model"].in_the_running is False


def test_recent_requests_is_empty_before_any_traffic(router):
    assert facts.recent_requests(router) == []


def test_recent_requests_reads_a_written_trace(router):
    import json
    path = router._cfg.state_dir + "/traces.jsonl"
    import os
    os.makedirs(router._cfg.state_dir, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "id": "req_1", "at": "2026-09-21T00:00:00Z",
            "asked": {"bucket": "low"}, "skipped": [],
            "answered_by": {"provider": "groq", "model": "llama-3.1-8b-instant"},
            "tokens": {"in": 10, "out": 5}, "ms_total": 123, "ok": True,
        }) + "\n")
    rows = facts.recent_requests(router)
    assert len(rows) == 1
    assert rows[0].id == "req_1"
    assert rows[0].answered_by == {"provider": "groq", "model": "llama-3.1-8b-instant"}
    assert rows[0].tokens_in == 10


def test_recent_requests_puts_the_newest_first(router):
    import json, os
    path = router._cfg.state_dir + "/traces.jsonl"
    os.makedirs(router._cfg.state_dir, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for i in range(3):
            f.write(json.dumps({
                "id": f"req_{i}", "at": f"2026-09-21T00:0{i}:00Z",
                "asked": {"bucket": "low"}, "skipped": [], "answered_by": None,
                "tokens": {"in": 0, "out": 0}, "ms_total": 0, "ok": True,
            }) + "\n")
    rows = facts.recent_requests(router)
    assert [r.id for r in rows] == ["req_2", "req_1", "req_0"]


def test_error_brain_entries_is_empty_before_anything_is_learned(router):
    assert facts.error_brain_entries(router) == []


def test_error_brain_entries_puts_flagged_ones_first(router):
    router._error_brain.classify("a completely unrecognized provider message", None)
    router._error_brain.classify("HTTP 401", 401)  # a confident rule match, not flagged
    entries = facts.error_brain_entries(router)
    assert entries[0].flagged_for_review is True


def test_allowance_lists_every_configured_model(router):
    rows = facts.allowance(router)
    assert len(rows) == 1
    assert rows[0].provider == "groq"
    assert rows[0].quota_ok is True
    assert rows[0].provider_rate_exhausted is False


def test_allowance_reports_a_configured_quota(router):
    from flexrouter.config import ModelConfig
    router._cfg.tiers["low"][0] = ModelConfig(
        provider="groq", model="llama-3.1-8b-instant", score=85,
        rpm=60, tpm=60000, quotas={"rpd": 1},
    )
    router._quota_tracker.record("groq", "llama-3.1-8b-instant")
    rows = facts.allowance(router)
    assert rows[0].quotas == {"rpd": 1}
    assert rows[0].quota_ok is False
    assert rows[0].quota_wait_seconds > 0


def test_allowance_reports_provider_rate_limit_exhaustion(router):
    import time
    router._rate_limit_store.update_headroom(
        "groq", "llama-3.1-8b-instant",
        remaining_requests=0, reset_requests_at=time.time() + 30,
    )
    row = facts.allowance(router)[0]
    assert row.provider_rate_exhausted is True
    assert row.provider_available_at is not None


def test_settings_fields_reports_the_live_value_when_no_override_exists(router):
    fields = {f.name: f for f in facts.settings_fields(router)}
    assert fields["window_seconds"].value == 60
    assert fields["window_seconds"].overridden is False


def test_settings_fields_prefers_the_override_value(router):
    from flexrouter.overrides import set_override
    set_override("settings", "window_seconds", None, 999)
    fields = {f.name: f for f in facts.settings_fields(router)}
    assert fields["window_seconds"].value == 999
    assert fields["window_seconds"].overridden is True


def test_provider_editable_fields_is_none_for_an_unknown_provider(router):
    assert facts.provider_editable_fields(router, "no-such-provider") is None


def test_provider_editable_fields_reports_the_live_value(router):
    result = facts.provider_editable_fields(router, "groq")
    assert result["fields"]["base_url"]["value"] == "https://api.groq.com/openai/v1"
    assert result["fields"]["base_url"]["overridden"] is False
    assert result["overridden_at_all"] is False


def test_provider_editable_fields_prefers_the_override_value(router):
    from flexrouter.overrides import set_override
    set_override("providers", "groq", "base_url", "http://localhost:1234/v1")
    result = facts.provider_editable_fields(router, "groq")
    assert result["fields"]["base_url"]["value"] == "http://localhost:1234/v1"
    assert result["fields"]["base_url"]["overridden"] is True
    assert result["overridden_at_all"] is True


def test_model_editable_fields_is_none_for_an_unknown_model(router):
    assert facts.model_editable_fields(router, "groq", "no-such-model") is None


def test_model_editable_fields_reports_the_live_value(router):
    result = facts.model_editable_fields(router, "groq", "llama-3.1-8b-instant")
    assert result["fields"]["score"]["value"] == 85
    assert result["fields"]["score"]["overridden"] is False


def test_model_editable_fields_prefers_the_override_value(router):
    from flexrouter.overrides import set_override
    set_override("models", "groq/llama-3.1-8b-instant", "score", 42)
    result = facts.model_editable_fields(router, "groq", "llama-3.1-8b-instant")
    assert result["fields"]["score"]["value"] == 42
    assert result["fields"]["score"]["overridden"] is True
    assert result["overridden_at_all"] is True


def test_pending_catalogue_reads_what_a_refresh_wrote(router):
    from flexrouter.store import write_json
    path = router._cfg.state_dir + "/catalog_pending.json"
    write_json(path, {"groq": {"checked_at": "now", "appeared": [{"model": "new-model"}],
                               "vanished": [], "changed": []}})
    pending = facts.pending_catalogue(router)
    assert pending["groq"]["appeared"][0]["model"] == "new-model"
