"""Buckets: what each bucket would pick from right now, and why.

The engine does not try models in list order: it picks at random among
every available model within 20% of the best available score
(`facts.buckets`, from the engine's own `explain_unavailable`). So each
bucket is drawn as a score ladder with that cut-off marked, and the models
that could answer a request right now lit up.

"Try it" sends one real, one-token request through the bucket (the owner
approved the small cost) and shows who answered.
"""
from __future__ import annotations

from flexrouter.dashboard import facts, ui
from flexrouter.dashboard.render import esc, tag

_REASON = {  # engine._skip_reason names, in plain words
    "penalized": "resting after errors",
    "quarantined": "provider set aside",
    "provider_rate_limit": "provider says slow down",
    "quota_exhausted": "used up its cap",
    "over_budget": "over its budget",
    "context_too_small": "message too long for it",
    "not_vision_capable": "can't see images",
}


def _row(rank: int, row, top: float, cut: float | None) -> str:
    width = (row.score / top * 100) if top else 0
    if row.in_the_running:
        state, word = "ok", "would answer"
    elif row.available:
        state, word = "idle", "outscored"
    else:
        state, word = "bad", _REASON.get(row.reason, (row.reason or "unavailable").replace("_", " "))
    marker = (tag("span", "", cls="ladder-cut", style=f"left:{cut / top * 100:.1f}%",
                  title="20% below the best score: anything left of this line is not picked")
              if cut and top else "")
    return tag("div",
               tag("span", esc(rank), cls="ladder-rank")
               + tag("span", esc(f"{row.provider}/{row.model}"), cls="ladder-name")
               + tag("div", tag("span", "", cls=f"ladder-fill ladder-{state}",
                                style=f"width:{width:.1f}%") + marker, cls="ladder-track")
               + tag("span", esc(row.score), cls="ladder-score")
               + tag("span", esc(word), cls=f"status status-{state} ladder-word",
                     title=row.detail or ""),
               cls=f"ladder-row is-{state}",
               **{"data-row": f"{row.provider}/{row.model}", "data-value": f"{row.score}/{state}"})


def _bucket(b, add_model_form: str) -> str:
    scores = [r.score for r in b.models]
    top = max(scores) if scores else 0
    live = [r.score for r in b.models if r.available]
    cut = max(live) * 0.8 if live else None
    running = sum(1 for r in b.models if r.in_the_running)
    rows = "".join(_row(i, r, top, cut) for i, r in enumerate(b.models, 1)) or ui.empty(
        "No models in this bucket yet.")
    try_it = ui.button("Try it", icon_name="arrow-right", kind="primary",
                       **{"data-try": b.name, "title": "Send one real one-token request"})
    body = (tag("div", rows, cls="ladder")
            + tag("div", "", cls="try-result", id=f"try-{b.name}", **{"aria-live": "polite"})
            + tag("details", tag("summary", "Add a model to this bucket") + add_model_form,
                  cls="table-view"))
    return ui.box(b.name, body,
                  sub=f"{len(b.models)} models · {running} could answer now",
                  action=try_it,
                  **{"data-enter": "", "data-box": f"bucket-{b.name}"})


def body(router, banner: str, add_model_form, add_bucket_form: str) -> str:
    all_buckets = facts.buckets(router)
    head = tag("div", tag("div", tag("h1", "Buckets", cls="page-title")
                          + tag("p", "A request to a bucket goes to a random model among those "
                                     "within 20% of its best available score.",
                                cls="page-status"), cls="page-head-text"),
               cls="page-head")
    boxes = "".join(_bucket(b, add_model_form(b.name)) for b in all_buckets)
    if not boxes:
        boxes = ui.empty("No buckets yet. A bucket is a name your apps ask for, like fast or "
                         "smart, holding the models that can answer it.")
    add = ui.box("Add a bucket", add_bucket_form, **{"data-enter": ""})
    return head + banner + tag("div", boxes, cls="bucket-grid") + add
