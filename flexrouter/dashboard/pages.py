"""The dashboard's pages.

Server-rendered HTML, no build step, no JavaScript. Every area is a real URL
and every control is a real form, so there is no client state that can
disagree with the service - see the Stage 8 roadmap, ruling R2. All nine
areas are built as of sub-plan 7.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from flexrouter import keys as keystore
from flexrouter import overrides as ov
from flexrouter import score_facts
from flexrouter import service_keys
from flexrouter.dashboard import facts, keytest, pending_actions, ranking, settings_write
from flexrouter.dashboard.render import attrs, esc, page, tag

pages = APIRouter()

CSS_PATH = Path(__file__).parent / "wire.css"


def _input(**kw) -> str:
    """A void `<input>` element. Not `tag("input", ...)`: `tag`'s own first
    parameter is called `name`, so an HTML `name=` attribute - required on
    nearly every form control here - can never be passed to it as a kwarg
    without colliding with that parameter."""
    return f"<input{attrs(kw)}>"


def _textarea(body: str, **kw) -> str:
    """A `<textarea>` element. Not `tag("textarea", body, **kw)`: same
    collision as `_input` above - `tag`'s first parameter is `name`."""
    return f"<textarea{attrs(kw)}>{body}</textarea>"


def _live_router():
    """The shared router, refreshed first if overrides.json/config.yaml/
    keys.json have changed since it last loaded.

    Every write this app makes lands in overrides.json, not in the
    router's own in-memory `FlexConfig` - normally that only gets
    refreshed on the next real chat request (`LocalRouter._maybe_hot_reload`,
    flexrouter/_router.py), which a page in this same process would never
    trigger on its own. Without this, a dashboard write would round-trip
    to disk and back but the very next page load would still show the old
    value.
    """
    from flexrouter.app import get_router
    router = get_router()
    router._maybe_hot_reload()
    return router


def _redirect_with_message(path: str, ok: bool, message: str) -> RedirectResponse:
    qs = f"ok={'1' if ok else '0'}&message={quote(message)}"
    return RedirectResponse(url=f"{path}?{qs}", status_code=303)


def _tile(label: str, value: object, note: str = "") -> str:
    return tag(
        "div",
        tag("span", esc(label)) + tag("b", esc(value)) + tag("small", esc(note)),
        cls="tile",
    )


def _overview_body(router) -> str:
    summaries = facts.provider_summaries(router)
    data = facts.overview(router, summaries=summaries)
    p, k, m, learned = data["providers"], data["keys"], data["models"], data["learned"]

    tiles = tag("div", "".join([
        _tile("Providers", p["total"],
              f"{p['ok']} fine, {p['warn']} unsettled, {p['bad']} down"),
        _tile("Keys held", k["total"],
              f"{k['live']} in use, {k['cooling']} resting, {k['parked']} parked"),
        _tile("Models", f"{m['available']} of {m['total']}", "reachable right now"),
        _tile("Failures understood", learned["error_kinds"],
              f"{learned['awaiting_you']} waiting for you"),
        _tile("Models it has learned about", learned["models_with_facts"], ""),
    ]), cls="tiles")

    rows = [tag("tr", "".join([
        tag("th", "Provider"), tag("th", "State"), tag("th", "Address"),
        tag("th", "Keys"), tag("th", "Models"), tag("th", "Why"),
    ]))]
    for s in summaries:
        rows.append(tag("tr", "".join([
            tag("td", esc(s.name)),
            tag("td", esc(s.state), cls=f"state-{s.state}"),
            tag("td", esc(s.base_url)),
            tag("td", esc(f"{s.keys_live} of {s.key_count} in use")),
            tag("td", esc(s.models_total)),
            tag("td", esc(s.quarantine_reason or "")),
        ])))

    buckets = ", ".join(data["service"]["buckets"]) or "none set up"

    needs_you = data["needs_you"]
    if needs_you:
        plural = "" if needs_you == 1 else "s"
        message = f"{needs_you} thing{plural} need{'s' if needs_you == 1 else ''} you - see What's broken."
        tail = tag("p", tag("a", esc(message), href="/broken"), cls="note")
    else:
        tail = tag("p", "Nothing needs you right now.", cls="note")

    return (
        tag("h1", "Overview")
        + tag("p", "Everything at a glance. Apps point here instead of at "
                   "OpenAI, and this is what the router can reach for them.",
              cls="lede")
        + tiles
        + tag("h3", "Every provider")
        + tag("table", "".join(rows))
        + tag("h3", "Buckets your apps can ask for")
        + tag("p", esc(buckets))
        + tail
    )


_KIND_LABELS = {
    "provider_down": "provider is down",
    "model_set_aside": "model set aside",
    "key_benched": "key benched",
    "key_cooling": "key resting",
    "unclear_error": "an error it isn't sure about",
}


def _broken_table(items) -> str:
    if not items:
        return tag("p", "Nothing here.", cls="note")
    rows = [tag("tr", "".join([
        tag("th", "What"), tag("th", "Where"), tag("th", "Why"),
    ]))]
    for item in items:
        where = item.provider
        if item.detail:
            where = f"{where} - {item.detail}" if where else item.detail
        rows.append(tag("tr", "".join([
            tag("td", esc(_KIND_LABELS.get(item.kind, item.kind))),
            tag("td", esc(where)),
            tag("td", esc(item.reason)),
        ])))
    return tag("table", "".join(rows))


def _broken_body(router) -> str:
    data = facts.broken(router)
    needs_you, handling_itself = data["needs_you"], data["handling_itself"]

    return (
        tag("h1", "What's broken")
        + tag("p", "Two piles: what only you can fix, and what the service "
                   "is already handling on its own without you doing "
                   "anything.", cls="lede")
        + tag("h3", f"Needs you ({len(needs_you)})")
        + _broken_table(needs_you)
        + tag("h3", f"Handling itself ({len(handling_itself)})")
        + _broken_table(handling_itself)
    )


def _add_provider_form() -> str:
    return tag(
        "form",
        _input(type="text", name="name", placeholder="name") + " "
        + _input(type="text", name="base_url", placeholder="base URL") + " "
        + _input(type="text", name="header_parser",
              placeholder="header parser (optional)") + " "
        + _input(type="text", name="key_strategy",
              placeholder="key strategy (optional)") + " "
        + _input(type="text", name="key_secret",
              placeholder="key (optional)") + " "
        + _input(type="text", name="key_label",
              placeholder="key label (optional)") + " "
        + tag("button", "Add provider", type="submit"),
        method="post", action="/providers",
    )


def _providers_body(router, banner: str = "") -> str:
    summaries = facts.provider_summaries(router)

    rows = [tag("tr", "".join([
        tag("th", "Provider"), tag("th", "State"), tag("th", "Address"),
        tag("th", "Keys"), tag("th", "Models"), tag("th", "Why"),
    ]))]
    for s in summaries:
        rows.append(tag("tr", "".join([
            tag("td", tag("a", esc(s.name), href=f"/providers/{quote(s.name)}")),
            tag("td", esc(s.state), cls=f"state-{s.state}"),
            tag("td", esc(s.base_url)),
            tag("td", esc(f"{s.keys_live} live, {s.keys_cooling} resting, {s.keys_parked} parked")),
            tag("td", esc(s.models_total)),
            tag("td", esc(s.quarantine_reason or "")),
        ])))

    return (
        tag("h1", "Providers & keys")
        + banner
        + tag("p", "Every provider you've configured, and every key it "
                   "holds. Click a provider for its keys.", cls="lede")
        + tag("table", "".join(rows))
        + tag("h3", "Add a provider")
        + tag("p", "A key can be added right here, or later from the "
                   "provider's own page - models are always added from "
                   "there, one at a time, once the provider exists.",
              cls="note")
        + _add_provider_form()
    )


def _key_rows(detail) -> str:
    if not detail.keys:
        return tag("p", "No keys configured for this provider.", cls="note")

    header = tag("tr", "".join([
        tag("th", h) for h in [
            "Key", "Value", "Status", "Why", "Weight", "Allowed models", "Enabled",
            "Requests today", "Tokens today", "Failures (24h)",
            "Consecutive failures", "Active now", "Typical latency",
            "Last used", "Source", "Test",
        ]
    ]))
    rows = [header]
    for k in detail.keys:
        until = f", back in {int(k.until - time.time())}s" if k.until else ""
        status = f"{k.status}{until}"
        last_used = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(k.last_used_at))
            if k.last_used_at else "never"
        )
        latency = f"{k.ema_latency_ms:.0f} ms" if k.ema_latency_ms else ""
        test_form = tag(
            "form",
            tag("button", "Test", type="submit"),
            method="post",
            action=f"/providers/{quote(detail.name, safe=':')}/keys/{quote(k.id, safe=':')}/test",
        )
        rows.append(tag("tr", "".join([
            tag("td", esc(k.label or k.id)),
            tag("td", esc(k.masked)),
            tag("td", esc(status), cls=f"state-{'ok' if k.status == 'live' else 'warn' if k.status == 'cooling' else 'bad'}"),
            tag("td", esc(k.reason)),
            tag("td", esc(k.weight)),
            tag("td", esc(", ".join(k.allow_models))),
            tag("td", esc("yes" if k.enabled else "no")),
            tag("td", esc(k.requests_today)),
            tag("td", esc(k.tokens_today)),
            tag("td", esc(k.failures_24h)),
            tag("td", esc(k.consecutive_failures)),
            tag("td", esc(k.active_requests)),
            tag("td", esc(latency)),
            tag("td", esc(last_used)),
            tag("td", esc(k.source)),
            tag("td", test_form),
        ])))
    return tag("table", "".join(rows))


def _provider_edit_form(detail, editable: dict) -> str:
    fields = editable["fields"]
    inputs = "".join(
        _input(type="text", name=name,
            value=esc(fields[name]["value"]), title=name) + " "
        for name in ("base_url", "header_parser", "key_strategy")
    )
    edit = tag(
        "form", inputs + tag("button", "Save", type="submit"),
        method="post", action=f"/providers/{quote(detail.name, safe=':')}/edit",
    )
    clear = (
        tag("form", tag("button", "Put it all back", type="submit"),
            method="post", action=f"/providers/{quote(detail.name, safe=':')}/clear")
        if editable["overridden_at_all"] else ""
    )
    return tag("h3", "Change it") + edit + clear


def _provider_add_model_form(provider: str, bucket_names: list) -> str:
    bucket_options = "".join(
        tag("option", esc(b), value=esc(b)) for b in bucket_names
    )
    return tag(
        "form",
        f"<select{attrs({'name': 'bucket'})}>{bucket_options}</select> "
        + _input(type="text", name="model", placeholder="model") + " "
        + _input(type="number", name="score", placeholder="score") + " "
        + _input(type="number", name="rpm", placeholder="rpm") + " "
        + _input(type="number", name="tpm", placeholder="tpm") + " "
        + _input(type="number", name="context_window",
              placeholder="context window (optional)") + " "
        + tag("label", _input(type="checkbox", name="vision") + "vision") + " "
        + tag("button", "Add model", type="submit"),
        method="post", action=f"/providers/{quote(provider, safe=':')}/models",
    )


def _provider_detail_body(detail, editable: dict, bucket_names: list,
                          banner: str = "") -> str:
    models = (
        tag("p", esc(f"Alive: {', '.join(detail.models_alive) or 'none'}"))
        + tag("p", esc(f"Gone: {', '.join(detail.models_gone) or 'none'}"))
    )
    quarantine_note = (
        tag("p", esc(detail.quarantine_reason), cls="state-bad")
        if detail.quarantined else ""
    )
    return (
        tag("h1", esc(detail.name))
        + banner
        + tag("p", esc(detail.base_url), cls="lede")
        + quarantine_note
        + tag("h3", "Models")
        + models
        + tag("h3", "Add a model")
        + tag("p", "One model per submission, into the bucket you choose. "
                   "Submit again for the next one.", cls="note")
        + _provider_add_model_form(detail.name, bucket_names)
        + tag("h3", "Keys")
        + tag("p", "Test sends one real chat request through this key - not "
                   "just a model list lookup, which can look fine while "
                   "every real request still fails.", cls="note")
        + _key_rows(detail)
        + _provider_edit_form(detail, editable)
    )


def _cap_cell(fact) -> str:
    if fact is None:
        return tag("span", "unknown", cls="cap-unknown")
    return tag("span", esc(f"{fact.status} ({fact.source})"), cls=f"cap-{fact.source}")


def _pending_action_form(action: str, url: str, extra: str = "") -> str:
    label = "Accept" if action == "accept" else "Reject"
    return tag(
        "form",
        _input(type="hidden", name="action", value=action)
        + extra
        + tag("button", label, type="submit"),
        method="post", action=url,
    )


def _pending_body(pending: dict, bucket_names: list) -> str:
    bucket_options = "".join(
        tag("option", esc(b), value=esc(b)) for b in bucket_names
    )
    sections = []
    for provider, bucket in sorted(pending.items()):
        items = []
        for m in bucket.get("appeared") or []:
            model = m.get("model")
            url = f"/models/pending/{quote(provider, safe=':')}/appeared/{quote(model, safe=':')}"
            picker = f"<select{attrs({'name': 'bucket'})}>{bucket_options}</select>"
            items.append(tag("li", esc(f"{provider}/{model} appeared")
                             + " " + _pending_action_form("accept", url, picker)
                             + " " + _pending_action_form("reject", url)))
        for model_id in bucket.get("vanished") or []:
            url = f"/models/pending/{quote(provider, safe=':')}/vanished/{quote(model_id, safe=':')}"
            items.append(tag("li", esc(f"{provider}/{model_id} vanished")
                             + " " + _pending_action_form("accept", url)
                             + " " + _pending_action_form("reject", url)))
        for c in bucket.get("changed") or []:
            model = c.get("model")
            field = c.get("field")
            url = (f"/models/pending/{quote(provider, safe=':')}/changed/"
                   f"{quote(model, safe=':')}")
            hidden_field = _input(type="hidden", name="field", value=esc(field))
            items.append(tag("li", esc(
                f"{provider}/{model}: {field} {c.get('old')} -> {c.get('new')}"
            ) + " " + _pending_action_form("accept", url, hidden_field)
              + " " + _pending_action_form("reject", url, hidden_field)))
        if items:
            sections.append(tag("h4", esc(provider)) + tag("ul", "".join(items)))
    if not sections:
        return tag("p", "Nothing pending.", cls="note")
    return "".join(sections)


def _model_ident_path(provider: str, model: str) -> str:
    return f"{quote(provider, safe=':')}/{quote(model, safe=':')}"


def _model_edit_form(r) -> str:
    ident = _model_ident_path(r.provider, r.model)
    vision_checked = True if r.vision_configured else False
    edit = tag(
        "form",
        _input(type="hidden", name="action", value="edit")
        + _input(type="number", name="score", value=esc(r.score),
              title="score")
        + _input(type="number", name="rpm", value=esc(r.rpm), title="rpm")
        + _input(type="number", name="tpm", value=esc(r.tpm), title="tpm")
        + _input(type="number", name="context_window",
              value=esc(r.context_window), title="context window")
        + tag("label", _input(type="checkbox", name="vision",
                          **{"checked": True} if vision_checked else {})
              + "vision")
        + tag("button", "Save", type="submit"),
        method="post", action=f"/models/{ident}",
    )
    disable = tag(
        "form",
        _input(type="hidden", name="action", value="disable")
        + tag("button", "Disable", type="submit"),
        method="post", action=f"/models/{ident}",
    )
    return edit + disable


def _models_body(router, banner: str = "") -> str:
    rows_data = facts.models(router)

    header = tag("tr", "".join(tag("th", h) for h in [
        "Provider", "Model", "Buckets", "Score", "Context",
        "Vision", "Tools", "Reasoning", "State", "Why", "Change it",
    ]))
    rows = [header]
    for r in rows_data:
        rows.append(tag("tr", "".join([
            tag("td", esc(r.provider)),
            tag("td", esc(r.model)),
            tag("td", esc(", ".join(r.buckets))),
            tag("td", esc(r.score)),
            tag("td", esc(r.learned_context or r.context_window)),
            tag("td", _cap_cell(r.vision)),
            tag("td", _cap_cell(r.tools)),
            tag("td", _cap_cell(r.reasoning)),
            tag("td", esc(r.state), cls=f"state-{'ok' if r.state == 'available' else 'bad'}"),
            tag("td", esc(r.why)),
            tag("td", _model_edit_form(r)),
        ])))

    pending = facts.pending_catalogue(router)
    bucket_names = list(router._cfg.tiers)

    disabled = facts.disabled_models()
    disabled_rows = [tag("tr", "".join([
        tag("td", esc(ident)),
        tag("td", tag(
            "form",
            _input(type="hidden", name="action", value="clear")
            + tag("button", "Put it back", type="submit"),
            method="post",
            action=f"/models/{_model_ident_path(*ident.split('/', 1))}",
        )),
    ])) for ident in disabled]
    disabled_section = (
        tag("table", "".join(disabled_rows)) if disabled_rows
        else tag("p", "No disabled models.", cls="note")
    )

    return (
        tag("h1", "Models")
        + banner
        + tag("p", "Every model configured in a bucket, and what the "
                   "service has learned about it from real traffic. "
                   "A capability's source is in parentheses: published "
                   "(the provider says so), observed (seen working), "
                   "guessed (nobody's said either way yet), or manual "
                   "(you set it and nothing overrides it).", cls="lede")
        + tag("table", "".join(rows))
        + tag("p", tag("a", "Rank models with an AI", href="/models/rank"))
        + tag("h3", "Disabled models")
        + disabled_section
        + tag("h3", "Pending catalogue changes")
        + tag("p", "What the last catalogue check found. Accepting an "
                   "appeared model adds it to the bucket you choose; "
                   "accepting a vanished model disables it; accepting a "
                   "changed field applies the provider's new value.",
              cls="note")
        + _pending_body(pending, bucket_names)
    )


def _rank_body(prompt: str, notes: str) -> str:
    notes_form = tag(
        "form",
        _textarea(esc(notes), name="notes", rows="6",
                 placeholder="benchmark material or notes (optional)")
        + " " + tag("button", "Build prompt", type="submit"),
        method="get", action="/models/rank",
    )
    paste_form = tag(
        "form",
        _textarea("", name="answer", rows="10",
                  placeholder="paste the AI's reply here, one line per "
                              "model: provider | model | score")
        + " " + tag("button", "Show proposed changes", type="submit"),
        method="post", action="/models/rank/proposal",
    )
    return (
        tag("h1", "Rank models with an AI")
        + tag("p", "Builds a ready-made prompt from the current model "
                   "list plus whatever benchmark material you supply. "
                   "Send it yourself, or copy it into whatever chat "
                   "you're already in and paste the answer back below. "
                   "Nothing is applied until you say so on the next page, "
                   "and a score you've set by hand here is never "
                   "proposed a new value.", cls="lede")
        + tag("h3", "1. Add benchmark material (optional)")
        + notes_form
        + tag("h3", "2. The prompt - copy this")
        + _textarea(esc(prompt), rows="16", readonly=True)
        + tag("h3", "3. Paste the answer back")
        + paste_form
    )


def _rank_proposal_body(changes: list, skipped_manual: list) -> str:
    if not changes:
        body = tag("p", "Nothing to propose - either nothing changed, or "
                        "every changed score is pinned by hand.", cls="note")
    else:
        rows_html = [tag("tr", "".join(tag("th", h) for h in [
            "Apply", "Provider", "Model", "Current", "Proposed",
        ]))]
        for row, proposed in changes:
            ident = f"{row.provider}/{row.model}"
            cell = (
                _input(type="checkbox", name=f"apply:{ident}", checked=True)
                + _input(type="hidden", name=f"score:{ident}", value=str(proposed))
            )
            rows_html.append(tag("tr", "".join([
                tag("td", cell),
                tag("td", esc(row.provider)),
                tag("td", esc(row.model)),
                tag("td", esc(row.score)),
                tag("td", esc(proposed)),
            ])))
        body = tag(
            "form", tag("table", "".join(rows_html))
            + tag("button", "Apply checked scores", type="submit"),
            method="post", action="/models/rank/apply",
        )

    skipped_note = (
        tag("p", esc(f"{len(skipped_manual)} model(s) skipped - their score "
                     f"is pinned by hand: {', '.join(skipped_manual)}"),
            cls="note")
        if skipped_manual else ""
    )

    return (
        tag("h1", "Proposed scores")
        + tag("p", "Nothing here is applied until you press the button "
                   "below. Uncheck any row you don't want changed.",
              cls="lede")
        + skipped_note
        + body
    )


def _bucket_verdict(row) -> tuple[str, str]:
    if row.in_the_running:
        return "ok", "would answer"
    if row.available:
        return "warn", "available, but outscored right now"
    return "bad", row.detail or (row.reason or "unavailable")


def _add_model_form(bucket: str) -> str:
    return tag(
        "form",
        _input(type="text", name="provider", placeholder="provider") + " "
        + _input(type="text", name="model", placeholder="model") + " "
        + _input(type="number", name="score", placeholder="score") + " "
        + _input(type="number", name="rpm", placeholder="rpm") + " "
        + _input(type="number", name="tpm", placeholder="tpm") + " "
        + _input(type="number", name="context_window",
              placeholder="context window (optional)") + " "
        + tag("label", _input(type="checkbox", name="vision") + "vision") + " "
        + tag("button", "Add model", type="submit"),
        method="post", action=f"/buckets/{quote(bucket, safe=':')}/models",
    )


def _add_bucket_form() -> str:
    return tag(
        "form",
        _input(type="text", name="name", placeholder="bucket name") + " "
        + tag("button", "Add bucket", type="submit"),
        method="post", action="/buckets",
    )


def _buckets_body(router, banner: str = "") -> str:
    all_buckets = facts.buckets(router)

    sections = []
    for b in all_buckets:
        header = tag("tr", "".join(tag("th", h) for h in [
            "Provider", "Model", "Score", "Verdict",
        ]))
        rows = [header]
        for row in b.models:
            state, verdict = _bucket_verdict(row)
            rows.append(tag("tr", "".join([
                tag("td", esc(row.provider)),
                tag("td", esc(row.model)),
                tag("td", esc(row.score)),
                tag("td", esc(verdict), cls=f"state-{state}"),
            ])))
        sections.append(
            tag("h3", esc(b.name))
            + (tag("table", "".join(rows)) if b.models else tag("p", "Empty.", cls="note"))
            + _add_model_form(b.name)
        )

    body = "".join(sections) if sections else tag("p", "No buckets are configured yet.", cls="note")

    return (
        tag("h1", "Buckets")
        + banner
        + tag("p", "What each bucket can pick from right now, best score "
                   "first. \"Would answer\" means a real request could "
                   "land on this model - the engine picks randomly among "
                   "everything within 20% of the top score, so more than "
                   "one model can carry that verdict.", cls="lede")
        + body
        + tag("h3", "Add a bucket")
        + _add_bucket_form()
    )


def _requests_body(router) -> str:
    rows_data = facts.recent_requests(router)
    if not rows_data:
        return (
            tag("h1", "Requests")
            + tag("p", "Nothing has come through yet.", cls="note")
        )

    header = tag("tr", "".join(tag("th", h) for h in [
        "When", "Bucket", "Result", "Answered by", "Tokens in/out",
        "Time", "Skipped",
    ]))
    rows = [header]
    for r in rows_data:
        answered = (
            f"{r.answered_by['provider']}/{r.answered_by['model']}"
            if r.answered_by else ""
        )
        rows.append(tag("tr", "".join([
            tag("td", esc(r.at)),
            tag("td", esc(r.bucket)),
            tag("td", esc("ok" if r.ok else "failed"), cls=f"state-{'ok' if r.ok else 'bad'}"),
            tag("td", esc(answered)),
            tag("td", esc(f"{r.tokens_in}/{r.tokens_out}")),
            tag("td", esc(f"{r.ms_total} ms")),
            tag("td", esc(r.skipped_count)),
        ])))

    return (
        tag("h1", "Requests")
        + tag("p", "The last 50 requests the service has handled, newest "
                   "first.", cls="lede")
        + tag("table", "".join(rows))
    )


def _brain_body(router) -> str:
    entries = facts.error_brain_entries(router)
    if not entries:
        return tag("h1", "Error brain") + tag("p", "Nothing learned yet.", cls="note")

    header = tag("tr", "".join(tag("th", h) for h in [
        "Verdict", "Source", "Confidence", "Seen", "First seen",
        "Last seen", "Sample", "Needs review",
    ]))
    rows = [header]
    for e in entries:
        rows.append(tag("tr", "".join([
            tag("td", esc(e.verdict)),
            tag("td", esc(e.source)),
            tag("td", esc(f"{e.confidence:.0%}")),
            tag("td", esc(e.seen)),
            tag("td", esc(e.first_at)),
            tag("td", esc(e.last_at)),
            tag("td", esc(e.sample)),
            tag("td", esc("yes" if e.flagged_for_review else ""),
                cls="state-warn" if e.flagged_for_review else ""),
        ])))

    return (
        tag("h1", "Error brain")
        + tag("p", "Every kind of provider error the service has learned "
                   "to recognise. Entries marked \"needs review\" were "
                   "guessed below its confidence threshold - correcting "
                   "one by hand isn't wired up yet.", cls="lede")
        + tag("table", "".join(rows))
    )


def _allowance_body(router) -> str:
    rows_data = facts.allowance(router)
    if not rows_data:
        return tag("h1", "Allowance") + tag("p", "No models configured.", cls="note")

    header = tag("tr", "".join(tag("th", h) for h in [
        "Provider", "Model", "Your caps", "Provider headroom",
    ]))
    rows = [header]
    for r in rows_data:
        caps = ", ".join(f"{k} {v}" for k, v in r.quotas.items()) or "none set"
        if not r.quotas:
            cap_cell = tag("td", esc(caps))
        else:
            text_ = caps if r.quota_ok else (
                f"{caps} - used up, back in {int(r.quota_wait_seconds)}s")
            cap_cell = tag("td", esc(text_), cls=f"state-{'ok' if r.quota_ok else 'bad'}")

        if r.provider_rate_exhausted:
            headroom = (
                f"exhausted, back in {int(r.provider_available_at - time.time())}s"
                if r.provider_available_at else "exhausted"
            )
            headroom_cell = tag("td", esc(headroom), cls="state-bad")
        else:
            headroom_cell = tag("td", "ok", cls="state-ok")

        rows.append(tag("tr", "".join([
            tag("td", esc(r.provider)),
            tag("td", esc(r.model)),
            cap_cell,
            headroom_cell,
        ])))

    return (
        tag("h1", "Allowance")
        + tag("p", "Free-tier headroom, not money - nothing here tracks "
                   "cost, because nothing in the service records it.",
              cls="lede")
        + tag("table", "".join(rows))
    )


_INT_SETTINGS = frozenset({
    "port", "dashboard_port", "window_seconds", "penalty_base_seconds",
    "penalty_max_seconds", "session_ttl_minutes", "sample_interval_seconds",
    "health_history_days", "key_concurrency_cap", "retries",
})
_FLOAT_SETTINGS = frozenset({"backoff_seconds"})
_JSON_SETTINGS = frozenset({"provider_budget", "hooks"})


def _cast_setting(field: str, raw_value: str):
    if field in _INT_SETTINGS:
        return int(raw_value)
    if field in _FLOAT_SETTINGS:
        return float(raw_value)
    if field in _JSON_SETTINGS:
        if not raw_value.strip():
            return {} if field == "provider_budget" else []
        return json.loads(raw_value)
    return raw_value


def _display_setting(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return "" if value is None else str(value)


def _settings_field_row(f) -> str:
    edit = tag(
        "form",
        _input(type="text", name="value", value=esc(_display_setting(f.value)))
        + tag("button", "Save", type="submit"),
        method="post", action=f"/settings/{quote(f.name)}",
    )
    clear = (
        tag("form", tag("button", "Put it back", type="submit"),
            method="post", action=f"/settings/{quote(f.name)}/clear")
        if f.overridden else ""
    )
    return tag("tr", "".join([
        tag("td", esc(f.name)),
        tag("td", edit),
        tag("td", esc("changed here" if f.overridden else "typed by you")),
        tag("td", clear),
    ]))


def _service_key_row(name: str, label: str, env_var: str) -> str:
    current = service_keys.current_record(name)
    if current:
        value = tag("td", esc(keystore.mask(current.secret)), cls="state-ok")
    elif os.environ.get(env_var):
        value = tag("td", esc(f"set via ${env_var} (environment)"))
    else:
        value = tag("td", "not set", cls="state-bad")

    save_form = tag(
        "form",
        _input(type="text", name="secret", placeholder="paste key") + " "
        + tag("button", "Save", type="submit"),
        method="post", action=f"/settings/keys/{quote(name, safe=':')}",
    )
    remove_form = (
        tag("form", tag("button", "Remove", type="submit"),
            method="post", action=f"/settings/keys/{quote(name, safe=':')}/remove")
        if current else ""
    )
    return tag("tr", "".join([
        tag("td", esc(label)),
        value,
        tag("td", save_form),
        tag("td", remove_form),
    ]))


def _service_keys_section() -> str:
    header = tag("tr", "".join(tag("th", h) for h in [
        "Service", "Current key", "Set it", "",
    ]))
    rows = [header] + [
        _service_key_row(name, label, env_var)
        for name, (label, env_var) in service_keys.SERVICES.items()
    ]
    return (
        tag("h3", "Service keys")
        + tag("p", "Not a chat provider - these are keys the service uses "
                   "for its own calls: scoring a newly discovered model, "
                   "or (once it's built) classifying an error. Typed here "
                   "first, its environment variable second - same order "
                   "as every other key in this app. Stored the same way a "
                   "provider's key is: masked everywhere, never shown in "
                   "full again.", cls="note")
        + tag("table", "".join(rows))
    )


def _settings_body(router, banner: str = "") -> str:
    header = tag("tr", "".join(tag("th", h) for h in [
        "Setting", "Change it", "Where it's from", "",
    ]))
    rows = [header] + [_settings_field_row(f) for f in facts.settings_fields(router)]

    return (
        tag("h1", "Settings")
        + banner
        + tag("p", "Everything here writes to a small file layered on top "
                   "of your own settings file, never to the settings file "
                   "itself - \"put it back\" removes just that one change. "
                   "Editing a provider's address or a model's numbers, "
                   "adding a provider, a bucket, or a model, live on "
                   "their own pages.", cls="lede")
        + tag("table", "".join(rows))
        + _service_keys_section()
    )


@pages.get("/wire.css", include_in_schema=False)
def stylesheet() -> Response:
    return Response(CSS_PATH.read_text(encoding="utf-8"),
                    media_type="text/css; charset=utf-8")


@pages.get("/", response_class=HTMLResponse, include_in_schema=False)
def overview_page() -> HTMLResponse:
    return HTMLResponse(
        page("Overview", "overview", _overview_body(_live_router()))
    )


@pages.get("/broken", response_class=HTMLResponse, include_in_schema=False)
def broken_page() -> HTMLResponse:
    return HTMLResponse(page("What's broken", "broken", _broken_body(_live_router())))


def _message_banner(ok: str, message: str) -> str:
    if not message:
        return ""
    return tag("p", esc(message), cls=f"state-{'ok' if ok == '1' else 'bad'}")


@pages.get("/models", response_class=HTMLResponse, include_in_schema=False)
def models_page(ok: str = "", message: str = "") -> HTMLResponse:
    banner = _message_banner(ok, message)
    return HTMLResponse(page("Models", "models", _models_body(_live_router(), banner)))


@pages.post("/models/pending/{provider}/{kind}/{model:path}", include_in_schema=False)
async def model_pending_action(provider: str, kind: str, model: str,
                               request: Request) -> RedirectResponse:
    form = await request.form()
    action = form.get("action", "")
    state_dir = _live_router()._cfg.state_dir
    try:
        if kind == "appeared":
            if action == "accept":
                bucket = form.get("bucket") or ""
                if not bucket:
                    raise ValueError("choose a bucket to accept into")
                pending_actions.accept_appeared(state_dir, provider, model, bucket)
            elif action == "reject":
                pending_actions.reject_appeared(state_dir, provider, model)
            else:
                raise ValueError(f"unknown action {action!r}")
        elif kind == "vanished":
            if action == "accept":
                pending_actions.accept_vanished(state_dir, provider, model)
            elif action == "reject":
                pending_actions.reject_vanished(state_dir, provider, model)
            else:
                raise ValueError(f"unknown action {action!r}")
        elif kind == "changed":
            field = form.get("field") or ""
            if action == "accept":
                pending_actions.accept_changed(state_dir, provider, model, field)
            elif action == "reject":
                pending_actions.reject_changed(state_dir, provider, model, field)
            else:
                raise ValueError(f"unknown action {action!r}")
        else:
            raise ValueError(f"unknown pending kind {kind!r}")
    except (ValueError, pending_actions.PendingActionError) as e:
        return _redirect_with_message("/models", ok=False, message=str(e))
    return _redirect_with_message("/models", ok=True, message=f"{provider}/{model} {action}ed")


@pages.get("/models/rank", response_class=HTMLResponse, include_in_schema=False)
def models_rank_page(notes: str = "") -> HTMLResponse:
    rows = facts.models(_live_router())
    prompt = ranking.build_prompt(rows, notes)
    return HTMLResponse(page("Rank models", "models", _rank_body(prompt, notes)))


@pages.post("/models/rank/proposal", response_class=HTMLResponse, include_in_schema=False)
async def models_rank_proposal(request: Request) -> HTMLResponse:
    form = await request.form()
    answer = form.get("answer") or ""
    router = _live_router()
    rows = facts.models(router)
    by_ident = {f"{r.provider}/{r.model}": r for r in rows}
    parsed = ranking.parse_answer(answer, set(by_ident))

    state_dir = router._cfg.state_dir
    skipped_manual = []
    changes = []
    for ident, proposed_score in parsed.items():
        provider, _, model = ident.partition("/")
        if score_facts.is_manual(state_dir, provider, model):
            skipped_manual.append(ident)
            continue
        row = by_ident[ident]
        if proposed_score == row.score:
            continue
        changes.append((row, proposed_score))

    return HTMLResponse(
        page("Rank models", "models", _rank_proposal_body(changes, skipped_manual)))


@pages.post("/models/rank/apply", include_in_schema=False)
async def models_rank_apply(request: Request) -> RedirectResponse:
    form = await request.form()
    router = _live_router()
    state_dir = router._cfg.state_dir
    applied = []
    for key in form.keys():
        if not key.startswith("apply:"):
            continue
        ident = key[len("apply:"):]
        score_raw = form.get(f"score:{ident}")
        if score_raw is None:
            continue
        provider, _, model = ident.partition("/")
        try:
            settings_write.set_model_fields(provider, model, {"score": int(score_raw)})
        except ValueError:
            continue
        score_facts.record(state_dir, provider, model, "ai-ranked")
        applied.append(ident)

    message = f"{len(applied)} score(s) applied" if applied else "nothing was checked"
    return _redirect_with_message("/models", ok=bool(applied), message=message)


@pages.post("/models/{ident:path}", include_in_schema=False)
async def model_write(ident: str, request: Request) -> RedirectResponse:
    provider, _, model = ident.partition("/")
    form = await request.form()
    action = form.get("action", "")
    try:
        if action == "disable":
            settings_write.set_model_fields(provider, model, {"enabled": False})
        elif action == "clear":
            settings_write.clear_model(provider, model)
        elif action == "edit":
            fields: dict = {}
            for name in ("score", "rpm", "tpm", "context_window"):
                raw_value = form.get(name)
                if raw_value not in (None, ""):
                    fields[name] = int(raw_value)
            fields["vision"] = "vision" in form
            settings_write.set_model_fields(provider, model, fields)
            if "score" in fields:
                score_facts.record(_live_router()._cfg.state_dir, provider, model, "manual")
        else:
            raise ValueError(f"unknown action {action!r}")
    except ValueError as e:
        return _redirect_with_message("/models", ok=False, message=str(e))
    return _redirect_with_message("/models", ok=True, message=f"{ident} updated")


@pages.get("/requests", response_class=HTMLResponse, include_in_schema=False)
def requests_page() -> HTMLResponse:
    return HTMLResponse(page("Requests", "requests", _requests_body(_live_router())))


@pages.get("/brain", response_class=HTMLResponse, include_in_schema=False)
def brain_page() -> HTMLResponse:
    return HTMLResponse(page("Error brain", "brain", _brain_body(_live_router())))


@pages.get("/allowance", response_class=HTMLResponse, include_in_schema=False)
def allowance_page() -> HTMLResponse:
    return HTMLResponse(page("Allowance", "allowance", _allowance_body(_live_router())))


@pages.get("/buckets", response_class=HTMLResponse, include_in_schema=False)
def buckets_page(ok: str = "", message: str = "") -> HTMLResponse:
    banner = _message_banner(ok, message)
    return HTMLResponse(page("Buckets", "buckets", _buckets_body(_live_router(), banner)))


@pages.post("/buckets", include_in_schema=False)
async def buckets_add(request: Request) -> RedirectResponse:
    form = await request.form()
    name = (form.get("name") or "").strip()
    try:
        if not name:
            raise ValueError("a bucket needs a name")
        ov.add_bucket(name)
    except ValueError as e:
        return _redirect_with_message("/buckets", ok=False, message=str(e))
    return _redirect_with_message("/buckets", ok=True, message=f"bucket {name!r} added")


@pages.post("/buckets/{bucket}/models", include_in_schema=False)
async def buckets_add_model(bucket: str, request: Request) -> RedirectResponse:
    form = await request.form()
    try:
        fields = {
            "provider": (form.get("provider") or "").strip(),
            "model": (form.get("model") or "").strip(),
            "score": int(form.get("score")),
            "rpm": int(form.get("rpm")),
            "tpm": int(form.get("tpm")),
        }
        context_window = form.get("context_window")
        if context_window:
            fields["context_window"] = int(context_window)
        if "vision" in form:
            fields["vision"] = True
        ov.add_model(bucket, fields)
    except (ValueError, TypeError) as e:
        return _redirect_with_message("/buckets", ok=False, message=str(e))
    return _redirect_with_message(
        "/buckets", ok=True, message=f"{fields['provider']}/{fields['model']} added")


@pages.get("/providers", response_class=HTMLResponse, include_in_schema=False)
def providers_page(ok: str = "", message: str = "") -> HTMLResponse:
    banner = _message_banner(ok, message)
    return HTMLResponse(page("Providers & keys", "providers", _providers_body(_live_router(), banner)))


@pages.post("/providers", include_in_schema=False)
async def providers_add(request: Request) -> RedirectResponse:
    form = await request.form()
    name = (form.get("name") or "").strip()
    try:
        if not name:
            raise ValueError("a provider needs a name")
        fields = {"base_url": (form.get("base_url") or "").strip()}
        for optional in ("header_parser", "key_strategy"):
            if form.get(optional):
                fields[optional] = form.get(optional)
        ov.add_provider(name, fields)
    except ValueError as e:
        return _redirect_with_message("/providers", ok=False, message=str(e))

    # The provider write above already landed - a failure past this point
    # (e.g. the key file can't be written) must not look like the whole
    # submission failed, since the provider it named now genuinely exists.
    dest = f"/providers/{quote(name, safe=':')}"
    secret = (form.get("key_secret") or "").strip()
    if not secret:
        return _redirect_with_message(dest, ok=True, message=f"provider {name!r} added")
    try:
        keystore.add_key(name, secret, (form.get("key_label") or "").strip())
    except (ValueError, OSError) as e:
        return _redirect_with_message(
            dest, ok=False, message=f"provider {name!r} added, but its key failed: {e}")
    return _redirect_with_message(
        dest, ok=True, message=f"provider {name!r} and its key added")


@pages.post("/providers/{provider}/models", include_in_schema=False)
async def provider_add_model(provider: str, request: Request) -> RedirectResponse:
    form = await request.form()
    dest = f"/providers/{quote(provider, safe=':')}"
    bucket = (form.get("bucket") or "").strip()
    model = (form.get("model") or "").strip()
    try:
        if not bucket:
            raise ValueError("choose a bucket")
        fields = {
            "provider": provider,
            "model": model,
            "score": int(form.get("score")),
            "rpm": int(form.get("rpm")),
            "tpm": int(form.get("tpm")),
        }
        context_window = form.get("context_window")
        if context_window:
            fields["context_window"] = int(context_window)
        if "vision" in form:
            fields["vision"] = True
        ov.add_model(bucket, fields)
    except (ValueError, TypeError) as e:
        return _redirect_with_message(
            dest, ok=False, message=f"{provider}/{model or '?'}: {e}")
    return _redirect_with_message(
        dest, ok=True, message=f"{provider}/{model} added to {bucket}")


@pages.get("/providers/{provider}", response_class=HTMLResponse, include_in_schema=False)
def provider_detail_page(provider: str, tested: str = "", ok: str = "",
                         message: str = "", status: str = "") -> HTMLResponse:
    router = _live_router()
    detail = facts.provider_detail(router, provider)
    if detail is None:
        return HTMLResponse(
            page("Providers & keys", "providers",
                 tag("h1", "No such provider") + tag("p", esc(provider))),
            status_code=404,
        )

    banner = ""
    if tested:
        outcome = f"Test of {tested}: {message}" + (f" (HTTP {status})" if status else "")
        banner = tag("p", esc(outcome), cls=f"state-{'ok' if ok == '1' else 'bad'}")
    elif message:
        banner = _message_banner(ok, message)

    editable = facts.provider_editable_fields(router, provider)
    bucket_names = list(router._cfg.tiers)
    return HTMLResponse(
        page(f"{provider} - Providers & keys", "providers",
             _provider_detail_body(detail, editable, bucket_names, banner))
    )


@pages.post("/providers/{provider}/edit", include_in_schema=False)
async def provider_edit(provider: str, request: Request) -> RedirectResponse:
    form = await request.form()
    dest = f"/providers/{quote(provider, safe=':')}"
    try:
        fields = {}
        for name in ("base_url", "header_parser", "key_strategy"):
            value = form.get(name)
            if value:
                fields[name] = value
        settings_write.set_provider_fields(provider, fields)
    except ValueError as e:
        return _redirect_with_message(dest, ok=False, message=str(e))
    return _redirect_with_message(dest, ok=True, message="saved")


@pages.post("/providers/{provider}/clear", include_in_schema=False)
async def provider_clear(provider: str) -> RedirectResponse:
    settings_write.clear_provider(provider)
    return _redirect_with_message(
        f"/providers/{quote(provider, safe=':')}", ok=True, message="changes put back")


@pages.post("/providers/{provider}/keys/{key_id}/test", include_in_schema=False)
async def provider_key_test(provider: str, key_id: str) -> RedirectResponse:
    result = await keytest.test_key(_live_router(), provider, key_id)
    qs = (
        f"tested={quote(key_id)}"
        f"&ok={'1' if result.ok else '0'}"
        f"&message={quote(result.message)}"
        f"&status={result.status_code or ''}"
    )
    return RedirectResponse(url=f"/providers/{quote(provider, safe=':')}?{qs}", status_code=303)


@pages.get("/settings", response_class=HTMLResponse, include_in_schema=False)
def settings_page(ok: str = "", message: str = "") -> HTMLResponse:
    banner = _message_banner(ok, message)
    return HTMLResponse(page("Settings", "settings", _settings_body(_live_router(), banner)))


@pages.post("/settings/{field}/clear", include_in_schema=False)
async def settings_clear(field: str) -> RedirectResponse:
    settings_write.clear_settings_field(field)
    return _redirect_with_message("/settings", ok=True, message=f"{field} put back")


@pages.post("/settings/{field}", include_in_schema=False)
async def settings_set(field: str, request: Request) -> RedirectResponse:
    form = await request.form()
    raw_value = form.get("value", "")
    try:
        value = _cast_setting(field, raw_value)
        settings_write.set_settings_field(field, value)
    except ValueError as e:
        return _redirect_with_message("/settings", ok=False, message=str(e))
    return _redirect_with_message("/settings", ok=True, message=f"{field} saved")


@pages.post("/settings/keys/{name}", include_in_schema=False)
async def settings_key_set(name: str, request: Request) -> RedirectResponse:
    if name not in service_keys.SERVICES:
        return _redirect_with_message("/settings", ok=False, message=f"unknown service {name!r}")
    form = await request.form()
    secret = (form.get("secret") or "").strip()
    if not secret:
        return _redirect_with_message("/settings", ok=False, message="paste a key first")
    service_keys.set_key(name, secret)
    return _redirect_with_message("/settings", ok=True, message=f"{name} key saved")


@pages.post("/settings/keys/{name}/remove", include_in_schema=False)
async def settings_key_remove(name: str) -> RedirectResponse:
    if name not in service_keys.SERVICES:
        return _redirect_with_message("/settings", ok=False, message=f"unknown service {name!r}")
    service_keys.clear_key(name)
    return _redirect_with_message("/settings", ok=True, message=f"{name} key removed")


