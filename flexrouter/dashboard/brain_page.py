"""The Error brain: every kind of provider error flexrouter has learned,
grouped by what it decided each one means - and a way to correct it.

A correction is saved as the owner's own answer (`source="manual"`) and is
never overruled by a rule or the classifier afterwards (error_brain.py).
"""
from __future__ import annotations

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


def _correct_form(fp: str, current: str) -> str:
    opts = "".join(
        f'<option value="{v}"{" selected" if v == current else ""}>{esc(LABELS[v])}</option>'
        for v in VERDICTS)
    return tag("form",
               f'<select name="verdict" aria-label="What this error means">{opts}</select>'
               + tag("button", "Set", type="submit"),
               method="post", action=f"/brain/{quote(fp, safe='')}/verdict", cls="correct-form")


def _card(fp: str, e) -> str:
    source, color = _SOURCE.get(e.source, (e.source, ""))
    tags = ui.tag_(source, color)
    if e.flagged_for_review:
        tags = ui.tag_("needs review", "warn") + tags
    seen = f"seen {e.seen:,}×" if e.seen != 1 else "seen once"
    return tag("div",
               tag("div", tags + tag("span", "", cls="spacer")
                   + tag("span", esc(f"{seen} · last {clock(e.last_at)}"), cls="n"),
                   cls="brain-top")
               + tag("pre", esc(e.sample), cls="brain-sample")
               + tag("div",
                     tag("span", "sure", cls="brain-label")
                     + ui.meter(e.confidence, cells=10, label="confidence", share=True)
                     + tag("span", esc(f"{e.confidence:.0%}"), cls="n")
                     + tag("span", "", cls="spacer")
                     + _correct_form(fp, e.verdict),
                     cls="brain-foot"),
               cls="card brain-card" + (" is-review" if e.flagged_for_review else ""),
               **{"data-row": f"brain:{fp}", "data-value": f"{e.verdict}/{e.seen}"})


def body(router, banner: str = "") -> str:
    brain = router._error_brain
    items = list(brain._entries.items())
    review = sum(1 for _, e in items if e.flagged_for_review)
    status = (f"{len(items)} kinds of error learned" + (f" · {review} need review" if review else "")
              if items else "Nothing learned yet.")
    head = tag("div", tag("div", tag("h1", "Error brain", cls="page-title")
                          + tag("p", esc(status), cls="page-status"), cls="page-head-text"),
               cls="page-head")
    if not items:
        return head + banner + ui.empty(
            "No provider errors seen yet. When a provider fails in a way flexrouter "
            "hasn't met before, it learns what that error means and it shows up here.")
    groups: dict[str, list] = {}
    for fp, e in items:
        groups.setdefault(e.verdict, []).append((fp, e))
    order = sorted(groups, key=lambda v: (not any(e.flagged_for_review for _, e in groups[v]),
                                          -len(groups[v]), v))
    sections = ""
    for verdict in order:
        entries = sorted(groups[verdict], key=lambda p: (not p[1].flagged_for_review, p[1].last_at),
                         reverse=False)
        cards = "".join(_card(fp, e) for fp, e in entries)
        sections += ui.box(LABELS.get(verdict, verdict), tag("div", cards, cls="col-body"),
                           sub=f"{len(entries)} kind" + ("" if len(entries) == 1 else "s"),
                           **{"data-enter": "", "data-box": f"verdict-{verdict}"})
    return head + banner + tag("div", sections, cls="ov")
