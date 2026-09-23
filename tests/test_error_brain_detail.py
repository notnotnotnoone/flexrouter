"""The Error brain keeps the whole story, not a clipped sample.

It used to keep one clipped, scrubbed sentence per kind of error: no status
code, no full provider response, no record of which models hit it or when,
and nothing about what the classifier said or why it said nothing.
"""
import json
from pathlib import Path

from flexrouter.decider import ErrorVerdict
from flexrouter.error_brain import ErrorBrain


class _Jev:
    configured = True

    def __init__(self, verdict=None):
        self.verdict = verdict or ErrorVerdict(
            "model_gone", "classifier", 0.95,
            insight={"ok": True, "transport": "decisions", "model": "~typesafe/jev-latest",
                     "latency_ms": 454, "cost": 2.1e-05, "raw_confidence": 0.99,
                     "probabilities": {"model_gone": 1.0, "bad_key": 0.0}})

    def classify_error(self, text, status):
        return self.verdict

    def describe_model(self, *a, **kw):
        return {}


LONG_BODY = json.dumps({"error": {"message": "Model labs-leanstral-1-5 is a Labs model. " + "detail " * 200,
                                  "type": "labs_not_enabled", "code": "1913"}})


def _brain(tmp_path, decider=None):
    return ErrorBrain(str(tmp_path), decider or _Jev())


def _only(brain):
    [entry] = brain._entries.values()
    return entry


def test_the_full_provider_response_and_status_are_kept(tmp_path):
    brain = _brain(tmp_path)
    brain.classify("403 from mistral/labs-leanstral-1-5: Model labs-leanstral-1-5 is a Labs model.",
                   403, provider="mistral", model="labs-leanstral-1-5", trace_id="req_1",
                   body=LONG_BODY)
    e = _only(brain)
    assert e.status == 403
    assert e.raw == LONG_BODY  # nothing clipped, nothing secret-shaped in it
    assert '"type": "labs_not_enabled"' in e.raw


def test_it_remembers_which_models_hit_it_and_when(tmp_path):
    brain = _brain(tmp_path)
    for i, model in enumerate(["a", "b", "a"]):
        brain.classify("404 from p/x: gone", 404, provider="p", model=model, trace_id=f"req_{i}")
    e = _only(brain)
    assert e.where == {"p/a": 2, "p/b": 1}
    assert [o["trace_id"] for o in e.recent] == ["req_2", "req_1", "req_0"]  # newest first
    assert e.recent[0]["model"] == "a" and e.recent[0]["status"] == 404


def test_only_the_last_twenty_occurrences_are_kept(tmp_path):
    brain = _brain(tmp_path)
    for i in range(25):
        brain.classify("404 from p/x: gone", 404, provider="p", model="x", trace_id=f"req_{i}")
    e = _only(brain)
    assert len(e.recent) == 20 and e.recent[0]["trace_id"] == "req_24"
    assert e.seen == 25


def test_what_jev_decided_is_kept_with_the_rule_it_was_weighed_against(tmp_path):
    brain = _brain(tmp_path)
    brain.classify("403 from m/x: a Labs model", 403, provider="m", model="x", trace_id="r")
    e = _only(brain)
    assert e.verdict == "model_gone" and e.source == "classifier"
    assert e.decision["probabilities"]["model_gone"] == 1.0
    assert e.decision["latency_ms"] == 454
    assert e.decision["rule_said"] == "bad_key"
    assert e.decision["overturned_rule"] is True


def test_every_classifier_call_is_logged_including_failures(tmp_path):
    failing = _Jev(ErrorVerdict("unknown", "classifier", 0.0, insight={
        "ok": False, "transport": "decisions", "model": "~typesafe/jev-latest",
        "http_status": 400, "latency_ms": 120,
        "error": '{"error":{"message":"is a decisions model and cannot be used with chat/completions"}}'}))
    brain = _brain(tmp_path, failing)
    brain.classify("something odd", None, provider="p", model="x", trace_id="r1")

    calls = brain.decider_calls()
    assert len(calls) == 1
    assert calls[0]["ok"] is False and calls[0]["http_status"] == 400
    assert "decisions model" in calls[0]["error"]
    assert calls[0]["error_text"] == "something odd"
    assert (Path(tmp_path) / "decider_calls.jsonl").exists()


def test_entries_saved_before_these_fields_existed_still_load(tmp_path):
    (Path(tmp_path) / "error_brain.json").write_text(json.dumps({"fp": {
        "verdict": "too_fast", "source": "rule", "confidence": 1.0, "seen": 3,
        "first_at": "2026-09-01T00:00:00+00:00", "last_at": "2026-09-02T00:00:00+00:00",
        "sample": "429", "flagged_for_review": False}}))
    e = _only(_brain(tmp_path))
    assert e.where == {} and e.recent == [] and e.raw == "" and e.decision is None
