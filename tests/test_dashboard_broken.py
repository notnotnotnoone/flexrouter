"""What's broken: two columns, one fix per card, and the menu badge."""
import pytest
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter.app import create_app


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def _down():
    app_module.get_router()._status.set_provider_needs_you("groq", "key rejected")


def test_all_clear_when_nothing_is_wrong(client):
    body = client.get("/broken").text
    assert "All clear" in body


def test_a_down_provider_is_a_card_with_a_fix(client):
    _down()
    body = client.get("/broken").text
    assert 'class="card' in body
    assert "key rejected" in body
    assert 'href="/providers/groq"' in body


def test_both_columns_are_labelled(client):
    _down()
    body = client.get("/broken").text
    assert "Needs you" in body and "Handling it" in body


def test_the_menu_badge_counts_what_needs_you(client):
    _down()
    body = client.get("/models_catalog").text      # any page carries the menu
    assert 'class="nav-badge"' in body


def test_no_badge_when_nothing_needs_you(client):
    assert 'class="nav-badge"' not in client.get("/models_catalog").text


def test_broken_polls_itself(client):
    assert "data-live" in client.get("/broken").text


# ── Resolving an unclear error in place ──────────────────────────────

class _Unsure:
    configured = True

    def classify_error(self, text, status):
        from flexrouter.decider import ErrorVerdict
        return ErrorVerdict("bad_request", "classifier", 0.41, insight={
            "ok": True, "probabilities": {"bad_request": 0.41, "message_too_long": 0.38,
                                          "bad_key": 0.21}})

    def describe_model(self, *a, **kw):
        return {}


def _unclear() -> str:
    brain = app_module.get_router()._error_brain
    brain._decider = _Unsure()
    brain.classify("the quota widget is sulking today", 400)
    [fp] = [fp for fp, e in brain._entries.items() if e.flagged_for_review]
    return fp


def test_an_unclear_error_resolves_on_its_card(client):
    fp = _unclear()
    body = client.get("/broken").text
    assert "data-resolve-toggle" in body
    assert f'action="/brain/{fp}/verdict"' in body
    assert "Review in Error brain" not in body
    # Every verdict is a chip, the classifier's best guess first and starred.
    assert body.count('class="verdict-chip') == 8
    first = body.index('class="verdict-chip')
    assert body.index('value="bad_request"') < body.index('value="message_too_long"') \
        < body.index('value="bad_key"') < body.index('value="unknown"')
    assert 'verdict-chip is-guess' in body[first:first + 40]
    assert "41%" in body


def test_a_chip_saves_in_place_as_json(client):
    fp = _unclear()
    r = client.post(f"/brain/{fp}/verdict", data={"verdict": "message_too_long", "next": "/broken"},
                    headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": 'Saved: that error now means "Message too long".'}
    entry = app_module.get_router()._error_brain._entries[fp]
    assert (entry.verdict, entry.source, entry.flagged_for_review) == \
        ("message_too_long", "manual", False)
    assert "data-resolve-toggle" not in client.get("/broken").text


def test_a_failed_save_says_why_as_json(client):
    r = client.post("/brain/nope/verdict", data={"verdict": "bad_key"},
                    headers={"Accept": "application/json"})
    assert r.status_code == 404
    assert r.json()["ok"] is False


def test_without_javascript_a_chip_lands_back_on_whats_broken(client):
    fp = _unclear()
    r = client.post(f"/brain/{fp}/verdict", data={"verdict": "bad_key", "next": "/broken"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/broken?ok=1")


def test_the_error_brain_form_still_goes_back_to_the_error_brain(client):
    fp = _unclear()
    r = client.post(f"/brain/{fp}/verdict", data={"verdict": "bad_key"}, follow_redirects=False)
    assert r.headers["location"].startswith("/brain?ok=1")
