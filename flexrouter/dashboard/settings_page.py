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

import yaml

from flexrouter import app_password, home
from flexrouter.config import redact_settings_text, retired_settings_notice
from flexrouter.dashboard import facts, prefs, ui
from flexrouter.dashboard.render import attrs, esc, tag
from flexrouter.overrides import load_overrides

GROUPS = ("Server", "Failover", "Error brain", "History", "Conversations", "Advanced")

# name -> (group, plain label, unit, one line of help, needs a restart)
META: dict[str, tuple[str, str, str, str, bool]] = {
    "port": ("Server", "Port", "", "Where apps and this dashboard reach flexrouter.", True),
    "state_dir": ("Server", "Data folder", "", "Where logs, traces and learned state are kept.", True),
    "provider_budget": ("Failover", "Per-provider budget", "JSON",
                        "Caps on how much each provider may be used.", False),
    "window_seconds": ("Failover", "Rate window", "seconds",
                       "The window per-minute limits are counted over.", False),
    "failover_budget_seconds": ("Failover", "Give up after", "seconds",
                                "How long a request keeps trying other models in its bucket "
                                "before it gives up.", False),
    "probe_timeout_seconds": ("Failover", "Health check timeout", "seconds",
                              "How long a check on whether a provider is back may take.", False),
    "key_concurrency_cap": ("Failover", "Requests at once per key", "",
                            "More than this at the same time and the next key is used.", False),
    "decider_base_url": ("Error brain", "Classifier address", "",
                         "OpenAI-compatible endpoint of the AI that reads unfamiliar errors.", False),
    "decider_model": ("Error brain", "Classifier model", "", "Which model reads them.", False),
    "decider_timeout_seconds": ("Error brain", "Classifier timeout", "seconds",
                                "Give up on the classifier after this long.", False),
    "error_max_length": ("Error brain", "Longest error kept", "characters",
                         "Provider messages are cut to this length.", False),
    "redact_errors": ("Error brain", "Redact error text", "",
                      "Off by default: error text is shown exactly as the provider sent it, "
                      "including full model names. On, a heuristic also blanks anything shaped "
                      "like a credential (16+ letters/digits/-/./, in a row) wherever it appears "
                      "in an error - which can catch a provider/model name that isn't registered "
                      "yet along with it. A key flexrouter itself holds is masked either way.",
                      False),
    "health_history_days": ("History", "Keep health history for", "days", "", False),
    "sample_interval_seconds": ("History", "Health sample every", "seconds", "", False),
    "session_ttl_minutes": ("History", "Session lasts", "minutes",
                            "How long a conversation sticks to the model it started on.", False),
    "save_conversations": ("Conversations", "Save conversations", "",
                           "Keeps the prompt, reply and reasoning of every request so you can "
                           "look back at them.", False),
    "save_conversations_days": ("Conversations", "Keep conversations for", "days",
                                "How long a saved conversation is kept before it's deleted.", False),
    "hooks": ("Advanced", "Hooks", "JSON", "Code run on every request.", False),
    "unscored_fallback_score": ("Advanced", "Score for unscored models", "",
                                "Used to rank a model nobody has scored yet.", False),
    "dashboard_port": ("Advanced", "Dashboard port (old)", "",
                       "A leftover from when the dashboard had its own port; same as Port.", True),
    "auto_add_models": (
        "Advanced", "Add new models automatically", "",
        "Reading each provider's model list is always on; this controls "
        "whether new models get staged for you to accept.", False),
    "show_quickstart": ("Advanced", "Show quickstart", "",
                        "Shows the getting-started checklist until you hide it.", False),
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
    if isinstance(f.value, bool):
        # A switch: flips at once and saves itself (app.js POSTs
        # value=true|false to data-post), snapping back if the save fails.
        form = tag("div", ui.switch("", f.value,
                                    **{"data-post": f"/settings/{f.name}",
                                       "aria-label": label}),
                   cls="set-form set-bool")
    else:
        value = _display(f.value)
        form = tag("form",
                   f'<input type="text" name="value" value="{esc(value)}" '
                   f'aria-label="{esc(label)}" class="set-input">'
                   + (tag("span", esc(unit), cls="set-unit") if unit else "")
                   + ui.submit("Save", **{"data-working": "Saving", "data-done": "Saved"}),
                   method="post", action=f"/settings/{f.name}", cls="set-form")
    reset = (tag("form", ui.submit("Reset", kind="ghost", **{"data-working": "Resetting"}),
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


def _retired_notice() -> str:
    try:
        raw_settings = (yaml.safe_load(home.config_path().read_text(encoding="utf-8")) or {}).get(
            "settings") or {}
    except (OSError, yaml.YAMLError):
        raw_settings = {}
    override_settings = load_overrides().get("settings") or {}
    notice = retired_settings_notice(raw_settings, override_settings)
    return tag("p", esc(notice), cls="page-status set-retired") if notice else ""


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
                      + ui.button("Copy", kind="copy", icon_name="copy",
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
    actions = tag("form", ui.submit("Make a new password" if (generated or from_settings)
                              else "Make a password", kind="primary"),
                  method="post", action="/settings/app-password")
    if generated:
        actions += tag("form", ui.submit("Forget generated password", kind="ghost"),
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
               + ui.submit("Save preferences", **{"data-working": "Saving", "data-done": "Saved"}),
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
                      + ui.submit("Restore", **{"data-working": "Restoring"}),
                      method="post", action="/settings/restore", enctype="multipart/form-data",
                      cls="restore-form")
                + tag("form", ui.submit("Reset everything I changed", kind="danger",
                                        **{"data-working": "Resetting"}),
                      method="post", action="/settings/reset-all"),
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
    if router._cfg.auto_add_models:
        check = tag("form", ui.submit("Check for new models now", kind="test"),
                    method="post", action="/settings/refresh-models",
                    **{"data-busy": "Checking every provider with a valid key..."})
        sub = "new models found go to the Models page to accept"
    else:
        check = tag("p", "Turn on Add new models automatically above to check "
                         "providers for new models.", cls="set-help")
        sub = ""
    test_limits = tag("form",
                      ui.submit("Test rate limits for every model", kind="test"),
                      method="post", action="/settings/test-rate-limits", cls="set-actions",
                      **{"data-busy": "Sending one test message to every model..."})
    return ui.box("About", table + tag("div", check, cls="set-actions")
                  + test_limits
                  + tag("p", "Sends one short test message to every configured model and saves "
                             "whatever rate-limit numbers the provider hands back - the same "
                             "numbers a real request would teach it over time, just immediately "
                             "instead of waiting for traffic. Costs a little quota per model.",
                        cls="note"),
                  sub=sub, id="g-about", **{"data-enter": ""})


def body(router, banner: str, service_keys_html: str, shown_password: str = "",
         danger: str = "") -> str:
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
    return (head + _retired_notice() + banner
            + tag("div",
                  tag("nav", nav, cls="toc", **{"aria-label": "Settings sections"})
                  + tag("div",
                        _groups(router) + _app_password(router, shown_password)
                        + tag("div", service_keys_html, cls="box set-keys", id="g-keys")
                        + _prefs() + _backup() + _about(router) + danger,
                        cls="set-main"),
                  cls="set-layout"))
