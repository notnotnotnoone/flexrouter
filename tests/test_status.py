"""One status per model (grill-decisions.md §3): every transition the
status store can make, plus the migration of the old quarantine/penalty
files it replaces."""
import json

import pytest

from flexrouter import status as st
from flexrouter.status import StatusStore, busy_seconds, classify_failure, parse_retry_after


NOW = 1_000_000.0


def test_unknown_model_is_ready():
    s = StatusStore().get("p", "m", now=NOW)
    assert s.value == st.READY
    assert s.action is None


# ---- Busy -------------------------------------------------------------------

def test_busy_uses_provider_retry_after():
    store = StatusStore()
    store.set_busy("p", "m", 42, "Too many requests", now=NOW)
    s = store.get("p", "m", now=NOW)
    assert s.value == st.BUSY
    assert s.until == NOW + 42
    assert s.action is None


def test_busy_defaults_to_60s_and_never_doubles():
    store = StatusStore()
    for _ in range(5):
        store.set_busy("p", "m", busy_seconds(None), "Too many requests", now=NOW)
    assert store.get("p", "m", now=NOW).until == NOW + 60


def test_busy_clears_on_its_own():
    store = StatusStore()
    store.set_busy("p", "m", 10, "x", now=NOW)
    assert store.get("p", "m", now=NOW + 11).value == st.READY


def test_busy_does_not_hide_a_needs_you():
    store = StatusStore()
    store.set_needs_you("p", "m", "Not on your plan.", kind="not_on_plan", action="retry", now=NOW)
    store.set_busy("p", "m", 10, "x", now=NOW)
    assert store.get("p", "m", now=NOW).value == st.NEEDS_YOU


# ---- Struggling -------------------------------------------------------------

def test_struggling_lasts_about_an_hour_with_try_now():
    store = StatusStore()
    store.set_struggling("p", "m", "Gave an empty reply.", now=NOW)
    s = store.get("p", "m", now=NOW)
    assert s.value == st.STRUGGLING
    assert s.action == "try_now"
    assert s.until == NOW + st.STRUGGLING_SECONDS
    assert store.get("p", "m", now=NOW + st.STRUGGLING_SECONDS + 1).value == st.READY


# ---- Needs you --------------------------------------------------------------

def test_needs_you_has_no_timer():
    store = StatusStore()
    store.set_needs_you("p", "m", "Your p balance is empty.", kind="balance_empty",
                        action="retry", now=NOW)
    s = store.get("p", "m", now=NOW + 10 * 365 * 86400)
    assert s.value == st.NEEDS_YOU
    assert s.until is None
    assert s.action == "retry"


def test_provider_wide_needs_you_covers_every_model():
    store = StatusStore()
    store.set_provider_needs_you("p", "p rejected every key.", kind="bad_key",
                                 action="replace_key", now=NOW)
    assert store.get("p", "anything", now=NOW).value == st.NEEDS_YOU
    assert store.get("other", "m", now=NOW).value == st.READY


def test_clear_returns_to_ready_and_emits_recovered():
    events = []
    store = StatusStore(on_event=lambda p, m, e, s: events.append(e))
    store.set_needs_you("p", "m", "x", kind="gone", action="remove", now=NOW)
    store.clear("p", "m")
    assert store.get("p", "m", now=NOW).value == st.READY
    assert events == ["needs_you", "recovered"]


# ---- What a failure becomes -------------------------------------------------

@pytest.mark.parametrize("code,kind,value,action", [
    (429, "rate_limited", st.BUSY, None),
    (503, "overloaded", st.BUSY, None),
    (500, "overloaded", st.BUSY, None),
    (None, "unreachable", st.BUSY, None),
    (402, "balance_empty", st.NEEDS_YOU, "retry"),
    (403, "not_on_plan", st.NEEDS_YOU, "retry"),
    (404, "gone", st.NEEDS_YOU, "remove"),
    (410, "gone", st.NEEDS_YOU, "remove"),
])
def test_classify_failure(code, kind, value, action):
    f = classify_failure(code, "googleai", "some text")
    assert (f.kind, f.value, f.action) == (kind, value, action)
    assert f.reason  # one plain sentence, never empty


def test_429_with_limit_zero_is_not_on_your_plan():
    body = ('[{"error": {"code": 429, "message": "Quota exceeded for metric: '
            'generate_content_free_tier_requests, limit: 0, model: gemini-2.5-pro"}}]')
    f = classify_failure(429, "googleai", body)
    assert f.value == st.NEEDS_YOU
    assert f.kind == "not_on_plan"


def test_503_is_only_ever_busy():
    f = classify_failure(503, "googleai", "The model is overloaded.")
    assert f.value == st.BUSY


@pytest.mark.parametrize("body,headers,expected", [
    ('{"details": [{"retryDelay": "40s"}]}', {}, 40),
    ("Please retry in 12.5s.", {}, 13),
    ("", {"Retry-After": "7"}, 7),
    ("", {"x-ratelimit-reset-requests": "2m"}, 120),
    ("", {}, None),
])
def test_parse_retry_after(body, headers, expected):
    assert parse_retry_after(body, headers) == expected


# ---- Persistence and migration ----------------------------------------------

def test_status_survives_restart(tmp_path):
    StatusStore(str(tmp_path)).set_needs_you("p", "m", "x", kind="gone", action="remove")
    assert StatusStore(str(tmp_path)).get("p", "m").value == st.NEEDS_YOU


def test_migrates_old_quarantine_and_penalties(tmp_path):
    import time
    now = time.time()
    (tmp_path / "quarantine.json").write_text(json.dumps({
        "googleai/gemini-3-flash": {"until": now + 999, "reason": "404 from googleai/gemini-3-flash: not found"},
        "llm7/ds": {"until": now + 999, "reason": "402 from llm7/ds: pay up"},
        "googleai/gemini-3.8-flash": {"until": now + 999, "reason": "auto-benched: 10% response rate"},
        "groq/*": {"until": now + 999, "reason": "Auth failure"},
    }))
    (tmp_path / "penalties.json").write_text(json.dumps({
        "mistral/small": {"until": now + 5000, "count": 6},
    }))
    store = StatusStore(str(tmp_path))
    assert store.get("googleai", "gemini-3-flash").kind == "gone"
    assert store.get("llm7", "ds").kind == "balance_empty"
    # The 7-day auto-bench for Google's own overload is dropped, not carried over.
    assert store.get("googleai", "gemini-3.8-flash").value == st.READY
    assert store.get("groq", "anything").value == st.NEEDS_YOU
    # A doubled penalty comes back as an ordinary Busy, capped at 60s.
    busy = store.get("mistral", "small")
    assert busy.value == st.BUSY and busy.until <= now + 61
    # Old files are set aside, so the migration runs once.
    assert not (tmp_path / "quarantine.json").exists()
    assert not (tmp_path / "penalties.json").exists()
    assert (tmp_path / "status.json").exists()


def test_corrupt_old_files_are_ignored(tmp_path):
    (tmp_path / "quarantine.json").write_text("{not json")
    store = StatusStore(str(tmp_path))
    assert store.all() == {}
