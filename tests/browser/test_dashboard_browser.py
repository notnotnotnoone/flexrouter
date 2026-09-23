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
    for path in ["/", "/providers", "/models", "/buckets", "/requests", "/playground",
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
    expect(page.locator("li.jstep-failed")).to_contain_text("slow down")
    expect(page).to_have_url(server + "/requests?id=req_b1")
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
    page.goto(server + "/models")
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
