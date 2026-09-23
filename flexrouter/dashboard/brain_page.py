"""The Error brain: every kind of provider error flexrouter has learned,
grouped by what it decided each one means - and a way to correct it.

Each card carries the whole story: the status code, the provider's full
response body, which models hit it and when, and what the classifier said.
The classifier panel on top shows whether the classifier is working at all,
because a classifier that fails on every call looks exactly like one that
is never asked.

A correction is saved as the owner's own answer (`source="manual"`) and is
never overruled by a rule or the classifier afterwards (error_brain.py).
"""
from __future__ import annotations

import statistics
from urllib.parse import quote

from flexrouter.decider import VERDICTS
from flexrouter.dashboard import ui
from flexrouter.dashboard.overview import clock
from flexrouter.dashboard.render import esc, tag

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
# source -> (tag text, tag colour)
_SOURCE = {"rule": ("rule", ""), "classifier": ("ai", "violet"),
           "manual": ("you", "ok"), "null": ("no one", "")}
_TRANSPORT = {"decisions": "OpenRouter Decisions API", "chat/completions": "chat completions"}


def _pct(x) -> str:
    return f"{float(x):.0%}"


def _cost(x) -> str:
    return f"${float(x):.6f}"


def _correct_form(fp: str, current: str) -> str:
    opts = "".join(
        f'<option value="{v}"{" selected" if v == current else ""}>{esc(LABELS[v])}</option>'
        for v in VERDICTS)
    return tag("form",
               f'<select name="verdict" aria-label="What this error means">{opts}</select>'
               + tag("button", "Set", type="submit"),
               method="post", action=f"/brain/{quote(fp, safe='')}/verdict", cls="correct-form")


def _probabilities(probs: dict) -> str:
    """Every option the classifier weighed, most likely first."""
    rows = ""
    for verdict, p in sorted(probs.items(), key=lambda kv: -float(kv[1] or 0)):
        rows += tag("div",
                    tag("span", esc(LABELS.get(verdict, verdict)), cls="brain-prob-name")
                    + ui.meter(float(p or 0), cells=20, label=verdict, share=True)
                    + tag("span", esc(_pct(p or 0)), cls="n"),
                    cls="brain-prob")
    return tag("div", rows, cls="brain-probs")


def _call_facts(d: dict) -> str:
    """One line of what a classifier call cost and returned."""
    bits = []
    if d.get("http_status") is not None:
        bits.append(f"HTTP {d['http_status']}")
    if d.get("latency_ms") is not None:
        bits.append(f"{d['latency_ms']:,} ms")
    if d.get("cost") is not None:
        bits.append(_cost(d["cost"]))
    if d.get("input_tokens") is not None:
        bits.append(f"{d['input_tokens']:,} tokens in")
    if d.get("served_model"):
        bits.append(f"served by {d['served_model']}")
    if d.get("generation_id"):
        bits.append(d["generation_id"])
    return tag("div", esc(" · ".join(bits)), cls="brain-facts n")


def _decision(d: dict) -> str:
    if not d.get("ok"):
        return (tag("p", esc(f"The classifier had no opinion "
                             f"({_TRANSPORT.get(d.get('transport'), d.get('transport') or '?')}):"),
                    cls="brain-note")
                + _call_facts(d)
                + tag("pre", esc(d.get("error") or "no error text"), cls="brain-raw"))
    said = LABELS.get(d.get("verdict"), d.get("verdict"))
    raw = d.get("raw_confidence")
    head = f"Said {said} at {_pct(d.get('confidence', 0))}"
    if raw is not None and raw != d.get("confidence"):
        head += f" (it reported {_pct(raw)}, capped)"
    if d.get("rule_said"):
        rule = LABELS.get(d["rule_said"], d["rule_said"])
        head += (f" - overturned the rule, which said {rule}" if d.get("overturned_rule")
                 else f" - the rule ({rule}) stood")
    return (tag("p", esc(head), cls="brain-note") + _call_facts(d)
            + (_probabilities(d["probabilities"]) if d.get("probabilities") else ""))


def _occurrences(e) -> str:
    where = ""
    if e.where:
        where = tag("div", "".join(
            tag("span", esc(f"{name} ×{count:,}"), cls="brain-where")
            for name, count in sorted(e.where.items(), key=lambda kv: -kv[1])), cls="brain-wheres")
    rows = ""
    for o in e.recent:
        what = f"{o.get('provider')}/{o.get('model')}" if o.get("provider") else "-"
        status = f"HTTP {o['status']}" if o.get("status") is not None else "no status"
        link = (tag("a", esc(o["trace_id"]), href=f"/requests?id={quote(o['trace_id'], safe='')}")
                if o.get("trace_id") else "")
        rows += tag("li", tag("span", esc(clock(o.get("at", ""))), cls="n")
                    + tag("span", esc(what)) + tag("span", esc(status), cls="n") + link)
    return where + (tag("ol", rows, cls="brain-recent") if rows else "")


def _card(fp: str, e) -> str:
    source, color = _SOURCE.get(e.source, (e.source, ""))
    tags = ui.tag_(source, color)
    if e.status is not None:
        tags = ui.tag_(f"HTTP {e.status}") + tags
    if e.flagged_for_review:
        tags = ui.tag_("needs review", "warn") + tags
    seen = f"seen {e.seen:,}×" if e.seen != 1 else "seen once"
    more = ""
    if e.raw:
        more += tag("details", tag("summary", "Full provider response")
                    + tag("pre", esc(e.raw), cls="brain-raw"), open=True)
    if e.decision:
        more += tag("details", tag("summary", "What the classifier said")
                    + _decision(e.decision), open=True)
    if e.where or e.recent:
        more += tag("details", tag("summary", f"Where it happened ({len(e.recent)} most recent)")
                    + _occurrences(e))
    return tag("div",
               tag("div", tags + tag("span", "", cls="spacer")
                   + tag("span", esc(f"{seen} · first {clock(e.first_at)} · last {clock(e.last_at)}"),
                         cls="n"),
                   cls="brain-top")
               + tag("pre", esc(e.sample), cls="brain-sample")
               + more
               + tag("div",
                     tag("span", "sure", cls="brain-label")
                     + ui.meter(e.confidence, cells=10, label="confidence", share=True)
                     + tag("span", esc(f"{e.confidence:.0%}"), cls="n")
                     + tag("span", "", cls="spacer")
                     + _correct_form(fp, e.verdict),
                     cls="brain-foot"),
               cls="card brain-card" + (" is-review" if e.flagged_for_review else ""),
               **{"data-row": f"brain:{fp}", "data-value": f"{e.verdict}/{e.seen}"})


def _classifier(router, brain) -> str:
    """Is the classifier set up, and is it actually answering?"""
    decider = brain._decider
    cfg = router._cfg.decider
    if not getattr(decider, "configured", False):
        return ui.box("Classifier", tag("p", esc(
            "No classifier set up - built-in rules only. Unfamiliar errors are filed as "
            "Unknown. Set a decider model, endpoint and key in Settings to have one "
            "(for example ~typesafe/jev-latest on OpenRouter) weigh in."), cls="brain-note"),
            sub="off", **{"data-box": "classifier"})

    calls = brain.decider_calls()
    # What the last call actually used beats what settings say it should.
    last = calls[0] if calls else {}
    model = last.get("model") or cfg.model
    transport = last.get("transport") or (
        "decisions" if type(decider).__name__ == "DecisionsDecider" else "chat/completions")
    where = f" at {cfg.base_url}" if cfg.base_url else ""
    who = tag("p", esc(f"{model} via {_TRANSPORT.get(transport, transport)}{where}"),
              cls="brain-note")
    if not calls:
        return ui.box("Classifier", who + tag("p", esc("Not asked anything yet."), cls="brain-note"),
                      sub="waiting", **{"data-box": "classifier"})

    ok = [c for c in calls if c.get("ok")]
    latencies = [c["latency_ms"] for c in calls if c.get("latency_ms") is not None]
    cost = sum(float(c.get("cost") or 0) for c in calls)
    overturned = sum(1 for c in ok if c.get("overturned_rule"))
    stats = tag("div",
                ui.stat("Calls", f"{len(calls):,}", key="dec-calls", note="most recent 200")
                + ui.stat("Answered", _pct(len(ok) / len(calls)), key="dec-ok",
                          note=f"{len(calls) - len(ok):,} failed")
                + ui.stat("Median latency",
                          f"{statistics.median(latencies):,.0f} ms" if latencies else "-",
                          key="dec-latency")
                + ui.stat("Cost", _cost(cost), key="dec-cost")
                + ui.stat("Overturned a rule", f"{overturned:,}", key="dec-overturned"),
                cls="stats")

    last_fail = next((c for c in calls if not c.get("ok")), None)
    failing = ""
    if last_fail is not None:
        failing = tag("div",
                      tag("p", esc(f"Last failure, {clock(last_fail.get('at', ''))}:"),
                          cls="brain-note")
                      + _call_facts(last_fail)
                      + tag("pre", esc(last_fail.get("error") or "no error text"), cls="brain-raw"),
                      cls="brain-failure" + (" is-current" if not calls[0].get("ok") else ""))

    rows = ""
    for c in calls[:10]:
        what = (f"{c.get('error_provider')}/{c.get('error_model')}"
                if c.get("error_provider") else "")
        status = "HTTP " + str(c["status"]) if c.get("status") is not None else "no status"
        head = tag("div",
                   tag("span", esc(clock(c.get("at", ""))), cls="n")
                   + tag("span", esc(what)) + tag("span", esc(status), cls="n")
                   + (tag("a", esc(c["trace_id"]),
                          href=f"/requests?id={quote(c['trace_id'], safe='')}")
                      if c.get("trace_id") else ""),
                   cls="brain-call-head")
        rows += tag("li", head + tag("pre", esc(c.get("error_text") or ""), cls="brain-sample")
                    + _decision(c))
    recent = tag("details", tag("summary", "Recent calls, newest first")
                 + tag("ol", rows, cls="brain-calls"), open=True)
    state = "working" if calls[0].get("ok") else "failing"
    return ui.box("Classifier", who + stats + failing + recent, sub=state,
                  **{"data-box": "classifier"})


def body(router, banner: str = "") -> str:
    brain = router._error_brain
    items = list(brain._entries.items())
    review = sum(1 for _, e in items if e.flagged_for_review)
    status = (f"{len(items)} kinds of error learned" + (f" · {review} need review" if review else "")
              if items else "Nothing learned yet.")
    head = tag("div", tag("div", tag("h1", "Error brain", cls="page-title")
                          + tag("p", esc(status), cls="page-status"), cls="page-head-text"),
               cls="page-head")
    classifier = _classifier(router, brain)
    if not items:
        return head + banner + tag("div", classifier, cls="ov") + ui.empty(
            "No provider errors seen yet. When a provider fails in a way flexrouter "
            "hasn't met before, it learns what that error means and it shows up here.")
    groups: dict[str, list] = {}
    for fp, e in items:
        groups.setdefault(e.verdict, []).append((fp, e))
    order = sorted(groups, key=lambda v: (not any(e.flagged_for_review for _, e in groups[v]),
                                          -len(groups[v]), v))
    sections = classifier
    for verdict in order:
        entries = sorted(groups[verdict], key=lambda p: (not p[1].flagged_for_review, p[1].last_at),
                         reverse=False)
        cards = "".join(_card(fp, e) for fp, e in entries)
        sections += ui.box(LABELS.get(verdict, verdict), tag("div", cards, cls="col-body"),
                           sub=f"{len(entries)} kind" + ("" if len(entries) == 1 else "s"),
                           **{"data-enter": "", "data-box": f"verdict-{verdict}"})
    return head + banner + tag("div", sections, cls="ov")
