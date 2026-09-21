from datetime import datetime, timedelta, timezone

from flexrouter.model_facts import (
    CapabilityFact, ModelFactsStore, check_staleness,
    record_contradicting_failure, record_success,
)


def _now(offset_days: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=offset_days)).isoformat(timespec="seconds")


def test_a_success_sets_yes_and_resets_strikes():
    fact = CapabilityFact(status="doubted", source="observed", strikes=2)
    updated = record_success(fact, _now())
    assert updated.status == "yes"
    assert updated.strikes == 0
    assert updated.last_success_at is not None


def test_a_success_never_touches_a_manual_fact():
    fact = CapabilityFact(status="no", source="manual", strikes=9)
    updated = record_success(fact, _now())
    assert updated == fact


def test_first_contradicting_failure_goes_to_doubted_never_straight_to_no():
    fact = CapabilityFact(status="yes", source="observed")
    updated = record_contradicting_failure(fact, _now(), "req_1")
    assert updated.status == "doubted"
    assert updated.strikes == 1
    assert updated.evidence == ["req_1"]


def test_three_strikes_on_an_observed_fact_reaches_no():
    fact = CapabilityFact(status="doubted", source="observed", strikes=2)
    updated = record_contradicting_failure(fact, _now(), "req_3")
    assert updated.status == "no"
    assert updated.strikes == 3


def test_overriding_a_published_fact_needs_five_strikes_not_three():
    fact = CapabilityFact(status="doubted", source="published", strikes=2)
    updated = record_contradicting_failure(fact, _now(), "req_3")
    assert updated.status == "doubted"  # 3 strikes is not enough for a published fact
    assert updated.strikes == 3
    updated = record_contradicting_failure(updated, _now(), "req_4")
    updated = record_contradicting_failure(updated, _now(), "req_5")
    assert updated.status == "no"
    assert updated.strikes == 5


def test_a_contradicting_failure_never_touches_a_manual_fact():
    fact = CapabilityFact(status="yes", source="manual")
    updated = record_contradicting_failure(fact, _now(), "req_1")
    assert updated == fact


def test_an_observed_no_goes_stale_after_thirty_days_and_reverts_to_doubted():
    fact = CapabilityFact(status="no", source="observed", strikes=3, last_failure_at=_now(31))
    updated = check_staleness(fact, _now())
    assert updated.status == "doubted"
    assert updated.strikes == 3  # history preserved


def test_a_recent_observed_no_does_not_go_stale():
    fact = CapabilityFact(status="no", source="observed", strikes=3, last_failure_at=_now(5))
    updated = check_staleness(fact, _now())
    assert updated.status == "no"


def test_a_published_no_does_not_go_stale_the_same_way():
    fact = CapabilityFact(status="no", source="published", strikes=5, last_failure_at=_now(31))
    updated = check_staleness(fact, _now())
    assert updated.status == "no"  # only "observed" ages out, per the spec


def test_store_creates_a_default_doubted_fact_on_first_use(tmp_path):
    store = ModelFactsStore(str(tmp_path))
    fact = store.ensure_capability("openrouter", "m", "vision")
    assert fact.status == "doubted"
    assert fact.source == "guessed"


def test_store_records_success_and_persists(tmp_path):
    store = ModelFactsStore(str(tmp_path))
    store.record_success("openrouter", "m", "vision")
    facts = store.get("openrouter", "m")
    assert facts.vision.status == "yes"

    reloaded = ModelFactsStore(str(tmp_path))
    assert reloaded.get("openrouter", "m").vision.status == "yes"


def test_store_records_contradicting_failure_and_persists(tmp_path):
    store = ModelFactsStore(str(tmp_path))
    for i in range(3):
        store.record_contradicting_failure("openrouter", "m", "vision", f"req_{i}")
    facts = store.get("openrouter", "m")
    assert facts.vision.status == "no"
    assert facts.vision.strikes == 3


def test_store_applies_staleness_on_read(tmp_path):
    store = ModelFactsStore(str(tmp_path))
    stale = CapabilityFact(status="no", source="observed", strikes=3, last_failure_at=_now(45))
    store._facts[("openrouter", "m")] = ModelFactsWithVision(stale)
    facts = store.get("openrouter", "m")
    assert facts.vision.status == "doubted"


def ModelFactsWithVision(vision_fact):
    from flexrouter.model_facts import ModelFacts
    return ModelFacts(vision=vision_fact)


def test_record_discovered_sets_a_published_context_fact(tmp_path):
    from flexrouter.model_facts import ModelFactsStore
    store = ModelFactsStore(str(tmp_path))
    store.record_discovered("openrouter", "new-model", context_window=131072)
    facts = store.get("openrouter", "new-model")
    assert facts.context.value == 131072
    assert facts.context.source == "published"


def test_record_discovered_with_no_context_window_does_nothing(tmp_path):
    from flexrouter.model_facts import ModelFactsStore
    store = ModelFactsStore(str(tmp_path))
    store.record_discovered("openrouter", "new-model", context_window=None)
    facts = store.get("openrouter", "new-model")
    assert facts.context is None


def test_record_discovered_never_overwrites_an_existing_context_fact(tmp_path):
    from flexrouter.model_facts import ModelFactsStore
    store = ModelFactsStore(str(tmp_path))
    store.record_discovered("openrouter", "m", context_window=8192)
    store.record_discovered("openrouter", "m", context_window=999999)  # a later, different refresh
    facts = store.get("openrouter", "m")
    assert facts.context.value == 8192  # first-seen value kept
