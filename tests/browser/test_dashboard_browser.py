"""The dashboard's JavaScript, driven in a real browser.

Opt-in: `uv run pytest -m browser`. A plain `pytest` skips these (see
pyproject.toml). Run them after changing anything in static/app.js or the
markup it hooks into. See docs/agents/testing.md.
"""
import json

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def _write_trace(trace: dict):
    import os
    import flexrouter.app as app_module
    state = app_module.get_router()._cfg.state_dir
    os.makedirs(state, exist_ok=True)
    with open(os.path.join(state, "traces.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(trace) + "\n")


def test_every_page_loads_without_a_script_error(page, server, errors):
    for path in ["/", "/providers", "/models_catalog", "/buckets", "/requests", "/playground",
                 "/broken", "/brain", "/allowance", "/settings"]:
        page.goto(server + path)
        expect(page.locator(".page-title")).to_be_visible()
    assert errors == []


def test_boxes_are_visible_after_the_entrance(page, server):
    page.goto(server + "/")
    page.wait_for_timeout(1200)
    hidden = page.evaluate(
        "[...document.querySelectorAll('[data-enter]')].filter(e => getComputedStyle(e).opacity !== '1').length")
    assert hidden == 0


def test_ctrl_k_finds_a_page_and_goes_there(page, server, errors):
    page.goto(server + "/")
    page.keyboard.press("Control+k")
    expect(page.locator("#palette")).to_be_visible()
    page.keyboard.type("allowance")
    page.keyboard.press("Enter")
    expect(page).to_have_url(server + "/allowance")
    expect(page.locator(".nav a[aria-current]")).to_have_text("Allowance")
    assert errors == []


def test_g_then_r_goes_to_requests(page, server):
    page.goto(server + "/")
    page.keyboard.press("g")
    page.keyboard.press("r")
    expect(page).to_have_url(server + "/requests")


def test_a_request_opens_its_journey_and_escape_closes_it(page, server, errors):
    _write_trace({"id": "req_b1", "at": "2026-09-22T10:00:00.000Z", "asked": {"bucket": "low"},
                  "skipped": [], "attempts": [{"n": 1, "provider": "mistral", "model": "large",
                                               "status": 429, "provider_message": "slow down",
                                               "verdict": "too_fast", "ms": 50}],
                  "answered_by": {"provider": "groq", "model": "m"},
                  "tokens": {"in": 1, "out": 1}, "ms_total": 90, "ok": True})
    page.goto(server + "/requests")
    page.locator("tr.req-row").first.click()
    expect(page.locator("#sheet-root .sheet")).to_be_visible()
    expect(page.locator("#sheet-root .rq-tried")).to_contain_text("slow down")
    expect(page).to_have_url(server + "/requests/req_b1")
    page.keyboard.press("Escape")
    expect(page.locator("#sheet-root .sheet")).to_have_count(0)
    expect(page).to_have_url(server + "/requests")
    assert errors == []


def test_settings_search_filters_rows(page, server):
    page.goto(server + "/settings")
    page.fill(".set-search input", "timeout")
    visible = page.locator(".set-row:not([hidden])")
    assert visible.count() >= 2
    for text in visible.all_inner_texts():
        assert "timeout" in text.lower()


def test_a_saved_setting_raises_a_toast(page, server):
    page.goto(server + "/settings")
    row = page.locator("#set-window_seconds")
    row.locator("input[name=value]").fill("61")
    row.locator("button", has_text="Save").click()
    expect(page.locator(".toast")).to_contain_text("window_seconds saved")


def test_motion_off_disables_entrances(page, server):
    import flexrouter.dashboard.prefs as prefs
    prefs.save(prefs.Prefs(motion="off"))
    page.goto(server + "/")
    assert page.evaluate("document.documentElement.dataset.motion") == "off"
    assert page.evaluate("getComputedStyle(document.querySelector('[data-enter]')).opacity") == "1"


def test_models_rows_expand_and_sort(page, server):
    page.goto(server + "/models_catalog")
    page.locator(".m-row").first.click()
    expect(page.locator(".m-detail:not([hidden])")).to_have_count(1)
    page.locator("th[data-sort=score]").click()
    expect(page.locator("th[data-sort=score]")).to_have_attribute("aria-sort", "descending")


def test_a_preset_opens_in_the_side_panel(page, server):
    page.goto(server + "/providers")
    page.locator(".preset-card:not(.is-configured) a").first.click()
    expect(page.locator("#sheet-root .sheet")).to_be_visible()
    expect(page.locator("#sheet-root input[name=secret]")).to_be_visible()


def test_the_playground_shows_the_message_it_sends(page, server):
    page.goto(server + "/playground")
    page.fill("#pg-input", "hello")
    page.keyboard.press("Enter")
    expect(page.locator(".pg-user .pg-text")).to_have_text("hello")


def test_add_with_ai_builds_a_prompt_and_the_copy_button_works(page, server, errors):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.goto(server + "/models_catalog")
    page.click("text=Add models with AI")
    expect(page).to_have_url(server + "/models_catalog/add-with-ai")
    page.select_option("select[name=provider]", "groq")
    page.click("button:has-text('Build prompt')")
    expect(page.locator("#add-ai-prompt")).to_be_visible()
    page.click("[data-copy='#add-ai-prompt']")
    expect(page.locator(".toast")).to_contain_text("Copied")
    copied = page.evaluate("navigator.clipboard.readText()")
    assert "groq" in copied
    assert errors == []


def test_add_with_ai_review_lets_you_edit_and_apply_a_row(page, server, errors):
    page.goto(server + "/models_catalog/add-with-ai")
    page.select_option("select[name=provider]", "groq")
    page.click("button:has-text('Build prompt')")
    page.fill("textarea[name=answer]", json.dumps([{
        "provider": "groq", "model": "new-model-x", "kind": "chat", "context": 8192,
        "rpm": 30, "tpm": 6000, "rph": None, "rpd": None, "rps": None, "tph": None,
        "tpd": None, "tps": None, "vision": False, "free": True, "score": 70}]))
    page.click("button:has-text('Show what would be added')")
    expect(page.locator("input[name='model:0']")).to_have_value("new-model-x")
    page.click("button:has-text('Apply checked rows')")
    expect(page).to_have_url(server + "/models_catalog?ok=1&message=1%20added")
    expect(page.locator("body")).to_contain_text("added")
    assert errors == []


def test_a_guessed_id_locks_apply_until_use_fixes_it(page, server, errors):
    """PLAN-V2.3.md Session 13: a row whose ID isn't on the provider's real
    list can't be applied; [use] swaps in the real one and unlocks it."""
    from pathlib import Path
    import flexrouter.app as app_module
    from flexrouter import catalogue
    from flexrouter.store import write_json
    state = Path(app_module.get_router()._cfg.state_dir)
    write_json(state / catalogue.KNOWN_MODEL_IDS_FILENAME, {"groq": {"ids": ["new-model-x2"]}})
    try:
        page.goto(server + "/models_catalog/add-with-ai")
        page.select_option("select[name=provider]", "groq")
        page.click("button:has-text('Build prompt')")
        page.fill("textarea[name=answer]", json.dumps([{
            "provider": "groq", "model": "new-model-x", "kind": "chat", "context": 8192,
            "rpm": None, "tpm": None, "rph": None, "rpd": None, "rps": None, "tph": None,
            "tpd": None, "tps": None, "vision": False, "free": True, "score": 70}]))
        page.click("button:has-text('Show what would be added')")
        expect(page.locator("#apply-rows")).to_be_disabled()
        expect(page.locator("#apply-block")).to_contain_text("Fix 1 ID")
        page.click("[data-use-id='new-model-x2']")
        expect(page.locator("input[name='model:0']")).to_have_value("new-model-x2")
        expect(page.locator("#apply-rows")).to_be_enabled()
        assert errors == []
    finally:
        (state / catalogue.KNOWN_MODEL_IDS_FILENAME).unlink(missing_ok=True)


def _unclear_error() -> str:
    import flexrouter.app as app_module
    from flexrouter.decider import ErrorVerdict

    class _Unsure:
        configured = True

        def classify_error(self, text, status):
            return ErrorVerdict("bad_request", "classifier", 0.41, insight={
                "ok": True, "probabilities": {"bad_request": 0.41, "message_too_long": 0.38}})

        def describe_model(self, *a, **kw):
            return {}

    brain = app_module.get_router()._error_brain
    brain._decider = _Unsure()
    brain.classify("the quota widget is sulking today", None)
    return next(fp for fp, e in brain._entries.items() if e.flagged_for_review)


def test_an_unclear_error_is_resolved_without_leaving_whats_broken(page, server, errors):
    import flexrouter.app as app_module
    fp = _unclear_error()
    page.goto(server + "/broken")
    expect(page.locator(".col-you .col-count")).to_have_text("1")
    chips = page.locator(".resolver-wrap .verdict-chip")
    # Closed until asked: folded to nothing and out of reach of Tab.
    expect(page.locator(".resolver-wrap")).to_have_attribute("inert", "")
    assert page.locator(".resolver").evaluate("e => e.getBoundingClientRect().height") < 1
    page.click("[data-resolve-toggle]")
    expect(page.locator("[data-resolve-toggle]")).to_have_attribute("aria-expanded", "true")
    expect(page.locator(".resolver-wrap")).not_to_have_attribute("inert", "")
    expect(chips.first).to_contain_text("Bad request")
    page.click(".verdict-chip[value='message_too_long']")
    expect(page.locator(".verdict-chip.is-picked")).to_have_count(1)
    expect(page.locator("[data-resolve-toggle]")).to_have_count(0)
    expect(page.locator(".toast")).to_contain_text("Message too long")
    assert page.url.endswith("/broken")
    assert app_module.get_router()._error_brain._entries[fp].verdict == "message_too_long"
    assert errors == []


def test_escape_closes_the_resolver(page, server):
    _unclear_error()
    page.goto(server + "/broken")
    page.click("[data-resolve-toggle]")
    page.locator(".verdict-chip").first.focus()
    page.keyboard.press("Escape")
    expect(page.locator("[data-resolve-toggle]")).to_have_attribute("aria-expanded", "false")
    expect(page.locator("[data-resolve-toggle]")).to_be_focused()
