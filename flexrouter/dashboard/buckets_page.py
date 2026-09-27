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

import json
from urllib.parse import quote

from flexrouter.dashboard import facts, ui
from flexrouter.dashboard.render import attrs, esc, tag

_REASON = {  # engine._skip_reason names, in plain words
    "busy": "busy",
    "struggling": "struggling",
    "needs_you": "needs you",
    "provider_rate_limit": "provider says slow down",
    "quota_exhausted": "used up its cap",
    "over_budget": "over its budget",
    "context_too_small": "message too long for it",
    "not_vision_capable": "can't see images",
}


def _speed_shown(row) -> str:
    if row.ttft_ms is not None:
        return f"{row.ttft_ms / 1000:.2f}s ({row.ttft_samples} sample{'s' if row.ttft_samples != 1 else ''})"
    if row.tokens_per_second is not None:
        return f"~{esc(row.tokens_per_second)} tok/s (guess)"
    return "not measured yet, will try"


def _row(rank: int, row, top: float, cut: float | None, strategy: str) -> str:
    value = row.fastest_rank if strategy == "fastest" else row.score
    shown = _speed_shown(row) if strategy == "fastest" else esc(value)
    width = (value / top * 100) if top and value is not None else 0
    if row.in_the_running:
        state, word = "ok", "would answer"
    elif row.available:
        state, word = "idle", "a better one goes first"
    else:
        state, word = "bad", _REASON.get(row.reason, (row.reason or "unavailable").replace("_", " "))
    label = "20% below the fastest: anything left of this line is not picked" if strategy == "fastest" \
        else "20% below the best score: anything left of this line is not picked"
    marker = (tag("span", "", cls="ladder-cut", style=f"left:{cut / top * 100:.1f}%", title=label)
              if cut and top else "")
    return tag("div",
               tag("span", esc(rank), cls="ladder-rank")
               + tag("span", esc(f"{row.provider}/{row.model}"), cls="ladder-name")
               + tag("div", tag("span", "", cls=f"ladder-fill ladder-{state}",
                                style=f"width:{width:.1f}%") + marker, cls="ladder-track")
               + tag("span", shown, cls="ladder-score")
               + tag("span", esc(word), cls=f"status status-{state} ladder-word",
                     title=row.detail or ""),
               cls=f"ladder-row is-{state}",
               **{"data-row": f"{row.provider}/{row.model}", "data-value": f"{value}/{state}"})


def _strategy_form(bucket: str, strategy: str) -> str:
    opts = "".join(
        tag("option", label, value=value, **({"selected": True} if value == strategy else {}))
        for value, label in (("smartest", "Smartest (by score)"), ("fastest", "Fastest (by first word)"))
    )
    select = f"<select{attrs({'name': 'strategy'})}>{opts}</select>"
    return tag(
        "form",
        select + ui.submit("Set", **{"data-working": "Saving", "data-done": "Saved"}),
        method="post", action=f"/buckets/{quote(bucket, safe=':')}/strategy", cls="inline-form",
    )


def _bucket(b, add_model_form: str) -> str:
    values = [r.fastest_rank if b.strategy == "fastest" else r.score for r in b.models]
    values = [v for v in values if v is not None]
    top = max(values) if values else 0
    live = [r.fastest_rank if b.strategy == "fastest" else r.score
           for r in b.models if r.available]
    live = [v for v in live if v is not None]
    cut = max(live) * 0.8 if live else None
    running = sum(1 for r in b.models if r.in_the_running)
    rows = "".join(_row(i, r, top, cut, b.strategy) for i, r in enumerate(b.models, 1)) or ui.empty(
        "No models in this bucket yet.")
    try_it = ui.button("Try it", icon_name="arrow-right", kind="primary",
                       **{"data-try": b.name, "title": "Send one real one-token request"})
    ranked_by = (tag("p", "ranked by: first word, your last 20 requests", cls="page-status")
                 if b.strategy == "fastest" else "")
    body = (_strategy_form(b.name, b.strategy)
            + ranked_by
            + tag("div", rows, cls="ladder")
            + tag("div", "", cls="try-result", id=f"try-{b.name}", **{"aria-live": "polite"})
            + tag("details", tag("summary", "Add a model to this bucket") + add_model_form,
                  cls="table-view"))
    return ui.box(b.name, body,
                  sub=f"{ui.plural(len(b.models), 'model')} · {running} could answer now",
                  action=try_it,
                  **{"data-enter": "", "data-box": f"bucket-{b.name}", "data-dropzone": b.name})


def _model_card(row) -> str:
    """One card to drag (or tap, or Enter) onto a bucket, carrying everything `add_model()` needs to file
    this already-configured model into another bucket - so dropping it
    never has to ask the owner to retype a score or a rate limit it
    already knows.
    """
    return tag(
        "div",
        ui.icon("grip")
        + tag("span", esc(f"{row.provider}/{row.model}"), cls="model-card-name")
        + tag("span", esc(row.score), cls="model-card-score"),
        cls="model-card", tabindex="0", role="button",
        **{
            "aria-pressed": "false",
            "data-provider": row.provider, "data-model": row.model,
            "data-score": row.score, "data-rpm": row.rpm, "data-tpm": row.tpm,
            "data-context-window": row.context_window or "",
            "data-tokens-per-second": (
                row.tokens_per_second if row.tokens_per_second is not None else ""),
            "data-vision": "1" if row.vision_configured else "",
            "data-quotas": json.dumps(row.quotas or {}),
        },
    )


def _model_palette(router) -> str:
    rows = facts.models(router)
    if not rows:
        return ""
    cards = "".join(_model_card(r) for r in sorted(rows, key=lambda r: (r.provider, r.model)))
    return ui.box(
        "All models", tag("div", cards, cls="model-card-grid"),
        sub="drag a model onto a bucket to add it there",
        **{"data-enter": ""},
    )


def body(router, banner: str, add_model_form, add_bucket_form: str) -> str:
    all_buckets = facts.buckets(router)
    head = tag("div", tag("div", tag("h1", "Buckets", cls="page-title")
                          + tag("p", "A request to a bucket goes to a random model among those "
                                     "within 20% of its best available score, or its fastest "
                                     "measured time to first word, depending on the bucket's "
                                     "strategy.",
                                cls="page-status"), cls="page-head-text"),
               cls="page-head")
    palette = _model_palette(router)
    boxes = "".join(_bucket(b, add_model_form(b.name)) for b in all_buckets)
    if not boxes:
        boxes = ui.empty("No buckets yet. A bucket is a name your apps ask for, like fast or "
                         "smart, holding the models that can answer it.")
    add = ui.box("Add a bucket", add_bucket_form, **{"data-enter": ""})
    return head + banner + palette + tag("div", boxes, cls="bucket-grid") + add
