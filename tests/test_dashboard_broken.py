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
    brain.classify("the quota widget is sulking today", None)
    [fp] = [fp for fp, e in brain._entries.items() if e.flagged_for_review]
    return fp


def test_an_unclear_error_resolves_on_its_card(client):
    fp = _unclear()
    body = client.get("/broken").text
    assert "data-resolve-toggle" in body
    from urllib.parse import quote
    assert f'action="/brain/{quote(fp, safe="")}/verdict"' in body
    assert "Review in Error brain" not in body
    # Every verdict is a chip, the classifier's best guess first and starred.
    assert body.count('<button type="submit" name="verdict"') == 8
    first = body.index('<button type="submit" name="verdict"')
    assert body.index('value="bad_request"') < body.index('value="message_too_long"') \
        < body.index('value="bad_key"') < body.index('value="unknown"')
    assert 'verdict-chip is-guess' in body[first:first + 120]
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


def test_a_fingerprint_with_a_slash_still_saves(client):
    """The fingerprint is the error's own words; "mistral/codestral" has a
    slash the server decodes before routing. It must still reach the save."""
    brain = app_module.get_router()._error_brain
    brain._decider = _Unsure()
    brain.classify("from mistral/codestral-embed: the quota widget is sulking", None)
    [fp] = [fp for fp, e in brain._entries.items() if e.flagged_for_review]
    assert "/" in fp
    from urllib.parse import quote
    r = client.post(f"/brain/{quote(fp, safe='')}/verdict", data={"verdict": "bad_key"},
                    headers={"Accept": "application/json"})
    assert r.status_code == 200, r.text
    assert brain._entries[fp].verdict == "bad_key"


# ── Fixing a model that needs you, in place ───────────────────────────

M = "llama-3.1-8b-instant"


def _gone(action=None):
    app_module.get_router()._status.set_needs_you(
        "groq", M, "The provider says this model is gone.", kind="gone", action=action)


def _fix(client, do):
    return client.post("/broken/model", data={"provider": "groq", "model": M, "do": do},
                       headers={"Accept": "application/json"})


def test_a_model_that_needs_you_has_options_on_its_card(client):
    _gone(action="retry")
    body = client.get("/broken").text
    assert "data-resolve-toggle" in body
    assert 'action="/broken/model"' in body
    assert body.index('value="retry"') < body.index('value="off"')
    assert 'name="do" value="retry" class="verdict-chip is-guess"' in body


def test_a_did_you_mean_leads_with_the_new_name(client):
    _gone(action="use:llama-3.3-8b-instant")
    body = client.get("/broken").text
    assert "Use llama-3.3-8b-instant" in body
    assert body.index('value="use"') < body.index('value="retry"')


def test_try_again_puts_the_model_back_to_ready(client):
    _gone(action="retry")
    r = _fix(client, "retry")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert app_module.get_router()._status.get("groq", M).value == "ready"
    assert "data-resolve-toggle" not in client.get("/broken").text


def test_turn_it_off_takes_it_out_of_routing(client):
    _gone(action="retry")
    r = _fix(client, "off")
    assert r.status_code == 200 and r.json()["ok"] is True
    from flexrouter.overrides import load_overrides
    assert load_overrides()["models"][f"groq/{M}"]["enabled"] is False


def test_use_without_a_suggestion_says_why(client):
    _gone(action="retry")
    r = _fix(client, "use")
    assert r.status_code == 400
    assert "no suggested replacement" in r.json()["message"]


def test_use_swaps_in_the_suggested_model(client):
    _gone(action="use:llama-3.3-8b-instant")
    r = _fix(client, "use")
    assert r.status_code == 200, r.text
    models = [m.model for ms in app_module.get_router()._cfg.tiers.values() for m in ms]
    assert "llama-3.3-8b-instant" in models and M not in models


def test_an_unknown_model_is_a_clear_no(client):
    r = client.post("/broken/model", data={"provider": "groq", "model": "nope", "do": "retry"},
                    headers={"Accept": "application/json"})
    assert r.status_code == 404
    assert "no longer in any bucket" in r.json()["message"]
