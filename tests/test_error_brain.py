import time

import pytest

from flexrouter import redact
from flexrouter.decider import ErrorVerdict
from flexrouter.error_brain import ErrorBrain, classify_by_rule, fingerprint


@pytest.fixture(autouse=True)
def _heuristic_redaction_on():
    """redact_errors defaults to off (2026-09-24) - see test_error_envelope.py."""
    redact.set_enabled(True)
    yield
    redact.set_enabled(False)


class _StubDecider:
    def __init__(self, verdict="unknown", source="stub", confidence=0.5):
        self._v = ErrorVerdict(verdict, source, confidence)
        self.calls: list = []

    def classify_error(self, text, status):
        self.calls.append((text, status))
        return self._v

    def describe_model(self, *a, **kw):
        return {}


class _SlowDecider:
    """Widens the window between deciding and recording, so a concurrency bug
    has time to happen instead of relying on luck."""

    configured = True

    def classify_error(self, text, status):
        time.sleep(0.005)
        return ErrorVerdict("unknown", "stub", 0.1)

    def describe_model(self, *a, **kw):
        return {}


def test_classify_by_rule_maps_every_documented_status_code():
    assert classify_by_rule("nope", 401).verdict == "bad_key"
    assert classify_by_rule("nope", 403).verdict == "bad_key"
    assert classify_by_rule("nope", 402).verdict == "needs_payment"
    assert classify_by_rule("nope", 404).verdict == "model_gone"
    assert classify_by_rule("nope", 410).verdict == "model_gone"
    assert classify_by_rule("nope", 429).verdict == "too_fast"
    assert classify_by_rule("nope", 500).verdict == "their_end_temporary"
    assert classify_by_rule("nope", 503).verdict == "their_end_temporary"


def test_classify_by_rule_catches_context_length_substrings():
    assert classify_by_rule("This model's maximum context length is 8192 tokens", None).verdict == "message_too_long"
    assert classify_by_rule("prompt is too long for this model", None).verdict == "message_too_long"


def test_classify_by_rule_returns_none_for_unrecognized_text_and_status():
    assert classify_by_rule("something genuinely new", 418) is None
    assert classify_by_rule("something genuinely new", None) is None


def test_fingerprint_normalizes_digits_and_case_and_whitespace():
    a = fingerprint("Rate limit exceeded for requests per minute (id: 8842)")
    b = fingerprint("rate limit   exceeded for requests per minute (id: 1)")
    assert a == b


def test_fingerprint_clips_to_two_hundred_chars():
    assert len(fingerprint("x" * 500)) <= 200


def test_error_brain_classifies_via_rule_without_consulting_the_decider(tmp_path):
    # Was written against 429. That status is now contestable on purpose --
    # providers overload it for billing -- so it does reach the decider when
    # one is configured. The behaviour this test exists to pin, "an
    # unambiguous rule answers on its own", is unchanged; 401 is unambiguous.
    decider = _StubDecider()
    brain = ErrorBrain(str(tmp_path), decider)
    v = brain.classify("boom", 401)
    assert v.verdict == "bad_key"
    assert v.source == "rule"
    assert decider.calls == []  # never consulted — the rule already answered


def test_error_brain_consults_the_decider_on_a_genuine_miss(tmp_path):
    decider = _StubDecider(verdict="unknown", confidence=0.5)
    brain = ErrorBrain(str(tmp_path), decider)
    v = brain.classify("a brand new kind of failure nobody has seen", None)
    assert v.verdict == "unknown"
    assert v.source == "stub"
    assert len(decider.calls) == 1


def test_error_brain_remembers_a_fingerprint_without_reconsulting_the_decider(tmp_path):
    decider = _StubDecider()
    brain = ErrorBrain(str(tmp_path), decider)
    brain.classify("a brand new kind of failure nobody has seen", None)
    brain.classify("a brand new kind of failure nobody has seen", None)  # same fingerprint
    assert len(decider.calls) == 1  # steady-state cost is ~zero


def test_error_brain_scrubs_before_persisting(tmp_path):
    decider = _StubDecider()
    brain = ErrorBrain(str(tmp_path), decider)
    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"
    brain.classify(f"Incorrect API key provided: {leaked}", 401)
    on_disk = (tmp_path / "error_brain.json").read_text(encoding="utf-8")
    assert leaked not in on_disk


def test_error_brain_flags_low_confidence_for_review(tmp_path):
    decider = _StubDecider(verdict="unknown", confidence=0.3)
    brain = ErrorBrain(str(tmp_path), decider, confidence_threshold=0.80)
    brain.classify("something ambiguous", None)
    import json
    on_disk = json.loads((tmp_path / "error_brain.json").read_text(encoding="utf-8"))
    entry = next(iter(on_disk.values()))
    assert entry["flagged_for_review"] is True


def test_error_brain_never_overwrites_a_manual_entry(tmp_path):
    decider = _StubDecider(verdict="unknown", confidence=0.9)
    brain = ErrorBrain(str(tmp_path), decider)
    fp = fingerprint("a recurring odd message")
    brain._entries[fp] = brain._entries.get(fp) or None
    from flexrouter.error_brain import ErrorBrainEntry
    brain._entries[fp] = ErrorBrainEntry(
        verdict="bad_request", source="manual", confidence=1.0, seen=1,
        first_at="2026-01-01T00:00:00Z", last_at="2026-01-01T00:00:00Z",
        sample="a recurring odd message")
    v = brain.classify("a recurring odd message", None)
    assert v.verdict == "bad_request"
    assert v.source == "manual"
    assert decider.calls == []  # a fingerprint hit never re-consults the decider


def test_error_brain_state_survives_a_new_instance(tmp_path):
    decider = _StubDecider()
    brain = ErrorBrain(str(tmp_path), decider)
    brain.classify("boom", 402)
    reloaded = ErrorBrain(str(tmp_path), decider)
    v = reloaded.classify("boom", 402)  # same rule, but prove the file round-trips
    assert v.verdict == "needs_payment"


def test_classify_by_rule_now_covers_bad_request():
    from flexrouter.error_brain import classify_by_rule
    assert classify_by_rule("malformed request body", 400).verdict == "bad_request"


def test_error_brain_exposes_its_confidence_threshold(tmp_path):
    from flexrouter.decider import NullDecider
    from flexrouter.error_brain import ErrorBrain
    brain = ErrorBrain(str(tmp_path), NullDecider(), confidence_threshold=0.8)
    assert brain.confidence_threshold == 0.8


# --- rules as a prior, not a verdict -----------------------------------------
# 429/400/403 are overloaded in practice: providers return 429 for "out of
# credits" as often as for "slow down". A rule that answers at confidence 1.0
# ends the matter, so the router backs off and retries forever against an
# account that needs topping up. These tests pin the escape hatch.


def test_overloaded_status_lets_a_confident_decider_overrule_the_rule(tmp_path):
    decider = _StubDecider(verdict="needs_payment", confidence=0.9)
    brain = ErrorBrain(str(tmp_path), decider)
    v = brain.classify("You have insufficient credits for this request", 429)
    assert v.verdict == "needs_payment"
    assert v.source == "stub"


def test_overloaded_status_keeps_the_rule_when_the_decider_is_less_sure(tmp_path):
    decider = _StubDecider(verdict="needs_payment", confidence=0.2)
    brain = ErrorBrain(str(tmp_path), decider)
    v = brain.classify("slow down please", 429)
    assert v.verdict == "too_fast"
    assert v.source == "rule"


def test_unambiguous_status_is_never_sent_to_the_decider(tmp_path):
    decider = _StubDecider(verdict="needs_payment", confidence=0.99)
    brain = ErrorBrain(str(tmp_path), decider)
    for status in (401, 404, 410, 500):
        brain.classify(f"failure text for {status}", status)
    assert decider.calls == []


def test_without_a_configured_decider_an_overloaded_status_is_never_contested(tmp_path):
    """The compatibility guarantee, stated as something that can actually fail.

    An earlier version of this test only checked the returned verdict -- and
    passed even with the `configured` check deleted, because arbitration hands
    the win back to the full-confidence rule anyway when the decider answers
    0.0. The behaviour that really depends on the flag is that an unconfigured
    install does not reach for a classifier at all, which for a real
    HttpDecider is a network call on every novel 400/403/429.
    """
    decider = _StubDecider(verdict="needs_payment", confidence=0.99)
    decider.configured = False
    brain = ErrorBrain(str(tmp_path), decider)
    v = brain.classify("Rate limit reached for gpt-4o", 429)
    assert v.verdict == "too_fast"
    assert v.source == "rule"
    assert v.confidence == 1.0
    assert decider.calls == []
    entry = brain._entries[fingerprint("Rate limit reached for gpt-4o")]
    assert entry.flagged_for_review is False


def test_null_decider_is_not_considered_configured():
    from flexrouter.decider import NullDecider
    assert NullDecider.configured is False


def test_concurrent_classification_of_distinct_errors_does_not_crash(tmp_path):
    """Once classification is offloaded to threads, two requests can hit a
    novel error at the same moment. On Windows this surfaced as os.replace
    losing a race for the file ("Access is denied"), not as a lost write.

    Note the texts differ by letter, not digit: fingerprint() strips digits on
    purpose, so numbered variants would all collapse to one entry.
    """
    import threading

    brain = ErrorBrain(str(tmp_path), _SlowDecider())
    errors = []

    def hammer(ch):
        try:
            brain.classify(f"a novel failure of kind {ch} appeared", None)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(chr(97 + i),)) for i in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent classify raised: {errors[:3]}"
    assert len(brain._entries) == 24


def test_concurrent_sightings_of_one_error_do_not_lose_increments(tmp_path):
    """Counts stay right when the same error arrives from several threads.

    Honest scope: this passes with the lock removed too. `existing.seen += 1`
    is a read-modify-write and unsafe in principle, but under CPython's GIL the
    window is too small to lose an increment reliably, so this does not
    demonstrate the lock is necessary -- the sibling crash test does that. It
    is kept as a plain correctness assertion on counting under concurrency."""
    import threading

    brain = ErrorBrain(str(tmp_path), _SlowDecider())

    def hammer():
        brain.classify("one recurring novel failure", None)

    threads = [threading.Thread(target=hammer) for _ in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(brain._entries) == 1
    assert next(iter(brain._entries.values())).seen == 24
