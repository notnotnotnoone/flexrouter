"""Correcting a learned error by hand, and the Error brain page."""
import pytest
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter.app import create_app
from flexrouter.decider import ErrorVerdict
from flexrouter.error_brain import ErrorBrain


class _Guess:
    configured = True

    def classify_error(self, text, status):
        return ErrorVerdict(verdict="their_end_temporary", source="classifier", confidence=0.4)


def _brain(tmp_path):
    return ErrorBrain(str(tmp_path), _Guess())


def _learn(brain):
    brain.classify("the quota widget is sulking today", 400)
    [fp] = list(brain._entries)
    return fp


def test_a_correction_is_stored_as_the_owners(tmp_path):
    brain = _brain(tmp_path)
    fp = _learn(brain)
    brain.correct(fp, "needs_payment")
    e = brain._entries[fp]
    assert (e.verdict, e.source, e.confidence, e.flagged_for_review) == \
        ("needs_payment", "manual", 1.0, False)


def test_a_correction_survives_a_restart(tmp_path):
    brain = _brain(tmp_path)
    fp = _learn(brain)
    brain.correct(fp, "bad_key")
    assert _brain(tmp_path)._entries[fp].verdict == "bad_key"


def test_the_classifier_never_overrules_a_correction(tmp_path):
    brain = _brain(tmp_path)
    fp = _learn(brain)
    brain.correct(fp, "bad_key")
    brain.classify("the quota widget is sulking today", 400)
    assert brain._entries[fp].verdict == "bad_key"


def test_an_unknown_verdict_is_refused(tmp_path):
    brain = _brain(tmp_path)
    fp = _learn(brain)
    with pytest.raises(ValueError):
        brain.correct(fp, "vibes")


def test_an_unknown_entry_is_refused(tmp_path):
    with pytest.raises(KeyError):
        _brain(tmp_path).correct("nope", "bad_key")


# ── the page ───────────────────────────────────────────────────────────

@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def _seed_page_brain():
    brain = app_module.get_router()._error_brain
    brain._entries.clear()
    from flexrouter.error_brain import ErrorBrainEntry
    brain._entries["fp1"] = ErrorBrainEntry(
        verdict="their_end_temporary", source="classifier", confidence=0.4, seen=3,
        first_at="2026-09-21T00:00:00+00:00", last_at="2026-09-22T00:00:00+00:00",
        sample="the quota widget is sulking", flagged_for_review=True)
    return brain


def test_the_page_groups_cards_and_flags_review(client):
    _seed_page_brain()
    body = client.get("/brain").text
    assert "Their end, temporary" in body
    assert "needs review" in body
    assert 'class="meter"' in body


def test_the_page_can_correct_a_verdict(client):
    brain = _seed_page_brain()
    r = client.post("/brain/fp1/verdict", data={"verdict": "needs_payment"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert brain._entries["fp1"].source == "manual"
    assert "YOU" in client.get("/brain").text or "you" in client.get("/brain").text


def test_a_bad_correction_is_refused_with_a_message(client):
    _seed_page_brain()
    r = client.post("/brain/fp1/verdict", data={"verdict": "vibes"}, follow_redirects=False)
    assert r.status_code == 303 and "ok=0" in r.headers["location"]
