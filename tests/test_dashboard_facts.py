import pytest

from flexrouter._router import LocalRouter
from flexrouter.dashboard import facts


@pytest.fixture
def router(config_file):
    r = LocalRouter(str(config_file))
    yield r
    r.close()


def test_provider_summaries_lists_every_configured_provider(router):
    names = [p.name for p in facts.provider_summaries(router)]
    assert names == ["groq"]


def test_provider_summary_reports_base_url_and_model_count(router):
    summary = facts.provider_summaries(router)[0]
    assert summary.base_url == "https://api.groq.com/openai/v1"
    assert summary.models_total == 1


def test_provider_with_an_env_key_counts_it_as_live(router):
    summary = facts.provider_summaries(router)[0]
    assert summary.key_count == 1
    assert summary.keys_live == 1
    assert summary.keys_cooling == 0
    assert summary.keys_parked == 0


def test_healthy_provider_reads_as_ok(router):
    assert facts.provider_summaries(router)[0].state == "ok"


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
