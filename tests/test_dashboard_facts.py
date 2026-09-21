import os
import pytest
import yaml

from flexrouter._router import LocalRouter
from flexrouter.dashboard import facts
from tests.conftest import MINIMAL_CONFIG


@pytest.fixture
def router(config_file):
    r = LocalRouter(str(config_file))
    yield r
    r.close()


@pytest.fixture
def router_no_credentials(tmp_path):
    """Router with a provider that has no credentials."""
    cfg = dict(MINIMAL_CONFIG)
    cfg["settings"] = dict(cfg["settings"])
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


def test_overview_names_the_buckets_and_the_state_directory(router):
    out = facts.overview(router)
    assert out["service"]["buckets"] == ["low"]
    assert out["service"]["state_dir"].endswith(".flexrouter")


def test_overview_never_returns_a_secret(router):
    import json
    blob = json.dumps(facts.overview(router), default=str)
    assert "test-key" not in blob
