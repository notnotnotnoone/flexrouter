"""The Status page: every model's one status, in one list (grill-decisions.md
§3-§4, the approved mockup). It replaces "What's broken" and "Error brain".

A summary strip on top (press a count to see only those); then the rows
that need you, each with one plain sentence and at most one button; the
ones struggling; the busy ones, with only a countdown; the errors the
error brain isn't sure about, to be told once; Ready and Off folded away.
Click a row for what the provider actually said and the model's last few
requests. The classifier's telemetry is one line at the bottom.
"""
from __future__ import annotations

import statistics
import time
from urllib.parse import quote

from flexrouter import status as st
from flexrouter.decider import VERDICTS
from flexrouter.dashboard import explain, facts, quickstart, ui
from flexrouter.dashboard.overview import KIND_LABELS, clock
from flexrouter.dashboard.render import esc, tag

GROUPS = ("needs", "struggling", "busy", "unsure", "ready", "off")
_HEADS = {"needs": "Needs you", "struggling": "Struggling", "busy": "Busy, sorting itself out",
          "unsure": "Not sure", "ready": "Ready", "off": "Off"}
_EMPTY = {"needs": "Nothing needs you.", "struggling": "Nothing is struggling.",
          "busy": "Nothing is busy.", "unsure": "", "ready": "", "off": ""}
_CHIP = {"needs": "need you", "struggling": "struggling", "busy": "busy", "unsure": "not sure",
         "ready": "ready", "off": "off"}
_GLYPH = {"needs": "▲", "struggling": "◆", "busy": "◐", "unsure": "?", "ready": "●", "off": "○"}
_GROUP_OF = {st.NEEDS_YOU: "needs", st.STRUGGLING: "struggling", st.BUSY: "busy",
             st.READY: "ready", st.OFF: "off"}


# What each error-brain verdict means, in the words the "Not sure" rows use.
LABELS = {
    "too_fast": "Too fast, slow down",
    "bad_key": "Bad key",
    "needs_payment": "Needs payment",
    "model_gone": "Model gone",
    "their_end_temporary": "Their end, temporary",
    "message_too_long": "Message too long",
    "bad_request": "Bad request",
    "unknown": "Unknown",
}


def _explain_button(prompt: str, label: str, ident: str, small: bool = False) -> str:
    """A copy-prompt button (§8) and the prompt it copies, kept hidden."""
    if not prompt:
        return ""
    src = tag("pre", esc(prompt), id=ident, hidden="")
    return src + ui.button(label, kind="copy", icon_name="" if small else "copy",
                           **{"data-copy": f"#{ident}",
                              "title": "Copy a prompt that explains this to a chatbot"})


def _prompts(router, items: list) -> dict:
    """One prompt per explainable row, keyed by id(item); traces read once."""
    traces = facts._read_traces(router, 5000)
    wanted = {explain.row_key(i) for i in explain.problems(router)}
    out: dict = {}
    for item in items:
        if explain.row_key(item) in wanted and not item.kind.startswith("key"):
            out[id(item)] = explain.build_prompt(router, only=explain.row_key(item),
                                                 traces=traces)
    return out


def _test_all_box(router) -> str:
    """[Test all] lives here too (§13): say hi to every model on demand."""
    models = quickstart._models(router)
    if not models:
        return ""
    results = (quickstart.load(router._cfg.state_dir).get("tested") or {})
    return ui.box("Say hi to every model",
                  quickstart.test_all(models, {m: results[m] for m in models if m in results},
                                      "status"),
                  sub="one tiny request each, only when you press it",
                  **{"data-enter": "", "data-box": "test-all"})


def _past(router) -> dict[str, list[str]]:
    """provider/model -> its last few tries, newest first, in plain words."""
    out: dict[str, list[str]] = {}
    for t in reversed(facts._read_traces(router, 2000)):
        when = clock(t.get("at", ""))
        for a in t.get("attempts") or []:
            ident = f"{a.get('provider')}/{a.get('model')}"
            if len(out.setdefault(ident, [])) < 5:
                out[ident].append(f"{when} · {a.get('status') or 'no answer'} "
                                  f"{(a.get('provider_message') or '')[:80]}".rstrip())
        who = t.get("answered_by")
        if who:
            ident = f"{who.get('provider')}/{who.get('model')}"
            if len(out.setdefault(ident, [])) < 5:
                out[ident].append(f"{when} · answered in {(t.get('ms_total') or 0) / 1000:.1f}s")
    return out


def _fix(item) -> str:
    """The row's one button, if it has one."""
    ident = f"{quote(item.provider, safe='')}/{item.model}"
    action = item.action or ""
    if item.kind in ("model_needs_you", "model_struggling") and item.model:
        if action.startswith("use:"):
            label, what = f"Use {action[4:]}", "use"
        elif action == st.TRY_NOW:
            label, what = "Try now", "retry"
        elif action == st.RETRY:
            label, what = "Retry", "retry"
        elif action == st.REMOVE:
            label, what = "Remove", "remove"
        else:
            return ""
        return ui.button(label, kind="fix", icon_name="wrench",
                         **{"data-st-action": f"/status/{what}/{ident}",
                            "data-working": "Fixing" if what == "use" else "Working",
                            "data-then": "off" if what == "remove" else "ready"})
    if item.kind in ("key_needs_you", "provider_needs_you"):
        return ui.button("Replace key" if item.kind == "key_needs_you" else "Open provider",
                         href=f"/providers/{quote(item.provider, safe='')}", kind="fix")
    return ""


def _row(group: str, name: str, why: str, *, n: int, until=None, act: str = "",
         raw: str = "", past: list | None = None, extra: str = "") -> str:
    detail_id = f"std-{n}"
    why_html = esc(why) + (" " + ui.countdown(until, prefix="· back in") if until else "")
    detail = ""
    if raw:
        detail += tag("div", "What the provider said", cls="sheet-h") + tag("pre", esc(raw))
    if past:
        detail += (tag("div", "Recent tries", cls="sheet-h")
                   + tag("ul", "".join(tag("li", esc(p)) for p in past), cls="st-past"))
    detail += extra
    if not detail:
        detail = tag("ul", tag("li", "Nothing went wrong recently."), cls="st-past")
    return (tag("div",
                tag("span", _GLYPH[group], cls="pill-dot", **{"aria-hidden": "true"})
                + tag("div", tag("span", esc(name), cls="st-name")
                      + tag("span", why_html, cls="st-why"), cls="st-what")
                + tag("div", act, cls="st-act")
                + tag("button", ui.icon("chevron"), type="button", cls="st-open",
                      **{"aria-expanded": "false", "aria-controls": detail_id,
                         "aria-label": f"Details for {name}"}),
                cls="st-row", **{"data-row": f"st:{name}", "data-status": group,
                                 "data-value": f"{group}:{why}"})
            + tag("div", detail, cls="st-detail", id=detail_id, hidden=""))


def _correct_form(fp: str, current: str) -> str:
    opts = "".join(
        f'<option value="{v}"{" selected" if v == current else ""}>{esc(LABELS[v])}</option>'
        for v in VERDICTS)
    return tag("form",
               tag("label", "It means ") + f'<select name="verdict" aria-label="What this error means">{opts}</select>'
               + ui.submit("Tell it", kind="fix", **{"data-working": "Saving", "data-done": "Saved"}),
               method="post", action=f"/brain/{quote(fp, safe='')}/verdict", cls="correct-form")


def _unsure_detail(fp: str, entry) -> str:
    """Where it happened, the requests it spoiled, what the classifier
    weighed - only once the row is open (§4) - and the one-time answer."""
    bits = ""
    if entry.where:
        bits += tag("div", "Where", cls="sheet-h") + tag("ul", "".join(
            tag("li", esc(f"{w} · {n}×")) for w, n in
            sorted(entry.where.items(), key=lambda kv: -kv[1])), cls="st-past")
    ids = [r.get("trace_id") for r in (entry.recent or []) if isinstance(r, dict) and r.get("trace_id")]
    if ids:
        bits += tag("div", "Requests it hit", cls="sheet-h") + tag("ul", "".join(
            tag("li", tag("a", esc(i), href=f"/requests?id={quote(i, safe='')}")) for i in ids[:5]),
            cls="st-past")
    probs = (entry.decision or {}).get("probabilities") or {}
    if probs:
        bits += tag("div", "What the classifier weighed", cls="sheet-h") + tag("ul", "".join(
            tag("li", esc(f"{float(p or 0):.0%} {LABELS.get(v, v)}"))
            for v, p in sorted(probs.items(), key=lambda kv: -float(kv[1] or 0))), cls="st-past")
    return bits + _correct_form(fp, entry.verdict)


def groups(router, now: float | None = None) -> dict[str, list[str]]:
    """Every row's markup, by group, plus the prompts for Explain with AI."""
    now = now if now is not None else time.time()
    data = facts.broken(router, now)
    past = _past(router)
    out: dict[str, list[str]] = {g: [] for g in GROUPS}
    items = [i for i in data["needs_you"] + data["handling_itself"] if i.kind != "unclear_error"]
    prompts = _prompts(router, items)
    shown: set = set()
    n = 0
    for item in items:
        n += 1
        group = _GROUP_OF.get(item.status, "needs" if item.pile == "you" else "busy")
        if item.kind == "key_busy":
            group = "busy"
        name = f"{item.provider}/{item.model}" if item.model else (
            f"{item.provider} · {item.detail}" if item.detail else item.provider)
        shown.add(name)
        ask = _explain_button(prompts.get(id(item), ""), "Explain with AI", f"explain-{n}",
                              small=True) if group != "busy" else ""
        out[group].append(_row(group, name, item.reason or KIND_LABELS.get(item.kind, ""),
                               n=n, until=item.until if group == "busy" else None,
                               act=_fix(item), raw=item.provider_text,
                               past=past.get(name), extra=ask))
    # the errors the classifier wasn't sure about: told once, then remembered
    for fp, entry in router._error_brain._entries.items():
        if not entry.flagged_for_review:
            continue
        n += 1
        where = max(entry.where, key=entry.where.get) if entry.where else ""
        out["unsure"].append(_row(
            "unsure", where or (entry.sample or "an error")[:90],
            f'{"HTTP " + str(entry.status) + ": " if entry.status else ""}guessed '
            f'"{LABELS.get(entry.verdict, entry.verdict)}", only {entry.confidence:.0%} sure. '
            "Say what it means once.",
            n=n, raw=entry.raw or entry.sample, extra=_unsure_detail(fp, entry)))
    # everything else that is configured is Ready
    seen: set = set()
    for bucket in router._cfg.tiers.values():
        for mc in bucket:
            ident = f"{mc.provider}/{mc.model}"
            if ident in seen or ident in shown:
                continue
            seen.add(ident)
            if facts.model_status(router, mc.provider, mc.model, now).value != st.READY:
                continue
            n += 1
            out["ready"].append(_row("ready", ident, "Ready", n=n, past=past.get(ident)))
    for ident in facts.disabled_models():
        n += 1
        provider, _, model = ident.partition("/")
        on = ui.button("Turn on", kind="ghost",
                       **{"data-st-action": f"/status/turn_on/{quote(provider, safe='')}/{model}",
                          "data-working": "Turning on", "data-then": "ready"})
        out["off"].append(_row("off", ident, "You turned it off.", n=n, act=on,
                               past=past.get(ident)))
    return out


def _classifier_line(router) -> str:
    """The error brain's telemetry, shrunk to one line (§4)."""
    brain = router._error_brain
    if not getattr(brain._decider, "configured", False):
        return "Error classifier: off, built-in rules only."
    calls = brain.decider_calls()
    if not calls:
        return f"Error classifier: {router._cfg.decider.model}, not asked anything yet."
    ok = sum(1 for c in calls if c.get("ok"))
    lat = [c["latency_ms"] for c in calls if c.get("latency_ms") is not None]
    cost = sum(float(c.get("cost") or 0) for c in calls)
    model = calls[0].get("model") or router._cfg.decider.model
    return (f"Error classifier: {model} · {ui.plural(len(calls), 'call')}, {ok / len(calls):.0%} "
            f"answered · median {statistics.median(lat):,.0f} ms · ${cost:.4f}"
            if lat else f"Error classifier: {model} · {ui.plural(len(calls), 'call')}")


def inner(router, rows: dict | None = None) -> str:
    rows = rows if rows is not None else groups(router)
    counts = {g: len(rows[g]) for g in GROUPS}
    strip = tag("div", "".join(
        tag("button", tag("b", esc(counts[g]), **{"data-value": counts[g]}) + esc(_CHIP[g]),
            type="button", cls="st-count", **{"data-status": g, "aria-pressed": "false"})
        for g in GROUPS if counts[g] or g in ("needs", "ready")),
        cls="st-strip", role="group", **{"aria-label": "Show only"})
    body = ""
    for g in GROUPS:
        if not rows[g] and not _EMPTY[g]:
            continue
        quiet = g in ("ready", "off")
        empty = tag("p", esc(_EMPTY[g]), cls="st-empty", hidden=None if not rows[g] else "") \
            if _EMPTY[g] else ""
        body += tag("div", tag("h3", esc(_HEADS[g]), cls="st-head") + "".join(rows[g]) + empty,
                    cls="st-group", **{"data-group": g, "data-quiet": "" if quiet else None,
                                       "hidden": "" if quiet else None})
    everything = explain.build_prompt(router)
    ask = _explain_button(everything, "Explain errors with AI", "explain-all") if everything else ""
    foot = tag("p", "Click any row for the full provider response and past requests. "
               + esc(_classifier_line(router)), cls="st-foot")
    return (_says(rows) + tag("div", strip + tag("span", "", cls="spacer") + ask, cls="st-top")
            + body + foot + _test_all_box(router))


def _says(rows: dict) -> str:
    """The one-line answer under the title; inside the live block, so a fix
    or a refresh keeps it true."""
    n = len(rows["needs"])
    return tag("p", esc(f"{n} thing{'s' if n != 1 else ''} need{'s' if n == 1 else ''} you" if n
                        else "Nothing needs you right now."), cls="page-status st-says",
               **{"data-row": "st-says", "data-value": n})


def body(router) -> str:
    rows = groups(router)
    head = tag("div", tag("div", tag("h1", "Status", cls="page-title"), cls="page-head-text"),
               cls="page-head")
    return head + tag("section", inner(router, rows), cls="st", id="status", **{
        "data-live": "", "hx-get": "/status?fragment=1",
        "hx-trigger": "every 10s [!document.hidden]", "hx-swap": "morph:innerHTML"})
