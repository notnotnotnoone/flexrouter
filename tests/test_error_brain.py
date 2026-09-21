from flexrouter.decider import ErrorVerdict
from flexrouter.error_brain import ErrorBrain, classify_by_rule, fingerprint


class _StubDecider:
    def __init__(self, verdict="unknown", source="stub", confidence=0.5):
        self._v = ErrorVerdict(verdict, source, confidence)
        self.calls: list = []

    def classify_error(self, text, status):
        self.calls.append((text, status))
        return self._v

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
    decider = _StubDecider()
    brain = ErrorBrain(str(tmp_path), decider)
    v = brain.classify("boom", 429)
    assert v.verdict == "too_fast"
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
