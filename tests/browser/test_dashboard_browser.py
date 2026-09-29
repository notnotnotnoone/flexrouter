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
    for path in ["/", "/providers", "/models_catalog", "/buckets", "/requests", "/chat",
                 "/status", "/allowance", "/settings"]:
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
    page.goto(server + "/chat")
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
    expect(page.locator("[data-copy='#add-ai-prompt']")).to_contain_text("Copied")
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


# ── PLAN-V2.3.md Sessions 15-16: feedback, no swapping, every width ──────

def _trace_row(n: int) -> dict:
    return {"id": f"req_live{n}", "at": "2026-09-22T10:00:00.000Z", "asked": {"bucket": "low"},
            "skipped": [], "attempts": [], "answered_by": {"provider": "groq", "model": "m"},
            "tokens": {"in": 1, "out": 1}, "ms_total": 90, "ok": True}


def test_a_live_refresh_waits_while_the_pointer_is_on_a_row(page, server):
    page.request.post(server + "/settings/dashboard", form={"refresh_seconds": "2"})
    _write_trace(_trace_row(1))
    polls = []
    page.on("request", lambda r: polls.append(r.url) if "fragment=1" in r.url else None)
    page.goto(server + "/requests")
    page.locator("tr.req-row").first.hover()
    page.wait_for_timeout(500)
    polls.clear()                       # one may have left before the hover landed
    page.wait_for_timeout(4500)
    assert polls == [], "a poll replaced the rows under the cursor"
    page.mouse.move(5, 5)
    for _ in range(40):                 # up to 8s: a busy machine is slow to tick
        if polls:
            break
        page.wait_for_timeout(200)
    assert polls, "the refresh never came back once the pointer left"


def test_a_failed_save_stays_on_the_page_and_says_why(page, server, errors):
    page.goto(server + "/settings")
    form = page.locator("form[action='/settings/failover_budget_seconds']")
    form.locator("input").fill("soon")
    form.locator("button").click()
    expect(form.locator("button")).to_have_attribute("data-state", "failed")
    expect(form.locator(".btn-note")).to_contain_text("needs a number")
    expect(form.locator("input")).to_have_value("soon")
    assert errors == []


def test_a_dangerous_reset_offers_undo(page, server, errors):
    page.request.post(server + "/settings/window_seconds", form={"value": "77"})
    page.goto(server + "/settings")
    page.click("form[action='/settings/reset-all'] button")
    expect(page.locator("form[action='/settings/window_seconds'] input")).to_have_value("60")
    page.click(".toast-act")
    expect(page.locator(".toast-msg").last).to_have_text("Put back")
    expect(page.locator("form[action='/settings/window_seconds'] input")).to_have_value("77")
    assert errors == []


@pytest.mark.parametrize("width", [1014, 375])
def test_no_page_is_wider_than_the_window(page, server, width):
    page.set_viewport_size({"width": width, "height": 800})
    for path in ["/", "/providers", "/providers/groq", "/models_catalog", "/buckets",
                 "/requests", "/allowance", "/settings", "/chat"]:
        page.goto(server + path)
        extra = page.evaluate("document.documentElement.scrollWidth - innerWidth")
        assert extra <= 0, f"{path} is {extra}px wider than a {width}px window"


def test_the_menu_folds_away_on_a_phone(page, server):
    page.set_viewport_size({"width": 375, "height": 800})
    page.goto(server + "/")
    expect(page.locator(".nav-links")).to_be_hidden()
    page.click(".nav-toggle")
    expect(page.locator(".nav-links")).to_be_visible()
    page.click(".nav-links a:has-text('Allowance')")
    expect(page).to_have_url(server + "/allowance")


def test_the_menu_button_is_hidden_on_a_laptop(page, server):
    page.set_viewport_size({"width": 1014, "height": 800})
    page.goto(server + "/")
    expect(page.locator(".nav-toggle")).to_be_hidden()
    expect(page.locator(".nav-links")).to_be_visible()


def test_chart_labels_are_readable_in_a_narrow_column(page, server):
    import flexrouter.app as app_module
    import os
    from datetime import datetime, timezone
    state = app_module.get_router()._cfg.state_dir
    os.makedirs(state, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(os.path.join(state, "audit.csv"), "a", encoding="utf-8") as f:
        f.write("timestamp,tier,provider,model,prompt_tokens,completion_tokens,cost_usd,"
                "latency_ms,status,request_id\n")
        f.write(f"{now},low,groq,m,1,1,0,90,ok,r1\n")
    page.set_viewport_size({"width": 1014, "height": 800})
    page.goto(server + "/")
    size = page.evaluate(
        "Math.min(...[...document.querySelectorAll('.chart-tick')].map(t => t.getBoundingClientRect().height))")
    assert size >= 9, f"axis labels render {size}px tall"


def test_the_overview_does_not_jump_while_it_loads(page, server):
    """Papercut 26: the layout-shift score was 0.32; Google calls >0.1 poor."""
    page.set_viewport_size({"width": 1014, "height": 800})
    page.goto(server + "/")
    page.wait_for_timeout(1500)
    cls = page.evaluate("""() => new Promise(done => {
        const seen = [];
        new PerformanceObserver(l => seen.push(...l.getEntries()))
            .observe({type: 'layout-shift', buffered: true});
        setTimeout(() => done(seen.reduce((a, e) => a + e.value, 0)), 200);
    })""")
    assert cls < 0.1


# ── PLAN-V2.3.md Session 18: Get started + Test all ─────────────────────

def test_test_all_says_hi_to_every_model_and_ticks_the_step(page, server, errors, monkeypatch):
    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"content": "hello"}}]}
    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    page.goto(server + "/")
    step = page.locator(".qs-step[data-n='4']")
    step.locator("[data-test-all]").click()
    expect(step.locator("[data-test-all]")).to_contain_text("All 1 work")
    expect(step).to_have_attribute("data-step", "done")
    expect(step.locator(".test-row")).to_have_attribute("data-state", "done")
    assert errors == []


def test_hiding_get_started_offers_undo(page, server, errors):
    page.goto(server + "/")
    expect(page.locator("#qs")).to_be_visible()
    page.click("#qs .box-action [data-qs-hide]")
    expect(page.locator("#qs")).to_be_hidden()
    page.click(".toast-act")
    expect(page.locator("#qs")).to_be_visible()
    assert errors == []


# ── PLAN-V2.3.md Session 8: the Status page ────────────────────────────

def test_a_status_row_opens_and_its_fix_settles_it(page, server, errors):
    import flexrouter.app as app_module
    app_module.get_router()._status.set_needs_you(
        "groq", "llama-3.1-8b-instant", "Your Groq balance is empty.", kind="balance_empty",
        action="retry", detail="402 Payment Required END-OF-BODY")
    page.goto(server + "/status")
    row = page.locator(".st-row[data-status='needs']")
    expect(row).to_contain_text("Your Groq balance is empty.")
    row.locator(".st-name").click()
    expect(page.locator(".st-detail:not([hidden])")).to_contain_text("END-OF-BODY")
    row.locator("[data-st-action]").click()
    expect(page.locator(".st-count[data-status='ready'] b")).to_have_text("1")
    expect(page.locator(".st-group[data-group='needs'] .st-row")).to_have_count(0)
    assert errors == []


def test_pressing_a_count_shows_only_those(page, server):
    page.goto(server + "/status")
    expect(page.locator(".st-group[data-group='ready']")).to_be_hidden()
    page.click(".st-count[data-status='ready']")
    expect(page.locator(".st-group[data-group='ready']")).to_be_visible()
    expect(page.locator(".st-group[data-group='needs']")).to_be_hidden()
