"""The Settings page: every setting in plain words, grouped by what you'd be
trying to do, plus the app password, dashboard preferences, backup and
restore, and what is running where.

Every router setting still writes to overrides.json, never the owner's
settings file (ADR 0002); the raw config name is shown under each plain
name so the two can always be matched up.
"""
from __future__ import annotations

import time
from pathlib import Path

from flexrouter import app_password, home
from flexrouter.config import redact_settings_text
from flexrouter.dashboard import facts, prefs, ui
from flexrouter.dashboard.render import attrs, esc, tag

GROUPS = ("Server", "Retries", "Sidelining bad providers", "Error brain", "History", "Advanced")

# name -> (group, plain label, unit, one line of help, needs a restart)
META: dict[str, tuple[str, str, str, str, bool]] = {
    "port": ("Server", "Port", "", "Where apps and this dashboard reach flexrouter.", True),
    "state_dir": ("Server", "Data folder", "", "Where logs, traces and learned state are kept.", True),
    "retries": ("Retries", "Tries per request", "times",
                "How many times a request is tried before giving up.", False),
    "backoff_seconds": ("Retries", "Wait between tries", "seconds",
                        "Pause after a rate limit before trying again.", False),
    "retry_policy": ("Retries", "Retry style", "",
                     "A named preset that sets the two values above (e.g. balanced).", False),
    "provider_budget": ("Retries", "Per-provider budget", "JSON",
                        "Caps on how much each provider may be used.", False),
    "window_seconds": ("Retries", "Rate window", "seconds",
                       "The window per-minute limits are counted over.", False),
    "penalty_base_seconds": ("Sidelining bad providers", "Sideline a failing provider for", "seconds",
                             "How long a provider is skipped after it errors. Doubles each time it "
                             "fails again.", False),
    "penalty_max_seconds": ("Sidelining bad providers", "Longest sideline", "seconds",
                            "The doubling above stops here.", False),
    "quarantine_seconds": ("Sidelining bad providers", "Quarantine a broken provider for", "seconds",
                           "How long a provider whose key was rejected is set aside.", False),
    "probe_timeout_seconds": ("Sidelining bad providers", "Health check timeout", "seconds",
                              "How long a check on whether a provider is back may take.", False),
    "key_concurrency_cap": ("Sidelining bad providers", "Requests at once per key", "",
                            "More than this at the same time and the next key is used.", False),
    "decider_base_url": ("Error brain", "Classifier address", "",
                         "OpenAI-compatible endpoint of the AI that reads unfamiliar errors.", False),
    "decider_model": ("Error brain", "Classifier model", "", "Which model reads them.", False),
    "decider_timeout_seconds": ("Error brain", "Classifier timeout", "seconds",
                                "Give up on the classifier after this long.", False),
    "decider_confidence_threshold": ("Error brain", "Review below", "0-1",
                                     "Guesses less sure than this are flagged for you.", False),
    "decider_rule_prior_confidence": ("Error brain", "Trust in built-in rules", "0-1",
                                      "How sure a built-in rule is for codes providers overload.",
                                      False),
    "decider_confidence_ceiling": ("Error brain", "Most the AI may claim", "0-1",
                                   "The AI's confidence is capped here.", False),
    "decider_contested_statuses": ("Error brain", "Codes the AI may overrule", "list",
                                   "Status codes where a rule is only a first guess.", False),
    "error_max_length": ("Error brain", "Longest error kept", "characters",
                         "Provider messages are cut to this length.", False),
    "health_history_days": ("History", "Keep health history for", "days", "", False),
    "sample_interval_seconds": ("History", "Health sample every", "seconds", "", False),
    "session_ttl_minutes": ("History", "Session lasts", "minutes",
                            "How long a conversation sticks to the model it started on.", False),
    "hooks": ("Advanced", "Hooks", "JSON", "Code run on every request.", False),
    "unscored_fallback_score": ("Advanced", "Score for unscored models", "",
                                "Used to rank a model nobody has scored yet.", False),
    "dashboard_port": ("Advanced", "Dashboard port (old)", "",
                       "A leftover from when the dashboard had its own port; same as Port.", True),
}


def _display(value) -> str:
    import json
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(list(value) if isinstance(value, tuple) else value)
    return "" if value is None else str(value)


def _row(f) -> str:
    group, label, unit, help_, restart = META.get(
        f.name, ("Advanced", f.name.replace("_", " ").capitalize(), "", "", False))
    changed = tag("span", "", cls="set-dot", title="changed here") if f.overridden else ""
    value = _display(f.value)
    form = tag("form",
               f'<input type="text" name="value" value="{esc(value)}" '
               f'aria-label="{esc(label)}" class="set-input">'
               + (tag("span", esc(unit), cls="set-unit") if unit else "")
               + tag("button", "Save", type="submit"),
               method="post", action=f"/settings/{f.name}", cls="set-form")
    reset = (tag("form", tag("button", "Reset", type="submit", cls="ghost-btn"),
                 method="post", action=f"/settings/{f.name}/clear", cls="set-reset")
             if f.overridden else "")
    where = tag("span", "changed here" if f.overridden else "from your settings file",
                cls="set-where" + (" is-changed" if f.overridden else ""))
    search = f"{label} {f.name} {help_} {group}".lower()
    return tag("div",
               tag("div",
                   tag("div", changed + tag("span", esc(label), cls="set-label")
                       + (ui.tag_("restart needed", "warn") if restart else ""), cls="set-title")
                   + (tag("p", esc(help_), cls="set-help") if help_ else "")
                   + tag("code", esc(f.name), cls="set-raw"),
                   cls="set-text")
               + tag("div", form + tag("div", where + reset, cls="set-meta"), cls="set-control"),
               cls="set-row", **{"data-search": search, "id": f"set-{f.name}"})


def _slug(group: str) -> str:
    return group.lower().replace(" ", "-")


def _groups(router) -> str:
    by_group: dict[str, list] = {g: [] for g in GROUPS}
    for f in facts.settings_fields(router):
        group = META.get(f.name, ("Advanced",))[0]
        by_group.setdefault(group, []).append(f)
    out = ""
    for g in GROUPS:
        rows = "".join(_row(f) for f in by_group.get(g) or [])
        if not rows:
            continue
        box = ui.box(g, rows, id=f"g-{_slug(g)}", cls="set-group",
                     **{"data-enter": ""})
        if g == "Advanced":
            box = tag("details", tag("summary", "Advanced settings", cls="set-adv") + box,
                      cls="set-advanced")
        out += box
    return out


def _app_password(router, shown: str = "") -> str:
    generated = app_password.current()
    from_settings = bool(router._cfg.auth_token)
    if shown:
        body = (tag("p", "Your new app password. Copy it now; it will not be shown again.",
                    cls="set-help")
                + tag("div", tag("code", esc(shown), cls="secret", id="new-secret")
                      + ui.button("Copy", icon_name="check",
                                  **{"data-copy": "#new-secret"}), cls="secret-row")
                + tag("p", "Apps send it as: Authorization: Bearer <password>. Any app still "
                           "using an older password gets a 401 from now on.", cls="note"))
    elif generated:
        body = tag("p", "A generated password is protecting /v1"
                   + (" (it overrides the auth_token in your settings file)" if from_settings else "")
                   + ".", cls="set-help")
    elif from_settings:
        body = tag("p", "Protected by the auth_token in your settings file.", cls="set-help")
    else:
        body = tag("p", "No password: any app on this machine can use /v1. flexrouter only "
                        "listens on this machine, so this is fine for most setups.", cls="set-help")
    actions = tag("form", tag("button", "Make a new password" if (generated or from_settings)
                              else "Make a password", type="submit", cls="primary-btn"),
                  method="post", action="/settings/app-password")
    if generated:
        actions += tag("form", tag("button", "Forget generated password", type="submit",
                                   cls="ghost-btn"),
                       method="post", action="/settings/app-password/clear")
    return ui.box("App password", body + tag("div", actions, cls="set-actions"),
                  id="g-app-password", **{"data-enter": ""})


def _prefs() -> str:
    p = prefs.load()

    def select(name, current, options):
        return (f'<select name="{name}" aria-label="{name}">'
                + "".join(f'<option value="{o}"{" selected" if o == current else ""}>{o}</option>'
                          for o in options) + "</select>")

    form = tag("form",
               tag("label", tag("span", "Animations") + select("motion", p.motion, prefs.MOTIONS),
                   cls="field")
               + tag("label", tag("span", "Refresh every (seconds)")
                     + f'<input type="number" name="refresh_seconds" min="2" max="3600" '
                       f'value="{p.refresh_seconds}">', cls="field")
               + tag("label", tag("span", "Default time range")
                     + select("default_range", p.default_range, prefs.RANGE_KEYS), cls="field")
               + tag("label", tag("span", "Timezone")
                     + f'<input type="text" name="timezone" value="{esc(p.timezone)}">', cls="field")
               + tag("button", "Save preferences", type="submit"),
               method="post", action="/settings/dashboard", cls="grouped-form")
    return ui.box("Dashboard", form, sub="stored in dashboard.json, not your settings file",
                  id="g-dashboard", **{"data-enter": ""})


def _backup() -> str:
    config = home.config_path()
    try:
        text = redact_settings_text(Path(config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        text = "(no settings file found)"
    body = (tag("div",
                ui.button("Download backup", href="/settings/backup", icon_name="arrow-right",
                          download="flexrouter-backup.json", **{"hx-boost": "false"})
                + tag("form",
                      '<input type="file" name="backup" accept="application/json" required '
                      'aria-label="Backup file">'
                      + tag("button", "Restore", type="submit"),
                      method="post", action="/settings/restore", enctype="multipart/form-data",
                      cls="restore-form")
                + tag("form", tag("button", "Reset everything I changed", type="submit",
                                  cls="danger"),
                      method="post", action="/settings/reset-all",
                      **{"data-confirm": "Undo every change made from this dashboard? "
                                        "Your settings file is not touched."}),
                cls="set-actions")
            + tag("p", "A backup holds every change made from this dashboard and your dashboard "
                       "preferences. Keys are never included.", cls="note")
            + tag("details", tag("summary", "Your settings file (read only, keys hidden)")
                  + tag("pre", esc(text), cls="config-view"), cls="table-view"))
    return ui.box("Backup and restore", body, id="g-backup", **{"data-enter": ""})


_STARTED = time.time()


def _about(router) -> str:
    from flexrouter.dashboard.render import _version
    rows = [("Version", _version() or "development"),
            ("Running for", ui.duration(int(time.time() - _STARTED))),
            ("Home", str(home.home_dir())),
            ("Settings file", str(home.config_path())),
            ("Keys", str(home.keys_path())),
            ("Data", str(router._cfg.state_dir))]
    table = "".join(tag("div", tag("span", esc(k), cls="stat-label") + tag("code", esc(v)),
                        cls="about-row") for k, v in rows)
    check = tag("form", tag("button", "Check for new models now", type="submit"),
                method="post", action="/settings/refresh-models",
                **{"data-busy": "Checking every provider with a valid key..."})
    test_limits = tag("form",
                      tag("button", "Test rate limits for every model", type="submit"),
                      method="post", action="/settings/test-rate-limits", cls="set-actions",
                      **{"data-busy": "Sending one test message to every model..."})
    return ui.box("About", table + tag("div", check, cls="set-actions")
                  + test_limits
                  + tag("p", "Sends one short test message to every configured model and saves "
                             "whatever rate-limit numbers the provider hands back - the same "
                             "numbers a real request would teach it over time, just immediately "
                             "instead of waiting for traffic. Costs a little quota per model.",
                        cls="note"),
                  sub="new models found go to the Models page to accept",
                  id="g-about", **{"data-enter": ""})


def body(router, banner: str, service_keys_html: str, shown_password: str = "") -> str:
    nav = "".join(tag("a", esc(g), href=f"#g-{_slug(g)}", cls="toc-link")
                  for g in (*GROUPS[:-1], "App password", "Dashboard", "Backup", "About"))
    head = tag("div",
               tag("div", tag("h1", "Settings", cls="page-title")
                   + tag("p", "Changes land in a file layered over your settings file, never in "
                              "it. Reset puts one setting back.", cls="page-status"),
                   cls="page-head-text")
               + tag("div", ui.icon("search")
                     + '<input type="search" placeholder="Find a setting" aria-label="Find a setting" '
                       'data-page-search data-filter=".set-row">', cls="page-actions set-search"),
               cls="page-head")
    return (head + banner
            + tag("div",
                  tag("nav", nav, cls="toc", **{"aria-label": "Settings sections"})
                  + tag("div",
                        _groups(router) + _app_password(router, shown_password)
                        + tag("div", service_keys_html, cls="box set-keys", id="g-keys")
                        + _prefs() + _backup() + _about(router),
                        cls="set-main"),
                  cls="set-layout"))
