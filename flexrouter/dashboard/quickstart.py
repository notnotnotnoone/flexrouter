"""The "Get started" card on top of Overview (grill-decisions.md §12-§13).

Five steps, each ticking itself when the thing it asks for has really
happened; there are no Next buttons. The card never writes a settings file
(ADR 0007/0022): it points at the normal pages, and the only things it
remembers are in state/quickstart.json - that a key test passed, and what
each model said when Test all said hi.

Shown when the "Show quickstart" setting is on, and whenever no model
works at all, hidden or not.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from flexrouter import presets
from flexrouter.store import read_json, write_json

FILENAME = "quickstart.json"
# Requests the dashboard sends itself; they don't prove your app is wired up.
DASHBOARD_CLIENTS = ("playground", "dashboard-test")


def _path(state_dir: str) -> Path:
    return Path(state_dir) / FILENAME


def load(state_dir: str) -> dict:
    data = read_json(_path(state_dir), default={}) or {}
    return data if isinstance(data, dict) else {}


def _save(state_dir: str, data: dict) -> None:
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    write_json(_path(state_dir), data)


def record_key_ok(state_dir: str, provider: str) -> None:
    data = load(state_dir)
    data["key_ok"] = {"provider": provider, "at": time.time()}
    _save(state_dir, data)


def record_test(state_dir: str, ident: str, ok: bool, message: str) -> None:
    data = load(state_dir)
    data.setdefault("tested", {})[ident] = {"ok": ok, "message": message, "at": time.time()}
    _save(state_dir, data)


@dataclass
class Step:
    n: int
    title: str
    done: bool
    sub: str = ""          # plain text under the title
    sub_html: str = ""     # or markup, when the sub needs links
    ok_sub: bool = False   # the sub reports a success (green, mono)
    extra: dict = field(default_factory=dict)


def _models(router) -> list[str]:
    seen: list[str] = []
    for bucket in router._cfg.tiers.values():
        for m in bucket:
            ident = f"{m.provider}/{m.model}"
            if getattr(m, "enabled", True) is not False and ident not in seen:
                seen.append(ident)
    return seen


def _first_app_request(router) -> Optional[str]:
    """When a request that wasn't the dashboard's own first arrived."""
    from flexrouter.dashboard import facts
    for row in reversed(facts.recent_requests(router, limit=500)):
        if (row.client or "") not in DASHBOARD_CLIENTS:
            return row.at
    return None


def steps(router, base_url: str) -> list[Step]:
    state = load(router._cfg.state_dir)
    providers = list(router._cfg.providers)
    found = presets.all()

    # 1. a provider
    one = Step(1, "Add a provider", bool(providers))
    if providers:
        one.sub = "Added: " + ", ".join(found[p].label if p in found else p for p in providers)
    else:
        one.extra["presets"] = [p for p in found.values()]

    # 2. its key works
    from flexrouter.dashboard import facts
    key_ok = state.get("key_ok")
    answered = any(r.ok for r in facts.recent_requests(router, limit=200))
    two = Step(2, "Paste its key", bool(key_ok) or answered)
    if key_ok:
        p = key_ok.get("provider", "")
        two.sub, two.ok_sub = f"{found[p].label if p in found else p} ✓ key works", True
    elif answered:
        two.sub, two.ok_sub = "✓ a model has answered", True
    else:
        two.sub = "Press Test next to the key; this ticks when it answers."
        two.extra["provider"] = providers[0] if providers else ""

    # 3. models
    models = _models(router)
    three = Step(3, "Add models with AI", bool(models))
    if models:
        n_buckets = sum(1 for b in router._cfg.tiers.values() if b)
        three.sub = (f"{len(models)} model{'s' if len(models) != 1 else ''} in "
                     f"{n_buckets} bucket{'s' if n_buckets != 1 else ''}")
        three.ok_sub = True
    else:
        three.sub = "We check every ID against the provider's real list."

    # 4. say hi to every model
    tested = state.get("tested") or {}
    results = {m: tested[m] for m in models if m in tested}
    good = sum(1 for r in results.values() if r.get("ok"))
    # Every model asked, and at least one answered: a run where nothing
    # answered isn't "done", it's the thing to go and fix.
    four = Step(4, "Say hi to every model", bool(models) and len(results) == len(models) and good > 0)
    if len(results) == len(models) and models and not good:
        four.sub = f"None of the {len(models)} answered. Status says why."
    elif four.done:
        bad = len(results) - good
        four.sub = f"{good} of {len(models)} answered" + (f" · {bad} didn't, see Status" if bad else "")
        four.ok_sub = not bad
    else:
        four.sub = "One tiny request each (\"hi\", up to 512 tokens), so you know they all answer."
    four.extra["models"] = models
    four.extra["results"] = results

    # 5. your app
    first = _first_app_request(router)
    five = Step(5, "Point your app at flexrouter", bool(first))
    with_models = [name for name, ms in router._cfg.tiers.items() if ms]
    bucket = "fast" if "fast" in with_models else (with_models[0] if with_models else "fast")
    five.extra.update(base_url=base_url, bucket=bucket)
    if first:
        from flexrouter.dashboard.overview import clock
        five.sub, five.ok_sub = f"First request arrived at {clock(first)}", True
    return [one, two, three, four, five]


def should_show(router, model_works: bool) -> bool:
    return bool(getattr(router._cfg, "show_quickstart", True)) or not model_works


# ── the card ─────────────────────────────────────────────────────────

def _preset_list(found: list) -> str:
    from flexrouter.dashboard.render import esc, tag

    def one(p) -> str:
        link = (" " + tag("a", "Get a key ↗", href=p.key_url or p.signup_url, target="_blank",
                          rel="noopener") if (p.key_url or p.signup_url) else "")
        return tag("span", tag("a", esc(p.label), href=f"/providers/add/{p.name}") + link,
                   cls="qs-preset", title=p.free_tier_note or None)

    free = [p for p in found if p.free]
    paid = [p for p in found if not p.free]
    return (tag("span", tag("b", "Free: ") + " · ".join(one(p) for p in free), cls="qs-sub")
            + (tag("span", tag("b", "Paid: ") + " · ".join(one(p) for p in paid), cls="qs-sub")
               if paid else ""))


def _test_list(models: list[str], results: dict) -> str:
    """Test all's rows: one per model, with what it said last time."""
    from flexrouter.dashboard import ui
    from flexrouter.dashboard.render import esc, tag
    rows = ""
    for ident in models:
        r = results.get(ident)
        state = "" if r is None else ("done" if r.get("ok") else "failed")
        glyph = {"": "dot", "done": "check", "failed": "x"}[state]
        out = "not tested" if r is None else r.get("message", "")
        provider, _, model = ident.partition("/")
        rows += tag("li", tag("span", ui.icon(glyph), cls="test-mark")
                    + tag("span", esc(ident)) + tag("span", esc(out), cls="test-out"),
                    cls="test-row", **{"data-row": f"test:{ident}", "data-state": state or None,
                                       "data-provider": provider, "data-model": model})
    return tag("ul", rows, cls="tests")


def test_all(models: list[str], results: dict, ident: str = "ta") -> str:
    """[Test all], its meter and its rows. Used by the quickstart and on
    Status. Only runs on a click; nothing pings in the background."""
    from flexrouter.dashboard import ui
    from flexrouter.dashboard.render import tag
    if not models:
        return ""
    meter = tag("div", "".join(
        tag("i", "", cls={True: "on", False: "bad"}.get(
            results[m].get("ok")) if m in results else None) for m in models),
        cls="meter", id=f"{ident}-meter", role="meter", **{"aria-label": "Models tested"})
    btn = ui.button("Test all", kind="test", icon_name="zap",
                    **{"data-test-all": f"#{ident}-list", "data-meter": f"{ident}-meter"})
    return (tag("div", btn + meter, cls="ta-head")
            + ui.fold(f"Every model ({len(models)})", _test_list(models, results),
                      note="what each one said", body_cls="").replace(
                '<div class="fold-body">', f'<div class="fold-body" id="{ident}-list">', 1))


def card(router, base_url: str, model_works: bool) -> str:
    """The Get started card, or "" when it shouldn't show."""
    if not should_show(router, model_works):
        return ""
    from flexrouter.dashboard import ui
    from flexrouter.dashboard.render import esc, tag
    all_steps = steps(router, base_url)
    done = sum(1 for s in all_steps if s.done)
    now = next((s for s in all_steps if not s.done), None)
    items = ""
    for s in all_steps:
        state = "done" if s.done else ("now" if s is now else "todo")
        mark = ui.icon("check") if s.done else ("▶" if state == "now" else str(s.n))
        if s.n == 1 and not s.done:
            sub = _preset_list(s.extra["presets"])
        else:
            sub = tag("span", esc(s.sub), cls="qs-sub" + (" ok" if s.ok_sub else "")) if s.sub else ""
        act, progress = "", ""
        if s.n == 1:
            act = ui.button("Add a provider", href="/providers", kind="primary")
        elif s.n == 2 and s.extra.get("provider"):
            act = ui.button("Test its key", href=f"/providers/{s.extra['provider']}", kind="primary")
        elif s.n == 3:
            act = ui.button("Open", href="/models_catalog/add-with-ai", kind="primary",
                            icon_name="arrow-right")
        elif s.n == 4:
            progress = tag("div", test_all(s.extra["models"], s.extra["results"], "qs"),
                           cls="qs-progress")
        elif s.n == 5:
            url, bucket = s.extra["base_url"], s.extra["bucket"]
            sub = tag("span", "Base URL " + tag("code", esc(url)) + ", model "
                      + tag("code", esc(f'"{bucket}"')), cls="qs-sub") + (sub if s.done else "")
            python = (f'from openai import OpenAI\n'
                      f'client = OpenAI(base_url="{url}", api_key="x")\n'
                      f'reply = client.chat.completions.create(model="{bucket}", '
                      f'messages=[{{"role": "user", "content": "hi"}}])\n'
                      f'print(reply.choices[0].message.content)')
            curl = (f"curl {url}/chat/completions -H 'Content-Type: application/json' "
                    f"-d '{{\"model\": \"{bucket}\", \"messages\": "
                    f"[{{\"role\": \"user\", \"content\": \"hi\"}}]}}'")
            act = (ui.button("Copy Python", kind="copy", icon_name="copy", **{"data-copy-text": python})
                   + ui.button("Copy curl", kind="copy", icon_name="copy", **{"data-copy-text": curl}))
        items += tag("li",
                     tag("span", mark, cls="qs-mark")
                     + tag("div", tag("span", esc(s.title), cls="qs-title") + sub)
                     + tag("div", act, cls="qs-act") + progress,
                     cls="qs-step", **{"data-step": state, "data-n": s.n,
                                       "data-row": f"qs:{s.n}", "data-value": state})
    complete = done == len(all_steps)
    done_line = tag("div", ui.icon("check") + tag("span", "You're set up. flexrouter is routing.")
                    + ui.button("Hide for good", kind="ghost", **{"data-qs-hide": ""}),
                    cls="qs-done-line", hidden=None if complete else "")
    bar = tag("div", "".join(tag("i", "", cls="on" if i < done else None)
                             for i in range(len(all_steps))), cls="qs-bar",
              **{"aria-hidden": "true"})
    return ui.box("Get started", done_line + bar + tag("ol", items, cls="qs-steps"),
                  sub=f"{done} of {len(all_steps)} done",
                  action=ui.button("Hide", kind="ghost", **{"data-qs-hide": ""}),
                  cls="qs" + (" is-complete" if complete else ""),
                  **{"data-box": "quickstart", "id": "qs"})
