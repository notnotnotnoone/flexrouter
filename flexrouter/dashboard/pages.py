"""The dashboard's pages.

Server-rendered HTML, no build step, no JavaScript. Every area is a real URL
and every control is a real form, so there is no client state that can
disagree with the service - see the Stage 8 roadmap, ruling R2. All nine
areas are built as of sub-plan 7.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from flexrouter import keys as keystore
from flexrouter import model_reset
from flexrouter import log_setup
from flexrouter import overrides as ov
from flexrouter import parked_models
from flexrouter import presets
from flexrouter import rate_limit_facts
from flexrouter.exceptions import RouterBusy, RouterError
from flexrouter.probe import probe_key
from flexrouter import score_facts
from flexrouter import service_keys
from flexrouter.dashboard import (add_models, facts, keytest, overview,
                                  pending_actions, ranking, settings_write, ui)
from flexrouter.dashboard import allowance_page as allowance_page_mod
from dataclasses import asdict

from fastapi.responses import Response

from flexrouter import app_password
from flexrouter.dashboard import prefs as dashboard_prefs
from flexrouter.dashboard import settings_page as settings_page_mod
from flexrouter.dashboard import brain_page as brain_page_mod
from flexrouter.dashboard import broken_page as broken_page_mod
from flexrouter.dashboard import logs_page as logs_page_mod
from flexrouter.dashboard import requests_page as requests_page_mod
from flexrouter.dashboard.render import attrs, esc, page, tag

pages = APIRouter()



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


# The Overview's ranges and formatting helpers live in overview.py; these
# names are kept because other pages (and tests) use them.
RANGES = overview.RANGES
DEFAULT_RANGE = overview.DEFAULT_RANGE
_range = overview.range_for
_num = overview.num
_chart_series = overview.chart_series
_KIND_LABELS = overview.KIND_LABELS


def _panel(title: str, body: str, *, sub: str = "", action: str = "") -> str:
    """A titled block on the Overview. `body` is already-rendered HTML."""
    head = tag("h2", esc(title))
    if sub:
        head += tag("span", esc(sub), cls="sub")
    if action:
        head += tag("span", "", cls="spacer") + action
    return tag("section", tag("div", head, cls="ph") + body, cls="panel")


def _field(label: str, input_html: str) -> str:
    """One form control with a small caption above it, instead of a bare
    input whose meaning only shows up in a hover tooltip."""
    return tag("label", tag("span", esc(label)) + input_html, cls="field")


# name -> caption, shared by every place a model's rate limits can be set
# (add-model forms, the edit form, the AI-paste review table) so "where do I
# set rps/tps" has one answer instead of depending on which form you're on.
_QUOTA_FIELDS = (
    ("rph", "Requests an hour"), ("rpd", "Requests a day"), ("rps", "Requests a second"),
    ("tph", "Tokens an hour"), ("tpd", "Tokens a day"), ("tps", "Tokens a second"),
)

# A key's own caps get rpm/tpm too, on top of the six a model has - a key is
# a real account-level credential and per-minute is the most commonly
# published window for one. Models don't get these here because ModelConfig
# already has dedicated rpm/tpm fields outside this dict (models.py's
# `_model_edit_form`); adding them here too would collide with those field
# names on that form.
_KEY_QUOTA_FIELDS = (("rpm", "Requests a minute"), ("tpm", "Tokens a minute")) + _QUOTA_FIELDS

# rps/tps convert from a day/month allowance into a per-second one and are
# legitimately fractional (e.g. Mistral's real published limits give 2.08,
# 0.63, 12.5) - live-verified against a provider's real numbers. The other
# fields stay whole numbers. Also used by add_models.py's own parser.
_FRACTIONAL_QUOTA_FIELDS = frozenset({"rps", "tps"})


def _quota_fields_html(quotas: Optional[dict] = None, fields=_QUOTA_FIELDS) -> str:
    """The optional rps/rph/rpd/tps/tph/tpd (and, for a key, rpm/tpm)
    inputs, pre-filled from an existing quotas dict. Blank means "no cap"."""
    quotas = quotas or {}
    return "".join(
        _field(label, _input(type="number",
                             **({"step": "any"} if name in _FRACTIONAL_QUOTA_FIELDS else {}),
                             name=name, value=esc(quotas[name]) if name in quotas else "",
                             placeholder="no cap"))
        for name, label in fields
    )


def _parse_quota_fields(form, fields=_QUOTA_FIELDS) -> dict:
    """The quota fields from a submitted form, as a `{rph: 100, ...}` dict
    holding only the ones that were actually filled in."""
    out: dict = {}
    for name, _ in fields:
        parser = _opt_float if name in _FRACTIONAL_QUOTA_FIELDS else _opt_int
        value = parser(form.get(name))
        if value is not None:
            out[name] = value
    return out


def _add_provider_form() -> str:
    return tag(
        "form",
        _field("Name", _input(type="text", name="name", placeholder="name"))
        + _field("Base URL", _input(type="text", name="base_url", placeholder="base URL"))
        + _field("Header parser", _input(type="text", name="header_parser",
              placeholder="optional"))
        + _field("Key strategy", _input(type="text", name="key_strategy",
              placeholder="optional"))
        + _field("Key", _input(type="text", name="key_secret",
              placeholder="optional"))
        + _field("Key label", _input(type="text", name="key_label",
              placeholder="optional"))
        + tag("button", "Add provider", type="submit"),
        method="post", action="/providers", cls="grouped-form",
    )


def _provider_row(s) -> str:
    return tag("tr", "".join([
        tag("td", tag("a", esc(s.name), href=f"/providers/{quote(s.name)}")),
        tag("td", ui.status(s.state)),
        tag("td", esc(s.base_url), cls="dim"),
        tag("td", esc(f"{s.keys_live} live, {s.keys_cooling} resting, {s.keys_parked} parked")),
        tag("td", _num(s.models_total), cls="num"),
        tag("td", esc(s.quarantine_reason or ""), cls="dim"),
    ]))


def _preset_card(p, configured: bool) -> str:
    tier = "free tier" if p.free else "paid"
    if configured:
        inner = (
            tag("span", esc(p.label), cls="preset-name")
            + tag("span", esc(tier), cls="preset-tier dim")
            + tag("a", "Open", href=f"/providers/{quote(p.name)}", cls="button-link")
        )
        return tag("div", inner, cls="preset-card is-configured")
    inner = (
        tag("span", esc(p.label), cls="preset-name")
        + tag("span", esc(tier), cls="preset-tier dim")
        + tag("a", "Add", href=f"/providers/add/{quote(p.name)}", cls="button-link",
              **{"hx-get": f"/providers/add/{quote(p.name)}?panel=1",
                 "hx-target": "#sheet-root", "hx-swap": "innerHTML",
                 "hx-push-url": f"/providers/add/{quote(p.name)}"})
    )
    return tag("div", inner, cls="preset-card")


def _preset_grid(router) -> str:
    known = set(router._cfg.providers)
    cards = "".join(
        _preset_card(p, p.name in known)
        for p in sorted(presets.all().values(), key=lambda x: (x.name not in known, x.label))
    )
    trouble = presets.problems()
    note = (
        tag("p", esc("Some of your own presets could not be read: "
                     + "; ".join(trouble)), cls="state-bad")
        if trouble else ""
    )
    return tag("div", note + tag("div", cards, cls="preset-grid"), cls="pb")


def _providers_body(router, banner: str = "") -> str:
    summaries = facts.provider_summaries(router)

    if summaries:
        header = tag("tr", "".join([
            tag("th", "Provider"), tag("th", "State"), tag("th", "Address"),
            tag("th", "Keys"), tag("th", "Models"), tag("th", "Why"),
        ]))
        rows = "".join(_provider_row(s) for s in summaries)
        table_html = tag("div", tag("table", header + rows, cls="matrix"), cls="scroll")
        sub = f"{len(summaries)} configured"
    else:
        table_html = tag("div", tag("p", "No providers configured yet.", cls="note"), cls="pb")
        sub = ""

    providers_panel = _panel("Providers", table_html, sub=sub)
    add_panel = _panel(
        "Add a provider",
        _preset_grid(router),
        sub="pick one, paste a key, import its models",
    )
    custom_panel = _panel(
        "Custom provider",
        tag("div",
            tag("p", "For anything not in the list above. You supply the "
                     "address; everything else works the same.", cls="note")
            + _add_provider_form(), cls="pb"),
    )

    return (
        tag("div", tag("h1", "Providers & keys", cls="page-title"), cls="page-head")
        + banner
        + tag("p", "Every provider you've configured, and every key it "
                   "holds. Click a provider for its keys.", cls="lede")
        + tag("div", providers_panel + add_panel + custom_panel, cls="panel-page")
        + _danger_zone("/providers/reset-all", "the models of every provider", "reset all",
                       model_reset.preview(router._cfg.state_dir))
    )


def _danger_zone(action: str, what: str, phrase: str, counts: dict) -> str:
    """A reset form that only works once `phrase` is typed exactly
    (flexrouter/model_reset.py does the work, and backs up first)."""
    found = (f"{counts.get('models', 0):,} model(s), {counts.get('field_edits', 0):,} "
             f"field edit(s) and {counts.get('facts', 0):,} learned fact(s) would be deleted.")
    return tag("div", _panel(
        "Danger zone",
        tag("div",
            tag("p", esc(f"Deletes {what}: models in every bucket, field edits, parked "
                         "models, scores, rate limits, capability facts, quarantine, "
                         "penalties and pending suggestions. Keys, providers, settings, "
                         "buckets and request history are not touched. Every changed file "
                         "is backed up to state/backups first."), cls="note")
            + tag("p", esc(found), cls="danger-count")
            + tag("form",
                  tag("label", esc(f"Type {phrase} to confirm "))
                  + f"<input{attrs({'name': 'confirm', 'autocomplete': 'off', 'aria-label': 'Confirmation'})}>"
                  + " " + tag("button", "Reset models and scores", type="submit", cls="danger"),
                  method="post", action=action, cls="set-actions"),
            cls="pb"),
    ), cls="danger-zone")


def _key_fact(label: str, value: object) -> str:
    return tag("span", tag("span", esc(label) + " ", cls="dim") + esc(value))


def _key_row(detail, k) -> str:
    until = f", back in {int(k.until - time.time())}s" if k.until else ""
    status = f"{k.status}{until}"
    status_cls = "ok" if k.status == "live" else "warn" if k.status == "cooling" else "bad"
    last_used = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(k.last_used_at))
        if k.last_used_at else "never"
    )
    latency = f"{k.ema_latency_ms:.0f} ms" if k.ema_latency_ms else "-"
    test_form = tag(
        "form",
        tag("button", "Test", type="submit"),
        method="post",
        action=f"/providers/{quote(detail.name, safe=':')}/keys/{quote(k.id, safe=':')}/test",
        cls="key-test",
    )
    remove_form = tag(
        "form",
        tag("button", "Remove", type="submit", cls="danger"),
        method="post",
        action=f"/providers/{quote(detail.name, safe=':')}/keys/{quote(k.id, safe=':')}/remove",
        cls="key-remove",
    )
    edit_action = f"/providers/{quote(detail.name, safe=':')}/keys/{quote(k.id, safe=':')}/edit"
    copy_action = f"/providers/{quote(detail.name, safe=':')}/keys/{quote(k.id, safe=':')}/copy-to-all"
    edit_form = tag(
        "form",
        tag("div",
            _field("Label", _input(type="text", name="label", value=esc(k.label)))
            + _field("Weight", _input(type="number", name="weight", value=esc(k.weight), min="1"))
            + _field("Only these models",
                     _input(type="text", name="allow_models",
                            value=esc(" ".join(k.allow_models)),
                            placeholder="* (all)")),
            cls="key-edit-identity")
        + tag("div", _quota_fields_html(k.quotas, fields=_KEY_QUOTA_FIELDS), cls="key-edit-quotas")
        + tag("div",
              tag("label",
                  _input(type="checkbox", name="enabled",
                         **({"checked": True} if k.enabled else {}))
                  + "enabled", cls="check-field")
              + tag("button", "Save", type="submit")
              + tag("button", "Save & copy to all keys", type="submit",
                    formaction=copy_action, cls="ghost",
                    title="Save this key, then copy its weight, allowed models, "
                          "caps and enabled state onto every other key for this provider"),
              cls="key-edit-actions"),
        method="post", action=edit_action, cls="key-edit-form",
    )

    head = tag(
        "div",
        tag("span", esc(k.label or k.id), cls="key-label")
        + tag("span", esc(k.masked), cls="key-value dim")
        + tag("span", esc(status), cls=f"key-status state-{status_cls}")
        + tag("span", esc("enabled" if k.enabled else "disabled"), cls="dim")
        + test_form
        + remove_form,
        cls="key-head",
    )
    facts_line = tag(
        "div",
        _key_fact("Weight", k.weight)
        + _key_fact("Allowed", ", ".join(k.allow_models) or "all")
        + (_key_fact("Key caps", ", ".join(f"{n}={v}" for n, v in k.quotas.items()))
           if k.quotas else "")
        + _key_fact("Today", f"{k.requests_today:,} req / {k.tokens_today:,} tok")
        + _key_fact("Failures (24h)", k.failures_24h)
        + _key_fact("Consecutive fails", k.consecutive_failures)
        + _key_fact("Active now", k.active_requests)
        + _key_fact("Typical latency", latency)
        + _key_fact("Last used", last_used)
        + _key_fact("Source", k.source),
        cls="key-facts",
    )
    why = tag("div", esc(k.reason), cls="key-reason dim") if k.reason else ""
    return tag("div", head + facts_line + why + edit_form, cls="key-row")


def _key_rows(detail) -> str:
    if not detail.keys:
        return tag("div", tag("p", "No keys configured for this provider.", cls="note"), cls="pb")
    return "".join(_key_row(detail, k) for k in detail.keys)


def _provider_edit_form(detail, editable: dict) -> str:
    fields = editable["fields"]
    labels = {
        "base_url": "Base URL", "header_parser": "Header parser",
        "key_strategy": "Key strategy",
    }
    inputs = "".join(
        _field(labels[name], _input(type="text", name=name,
            value=esc(fields[name]["value"])))
        for name in ("base_url", "header_parser", "key_strategy")
    )
    edit = tag(
        "form", inputs + tag("button", "Save", type="submit"),
        method="post", action=f"/providers/{quote(detail.name, safe=':')}/edit",
        cls="grouped-form",
    )
    clear = (
        tag("form", tag("button", "Put it all back", type="submit"),
            method="post", action=f"/providers/{quote(detail.name, safe=':')}/clear")
        if editable["overridden_at_all"] else ""
    )
    return edit + clear


def _provider_add_model_form(provider: str, bucket_names: list) -> str:
    bucket_options = "".join(
        tag("option", esc(b), value=esc(b)) for b in bucket_names
    )
    picker = f"<select{attrs({'name': 'bucket'})}>{bucket_options}</select>"
    return tag(
        "form",
        _field("Bucket", picker)
        + _field("Model", _input(type="text", name="model", placeholder="model"))
        + _field("Score", _input(type="number", name="score", placeholder="score"))
        + _field("RPM", _input(type="number", name="rpm", placeholder="rpm"))
        + _field("TPM", _input(type="number", name="tpm", placeholder="tpm"))
        + _field("Context", _input(type="number", name="context_window",
              placeholder="optional"))
        + _quota_fields_html()
        + _field("Gen tokens/s", _input(type="number", name="tokens_per_second", step="any",
              placeholder="optional"))
        + tag("label", _input(type="checkbox", name="vision") + "vision", cls="check-field")
        + tag("button", "Add model", type="submit"),
        method="post", action=f"/providers/{quote(provider, safe=':')}/models",
        cls="grouped-form",
    )


def _provider_models_line(label: str, models: list) -> str:
    if not models:
        return tag("div", tag("span", esc(label) + " ", cls="dim") + "none", cls="model-line")
    return tag("div", tag("span", esc(label) + " ", cls="dim") + esc(", ".join(models)),
              cls="model-line")


def _provider_detail_body(detail, editable: dict, bucket_names: list,
                          banner: str = "", reset_counts: dict | None = None) -> str:
    quarantine_note = (
        tag("p", esc(detail.quarantine_reason), cls="state-bad")
        if detail.quarantined else ""
    )

    models_panel = _panel(
        "Models",
        tag("div",
            _provider_models_line("Alive:", detail.models_alive)
            + _provider_models_line("Gone:", detail.models_gone),
            cls="pb"),
    )
    add_model_panel = _panel(
        "Add a model",
        tag("div",
            tag("p", "One model per submission, into the bucket you choose. "
                     "Submit again for the next one.", cls="note")
            + _provider_add_model_form(detail.name, bucket_names),
            cls="pb"),
    )
    keys_panel = _panel(
        "Keys",
        _key_rows(detail)
        + tag("div",
              tag("p", "One key per account. Two keys from the same account "
                       "share that account's rate limit, and flexrouter "
                       "counts them separately - so add a second key only "
                       "when it is a second signup.", cls="note")
              + tag(
                  "form",
                  _field("Key", _input(type="password", name="secret",
                                       placeholder="paste it here"))
                  + _field("Label", _input(type="text", name="label",
                                           placeholder="which account this is"))
                  + tag("button", "Add key", type="submit"),
                  method="post", action=f"/providers/{quote(detail.name, safe=':')}/keys",
                  cls="grouped-form",
              ),
              cls="pb"),
        sub="test sends one real chat request through this key",
    )
    edit_panel = _panel(
        "Change it",
        tag("div", _provider_edit_form(detail, editable), cls="pb"),
    )

    return (
        tag("div", tag("h1", esc(detail.name), cls="page-title"), cls="page-head")
        + banner
        + tag("p", esc(detail.base_url), cls="lede")
        + quarantine_note
        + tag("div",
              models_panel + add_model_panel + keys_panel + edit_panel,
              cls="panel-page")
        + _danger_zone(f"/providers/{quote(detail.name, safe=':')}/reset",
                       f"every {detail.name} model", detail.name, reset_counts or {})
    )


_CAP_SOURCE_WORDS = {
    "published": "the provider says so",
    "observed": "seen working",
    "guessed": "nobody has said either way yet",
    "manual": "you set it; nothing overrides it",
}


def _cap_cell(fact, name: str = "") -> str:
    """One capability as a tag. The tag's style says where the fact came
    from (solid published, outlined observed, faded guessed, underlined
    manual); hovering says it in words."""
    if fact is None:
        return tag("span", esc(name or "unknown"), cls="cap cap-unknown",
                   title="not known yet")
    if fact.status == "no":
        return ""
    word = name or fact.status
    mark = "?" if fact.status == "doubted" else ""
    return tag("span", esc(word + mark), cls=f"cap cap-{fact.source}",
               title=f"{fact.status} - {_CAP_SOURCE_WORDS.get(fact.source, fact.source)}")


def _size_tag(tokens) -> str:
    if not tokens:
        return ""
    k = int(tokens) // 1000
    return tag("span", esc(f"{k}K" if k < 1000 else f"{k // 1000}M"), cls="cap cap-size",
               title=f"{int(tokens):,} tokens of context")


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
            url = f"/models_catalog/pending/{quote(provider, safe=':')}/appeared/{quote(model, safe=':')}"
            picker = f"<select{attrs({'name': 'bucket'})}>{bucket_options}</select>"
            items.append(tag("li", esc(f"{provider}/{model} appeared")
                             + " " + _pending_action_form("accept", url, picker)
                             + " " + _pending_action_form("reject", url)))
        for model_id in bucket.get("vanished") or []:
            url = f"/models_catalog/pending/{quote(provider, safe=':')}/vanished/{quote(model_id, safe=':')}"
            items.append(tag("li", esc(f"{provider}/{model_id} vanished")
                             + " " + _pending_action_form("accept", url)
                             + " " + _pending_action_form("reject", url)))
        for c in bucket.get("changed") or []:
            model = c.get("model")
            field = c.get("field")
            url = (f"/models_catalog/pending/{quote(provider, safe=':')}/changed/"
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
        + _field("Score", _input(type="number", name="score", value=esc(r.score)))
        + _field("Requests a minute", _input(type="number", name="rpm", value=esc(r.rpm)))
        + _field("Tokens a minute", _input(type="number", name="tpm", value=esc(r.tpm)))
        + _field("Context window", _input(type="number", name="context_window",
                                          value=esc(r.context_window)))
        + _quota_fields_html(r.quotas)
        + _field("Gen tokens/s", _input(type="number", name="tokens_per_second", step="any",
              value=esc(r.tokens_per_second) if r.tokens_per_second is not None else ""))
        + tag("label", _input(type="checkbox", name="vision",
                              **{"checked": True} if vision_checked else {})
              + "Can see images", cls="check-field")
        + tag("button", "Save", type="submit"),
        method="post", action=f"/models_catalog/{ident}", cls="grouped-form",
    )
    disable = tag(
        "form",
        _input(type="hidden", name="action", value="disable")
        + tag("button", "Disable this model", type="submit", cls="danger"),
        method="post", action=f"/models_catalog/{ident}",
    )
    return edit + disable


def _models_body(router, banner: str = "") -> str:
    rows_data = facts.models(router)

    header = tag("tr", "".join(
        tag("th", h, cls=c, **({"data-sort": k} if k else {}))
        for h, c, k in [("Model", "", "model"), ("Buckets", "", ""), ("Score", "", "score"),
                        ("Answered", "", "response-rate"), ("Can do", "", ""),
                        ("State", "", "state"), ("", "", "")]))
    rows = []
    top_score = max((r.score for r in rows_data), default=0) or 1
    providers = sorted({r.provider for r in rows_data})
    for n, r in enumerate(rows_data):
        caps = (_cap_cell(r.vision, "vision") + _cap_cell(r.tools, "tools")
                + _cap_cell(r.reasoning, "reason") + _size_tag(r.learned_context or r.context_window))
        ok = r.state == "available"
        search = f"{r.provider} {r.model} {' '.join(r.buckets)}".lower()
        rows.append(tag("tr", "".join([
            tag("td", tag("span", esc(r.provider), cls="m-prov") + tag("span", esc(r.model),
                                                                        cls="m-name")),
            tag("td", "".join(tag("span", esc(b), cls="tag") for b in r.buckets)),
            tag("td", tag("div", tag("span", "", cls="m-bar",
                                     style=f"width:{r.score / top_score * 100:.0f}%"),
                          cls="m-track") + tag("span", esc(r.score), cls="m-score")),
            tag("td", esc(f"{r.response_rate:.0%} ({r.requests:,} reqs)")
                if r.response_rate is not None else tag("span", "no data yet", cls="dim")),
            tag("td", tag("div", caps, cls="caps")),
            tag("td", ui.status("ok" if ok else "bad")
                + (tag("div", esc(r.why), cls="m-why") if r.why else "")),
            tag("td", tag("button", ui.icon("arrow-right"), type="button", cls="m-open",
                          **{"aria-label": f"Details for {r.model}", "data-toggle": f"m-{n}"})),
        ]), cls="m-row", **{"data-search": search, "data-provider": r.provider,
                            "data-score": r.score, "data-model": r.model,
                            "data-state": r.state, "data-toggle-row": f"m-{n}",
                            "data-response-rate": (
                                r.response_rate if r.response_rate is not None else "")}))
        detail = tag("div",
                     tag("div",
                         tag("h4", "Change this model")
                         + _model_edit_form(r)
                         + (tag("p", esc(f"Priced at ${r.price_in}/M in, ${r.price_out}/M out"),
                                cls="note") if r.price_in is not None else ""),
                         cls="m-detail-body"),
                     cls="m-detail-inner")
        rows.append(tag("tr", tag("td", detail, colspan="7"), cls="m-detail", id=f"m-{n}",
                        hidden=True))
    if not rows:
        rows.append(tag("tr", tag("td", "No models yet. Add a provider key on Providers & keys "
                                        "and its models show up here.", colspan="7"),
                        cls="models-empty"))
    provider_opts = "".join(f'<option value="{esc(p)}">{esc(p)}</option>' for p in providers)
    filters = tag("div",
                  ui.icon("search")
                  + '<input type="search" placeholder="Find a model" aria-label="Find a model" '
                    'data-page-search data-filter=".m-row">'
                  + f'<select data-filter-attr="provider" data-filter-target=".m-row" '
                    f'aria-label="Provider"><option value="">Any provider</option>{provider_opts}'
                    "</select>",
                  cls="filters")
    table = tag("table", tag("thead", header) + tag("tbody", "".join(rows)),
                cls="models-table", **{"data-sortable": ""})

    discovery_on = router._cfg.experimental_model_discovery
    pending = facts.pending_catalogue(router) if discovery_on else {}
    bucket_names = list(router._cfg.tiers)

    disabled = facts.disabled_models()
    disabled_rows = [tag("tr", "".join([
        tag("td", esc(ident)),
        tag("td", tag(
            "form",
            _input(type="hidden", name="action", value="clear")
            + tag("button", "Put it back", type="submit"),
            method="post",
            action=f"/models_catalog/{_model_ident_path(*ident.split('/', 1))}",
        )),
    ])) for ident in disabled]
    disabled_section = (
        tag("table", "".join(disabled_rows)) if disabled_rows
        else tag("p", "No disabled models.", cls="note")
    )

    parked = parked_models.all(router._cfg.state_dir)
    parked_rows = "".join(tag("tr", "".join([
        tag("td", esc(p.get("provider"))),
        tag("td", esc(p.get("model"))),
        tag("td", esc(p.get("kind"))),
    ])) for p in parked)
    parked_section = (
        tag("table", tag("tr", "".join(tag("th", h) for h in ["Provider", "Model", "Kind"]))
            + parked_rows)
        if parked else tag("p", "Nothing parked.", cls="note")
    )

    n_pending = sum(len(b.get(kind) or []) for b in pending.values() if isinstance(b, dict)
                    for kind in ("appeared", "vanished", "changed"))
    status = f"{len(rows_data)} models in use · {len(disabled)} disabled"
    if n_pending:
        status += f" · {n_pending} catalogue changes waiting"
    head = tag("div",
               tag("div", tag("h1", "Models", cls="page-title")
                   + tag("p", esc(status), cls="page-status"), cls="page-head-text")
               + tag("div", ui.button("Rank models with an AI", href="/models_catalog/rank",
                                      icon_name="brain")
                     + ui.button("Get rate limits with an AI", href="/models_catalog/rate-limits",
                                icon_name="gauge")
                     + ui.button("Add models with AI", href="/models_catalog/add-with-ai",
                                icon_name="plus"), cls="page-actions"),
               cls="page-head")
    legend = tag("div", tag("span", "Tag styles say where a fact came from:")
                 + "".join(tag("span", tag("span", label, cls=f"cap cap-{kind}") + esc(meaning),
                               cls="cap-item")
                           for label, kind, meaning in [
                               ("solid", "published", "the provider says so"),
                               ("outlined", "observed", "seen working"),
                               ("faded", "guessed", "a guess"),
                               ("underlined", "manual", "set by you")]),
                 cls="note cap-legend")
    pending_tray = (
        ui.box("Pending catalogue changes",
               tag("p", "What the last catalogue check found. Accepting a new model adds it "
                        "to the bucket you choose; accepting a vanished one disables it; "
                        "accepting a changed field applies the provider's new value.",
                   cls="note") + _import_all_form(bucket_names)
               + _pending_body(pending, bucket_names),
               cls="tray" + (" has-items" if n_pending else ""),
               id="pending", **{"data-enter": ""})
        if discovery_on else ""
    )
    return (
        head + banner + filters
        + tag("div", tag("div", table, cls="scroll"), cls="box flush", **{"data-enter": ""})
        + legend
        + ui.box("Disabled models", disabled_section, **{"data-enter": ""})
        + ui.box("Saved, not routable yet",
                 tag("p", "Models 'Add models with AI' learned about that aren't chat "
                          "models - flexrouter only ever routes chat requests, so these "
                          "can never go in a bucket, but their fields are kept here.",
                     cls="note") + parked_section,
                 cls="tray" + (" has-items" if parked else ""), **{"data-enter": ""})
        + pending_tray
    )


def _import_all_form(bucket_names: list) -> str:
    if not bucket_names:
        return ""
    bucket_options = "".join(tag("option", esc(b), value=esc(b)) for b in bucket_names)
    return tag(
        "form",
        tag("label", "Discover and import every model from every provider with a valid "
                     "key, straight into ")
        + f"<select{attrs({'name': 'bucket'})}>{bucket_options}</select>"
        + " " + tag("button", "Discover and import everything", type="submit"),
        method="post", action="/models_catalog/import-all", cls="set-actions",
        **{"data-busy": "Checking every provider and scoring what it finds..."},
    )


def _rank_body(prompt: str, notes: str) -> str:
    notes_form = tag(
        "form",
        _textarea(esc(notes), name="notes", rows="6",
                 placeholder="benchmark material or notes (optional)")
        + " " + tag("button", "Build prompt", type="submit"),
        method="get", action="/models_catalog/rank",
    )
    paste_form = tag(
        "form",
        _textarea("", name="answer", rows="10",
                  placeholder="paste the AI's reply here, one line per "
                              "model: provider | model | score")
        + " " + tag("button", "Show proposed changes", type="submit"),
        method="post", action="/models_catalog/rank/proposal",
    )
    return (
        tag("h1", "Rank models with an AI", cls="page-title")
        + tag("p", "Builds a ready-made prompt from the current model "
                   "list plus whatever benchmark material you supply. "
                   "Send it yourself, or copy it into whatever chat "
                   "you're already in and paste the answer back below. "
                   "Nothing is applied until you say so on the next page, "
                   "and a score you've set by hand here is never "
                   "proposed a new value.", cls="lede")
        + tag("p", 'Need benchmark material to paste in as notes? '
                   '<a href="https://artificialanalysis.ai/leaderboards/models" '
                   'target="_blank" rel="noopener">Artificial Analysis\' leaderboard</a> '
                   "is a reasonable source - handy if the automatic AA "
                   "scoring on the Settings page isn't giving you what you expect.",
              cls="note")
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
            method="post", action="/models_catalog/rank/apply", cls="table-form",
        )

    skipped_note = (
        tag("p", esc(f"{len(skipped_manual)} model(s) skipped - their score "
                     f"is pinned by hand: {', '.join(skipped_manual)}"),
            cls="note")
        if skipped_manual else ""
    )

    return (
        tag("h1", "Proposed scores", cls="page-title")
        + tag("p", "Nothing here is applied until you press the button "
                   "below. Uncheck any row you don't want changed.",
              cls="lede")
        + skipped_note
        + body
    )


def _rate_limits_body(prompt: str, docs: str) -> str:
    docs_form = tag(
        "form",
        _textarea(esc(docs), name="docs", rows="10",
                 placeholder="paste the provider's rate-limit documentation here")
        + " " + tag("button", "Build prompt", type="submit"),
        method="post", action="/models_catalog/rate-limits/build",
    )
    body = (
        tag("h1", "Get rate limits with an AI", cls="page-title")
        + tag("p", "Same idea as ranking models: builds a ready-made prompt "
                   "from the current model list plus whatever docs text you "
                   "paste in. Send it to whichever model you actually use, "
                   "and paste the answer back below. Nothing is applied "
                   "until you say so on the next page, and a limit you've "
                   "set by hand here is never proposed a new value.",
              cls="lede")
        + tag("h3", "1. Paste the provider's rate-limit docs")
        + docs_form
    )
    if prompt:
        paste_form = tag(
            "form",
            _textarea("", name="answer", rows="10",
                      placeholder="paste the AI's reply here, one line per "
                                  "model: provider | model | rpm | tpm")
            + " " + tag("button", "Show proposed changes", type="submit"),
            method="post", action="/models_catalog/rate-limits/proposal",
        )
        body += (
            tag("h3", "2. The prompt - copy this")
            + _textarea(esc(prompt), rows="16", readonly=True)
            + tag("h3", "3. Paste the answer back")
            + paste_form
        )
    return body


def _rate_limits_proposal_body(changes: list, skipped_manual: list) -> str:
    if not changes:
        body = tag("p", "Nothing to propose - either the docs didn't mention "
                        "any configured model, or every model it found is "
                        "pinned by hand.", cls="note")
    else:
        rows_html = [tag("tr", "".join(tag("th", h) for h in [
            "Apply", "Provider", "Model", "Current rpm", "Proposed rpm",
            "Current tpm", "Proposed tpm",
        ]))]
        for row, proposed_rpm, proposed_tpm in changes:
            ident = f"{row.provider}/{row.model}"
            cell = (
                _input(type="checkbox", name=f"apply:{ident}", checked=True)
                + _input(type="hidden", name=f"rpm:{ident}", value=str(proposed_rpm))
                + _input(type="hidden", name=f"tpm:{ident}", value=str(proposed_tpm))
            )
            rows_html.append(tag("tr", "".join([
                tag("td", cell),
                tag("td", esc(row.provider)),
                tag("td", esc(row.model)),
                tag("td", esc(row.rpm)),
                tag("td", esc(proposed_rpm)),
                tag("td", esc(row.tpm)),
                tag("td", esc(proposed_tpm)),
            ])))
        body = tag(
            "form", tag("table", "".join(rows_html))
            + tag("button", "Apply checked limits", type="submit"),
            method="post", action="/models_catalog/rate-limits/apply", cls="table-form",
        )

    skipped_note = (
        tag("p", esc(f"{len(skipped_manual)} model(s) skipped - their rate "
                     f"limit is pinned by hand: {', '.join(skipped_manual)}"),
            cls="note")
        if skipped_manual else ""
    )

    return (
        tag("h1", "Proposed rate limits", cls="page-title")
        + tag("p", "Nothing here is applied until you press the button "
                   "below. Uncheck any row you don't want changed.",
              cls="lede")
        + skipped_note
        + body
    )


def _add_with_ai_step1_form(providers: list[str], provider: str, notes: str) -> str:
    opts = [tag("option", "choose a provider", value="")]
    opts += [
        tag("option", esc(p), value=esc(p), **({"selected": True} if p == provider else {}))
        for p in providers
    ]
    picker = f"<select{attrs({'name': 'provider'})}>{''.join(opts)}</select>"
    return tag(
        "form",
        _field("Provider", picker)
        + _field("Docs or model list (optional)",
                 _textarea(esc(notes), name="notes", rows="6",
                          placeholder="paste provider docs or a model list here (optional)"))
        + tag("button", "Build prompt", type="submit"),
        method="get", action="/models_catalog/add-with-ai", cls="grouped-form",
    )


def _add_with_ai_body(providers: list[str], provider: str, notes: str, prompt: str,
                      error: str = "") -> str:
    parts = [
        tag("h1", "Add models with AI", cls="page-title"),
        tag("p", "Builds a ready-made prompt naming one provider and whatever it already "
                 "has configured, plus any docs or model list you paste in. Send it "
                 "yourself, or copy it into whatever chat you're already in, then paste "
                 "the answer back below. Nothing is applied until you've reviewed every "
                 "row on the next page and say so.", cls="lede"),
    ]
    if error:
        parts.append(tag("p", esc(error), cls="state-bad"))
    parts.append(tag("h3", "1. Choose a provider"))
    parts.append(_add_with_ai_step1_form(providers, provider, notes))
    if prompt:
        parts.append(tag("h3", "2. The prompt - copy this"))
        parts.append(_textarea(esc(prompt), rows="16", readonly=True, id="add-ai-prompt"))
        parts.append(ui.button("Copy prompt", icon_name="check", **{"data-copy": "#add-ai-prompt"}))
        parts.append(tag("h3", "3. Paste the answer back"))
        parts.append(tag(
            "form",
            _input(type="hidden", name="provider", value=esc(provider))
            + _textarea("", name="answer", rows="10",
                       placeholder="paste the AI's reply here: a JSON array, one object per "
                                   "model")
            + " " + tag("button", "Show what would be added", type="submit"),
            method="post", action="/models_catalog/add-with-ai/review",
        ))
    return "".join(parts)


def _add_with_ai_row_html(i: int, row, bucket_names: list, is_update: bool, old) -> str:
    """One editable review row. `old` is the existing `ModelRow` (chat) or
    parked-models entry (anything else) when `is_update`, else None."""
    def _txt(field: str, value) -> str:
        shown = "none" if value is None else str(value)
        return _input(type="text", name=f"{field}:{i}", value=esc(shown))

    def _chk(field: str, checked: bool) -> str:
        return _input(type="checkbox", name=f"{field}:{i}",
                      **({"checked": True} if checked else {}))

    kind_opts = "".join(
        tag("option", k, value=k, **({"selected": True} if k == row.kind else {}))
        for k in add_models.KINDS)
    kind_select = f"<select{attrs({'name': f'kind:{i}'})}>{kind_opts}</select>"

    bucket_cell = ""
    if row.kind == "chat":
        default_bucket = ""
        if is_update and old is not None and getattr(old, "buckets", None):
            default_bucket = old.buckets[0]
        elif bucket_names:
            default_bucket = bucket_names[0]
        bopts = "".join(
            tag("option", esc(b), value=esc(b),
                **({"selected": True} if b == default_bucket else {}))
            for b in bucket_names)
        bucket_cell = f"<select{attrs({'name': f'bucket:{i}'})}>{bopts}</select>"

    status = tag("span", "update" if is_update else "new", cls="tag")
    if is_update and old is not None:
        if row.kind == "chat":
            old_ctx = old.learned_context or old.context_window
            detail = (f"was: context {old_ctx}, rpm {old.rpm}, tpm {old.tpm}, "
                      f"score {old.score}, tokens/s {old.tokens_per_second}")
        else:
            detail = (f"was: context {old.get('context')}, rpm {old.get('rpm')}, "
                      f"tpm {old.get('tpm')}, score {old.get('score')}, "
                      f"quotas {old.get('quotas', {})}")
        status += tag("div", esc(detail), cls="dim")

    cells = [
        tag("td", _chk("apply", True)),
        tag("td", _txt("score", row.score)),
        tag("td", _txt("provider", row.provider)),
        tag("td", tag("span", _txt("model", row.model), cls="stretch")),
        tag("td", kind_select),
        tag("td", _txt("context", row.context)),
        tag("td", _txt("rpm", row.rpm)),
        tag("td", _txt("tpm", row.tpm)),
        tag("td", _txt("rph", row.rph)),
        tag("td", _txt("rpd", row.rpd)),
        tag("td", _txt("rps", row.rps)),
        tag("td", _txt("tph", row.tph)),
        tag("td", _txt("tpd", row.tpd)),
        tag("td", _txt("tps", row.tps)),
        tag("td", _txt("gen_tps", row.gen_tps)),
        tag("td", _chk("vision", row.vision)),
        tag("td", _chk("free", row.free)),
        tag("td", status),
        tag("td", bucket_cell),
    ]
    return tag("tr", "".join(cells))


def _add_with_ai_review_body(rows_info: list, issues: list, bucket_names: list) -> str:
    parts = [
        tag("h1", "Review what to add", cls="page-title"),
        tag("p", "Nothing here is applied until you press the button below. Every cell "
                 "is editable - fix anything the AI got wrong before applying it. Uncheck "
                 "any row you don't want.", cls="lede"),
    ]
    if rows_info:
        header = tag("tr", "".join(tag("th", h) for h in [
            "Apply", "Score", "Provider", "Model", "Kind", "Context",
            "RPM", "TPM", "RPH", "RPD", "RPS", "TPH", "TPD", "TPS", "Tok/s",
            "Vision", "Free", "Status", "Bucket",
        ]))
        body_rows = "".join(
            _add_with_ai_row_html(i, row, bucket_names, is_update, old)
            for i, row, is_update, old in rows_info)
        table = tag("div", tag("table", header + body_rows, cls="matrix"), cls="scroll")
        parts.append(tag(
            "form",
            _input(type="hidden", name="row_count", value=str(len(rows_info)))
            + table
            + tag("button", "Apply checked rows", type="submit"),
            method="post", action="/models_catalog/add-with-ai/apply", cls="table-form",
        ))
    else:
        parts.append(tag("p", "Nothing parsed from that answer.", cls="note"))

    if issues:
        items = "".join(
            tag("li", tag("code", esc(issue.line)) + " - " + esc(issue.reason))
            for issue in issues)
        parts.append(ui.box(
            f"{len(issues)} line(s) could not be read",
            tag("p", "Never silently dropped - fix these in the AI's answer and paste "
                     "it back in, or ignore them.", cls="note") + tag("ul", items)))

    return "".join(parts)


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
        + _input(type="number", name="tokens_per_second", step="any",
              placeholder="tokens/s (optional)") + " "
        + "".join(_input(type="number", name=name, placeholder=name) + " "
                 for name, _ in _QUOTA_FIELDS)
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


_INT_SETTINGS = frozenset({
    "port", "dashboard_port", "window_seconds", "penalty_base_seconds",
    "penalty_max_seconds", "session_ttl_minutes", "sample_interval_seconds",
    "health_history_days", "key_concurrency_cap", "retries",
})
_FLOAT_SETTINGS = frozenset({"backoff_seconds"})
_JSON_SETTINGS = frozenset({"provider_budget", "hooks"})
_BOOL_SETTINGS = frozenset({"experimental_model_discovery", "redact_errors"})


def _cast_setting(field: str, raw_value: str):
    if field in _INT_SETTINGS:
        return int(raw_value)
    if field in _FLOAT_SETTINGS:
        return float(raw_value)
    if field in _JSON_SETTINGS:
        if not raw_value.strip():
            return {} if field == "provider_budget" else []
        return json.loads(raw_value)
    if field in _BOOL_SETTINGS:
        return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}
    return raw_value


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
    rescore = tag("form",
                  tag("button", "Re-score every model with Artificial Analysis", type="submit"),
                  method="post", action="/settings/rescore-models", cls="set-actions",
                  **{"data-busy": "Fetching Artificial Analysis' latest scores..."})
    return (
        tag("div", tag("h2", "Service keys"), cls="box-head")
        + tag("div",
              tag("p", "Keys flexrouter uses for its own calls, not for chat: scoring a "
                       "newly found model, and the Error brain's classifier. Masked "
                       "everywhere, never shown in full again.", cls="set-help")
              + tag("table", "".join(rows))
              + rescore
              + tag("p", "Re-scoring asks Artificial Analysis about every model you have "
                         "configured and saves the new scores as dashboard changes, so any "
                         "of them can be put back from the Models page.", cls="note"),
              cls="box-body")
    )


@pages.get("/playground", response_class=HTMLResponse, include_in_schema=False)
def playground_page() -> HTMLResponse:
    from flexrouter.dashboard import playground_page as pg
    return HTMLResponse(page("Playground", "playground", pg.body(_live_router())))


def _compare_targets(router, target: str) -> list[str]:
    """The distinct `provider/model` pins a `compare:...` target expands to.

    Raises KeyError (message forwarded to the client) if it names a bucket
    or provider that does not exist, or resolves to nothing.
    """
    all_models: list[str] = []
    seen = set()
    for models in router._cfg.tiers.values():
        for mc in models:
            ident = f"{mc.provider}/{mc.model}"
            if ident not in seen:
                seen.add(ident)
                all_models.append(ident)

    if target == "compare:all":
        pins = all_models
    elif target.startswith("compare:bucket:"):
        name = target[len("compare:bucket:"):]
        if name not in router._cfg.tiers:
            raise KeyError(f"There is no bucket named {name!r}.")
        seen = set()
        pins = []
        for mc in router._cfg.tiers[name]:
            ident = f"{mc.provider}/{mc.model}"
            if ident not in seen:
                seen.add(ident)
                pins.append(ident)
    elif target.startswith("compare:provider:"):
        name = target[len("compare:provider:"):]
        pins = [ident for ident in all_models if ident.split("/", 1)[0] == name]
        if not pins:
            raise KeyError(f"There is no provider named {name!r} with any models.")
    else:
        raise KeyError(f"Unknown compare target {target!r}.")
    if not pins:
        raise KeyError("Nothing to compare: that group has no models.")
    return pins


@pages.post("/playground/chat", include_in_schema=False)
async def playground_chat(request: Request):
    """Stream one Playground turn through the same in-process path /v1 uses.

    Not behind the app password: the dashboard is local and open by design
    (ADR 0009), and a browser has no way to hold that password anyway.
    After the model's stream ends, one `event: flexrouter` says who answered,
    read from the trace the router just wrote. A `compare:...` target runs
    every matching model concurrently and merges their streams into one
    response; the client tells them apart by each chunk's own `model` field,
    and by `event: targets` sent up front.
    """
    from fastapi.responses import StreamingResponse

    from flexrouter.app import _best_bucket, _stream_chat
    from flexrouter.wire import parse_model, resolve

    body = await request.json()
    messages = [m for m in (body.get("messages") or [])
                if isinstance(m, dict) and m.get("role") in ("system", "user", "assistant")
                and isinstance(m.get("content"), str)]
    if not messages:
        return Response(json.dumps({"error": "send at least one message"}), status_code=400,
                        media_type="application/json")
    router = _live_router()
    target = str(body.get("target") or "auto")
    compare_pins: list[str] | None = None
    tier = ""
    try:
        if target.startswith("compare:"):
            compare_pins = _compare_targets(router, target)
        else:
            tier = resolve(parse_model(target), list(router._cfg.tiers.keys()), _best_bucket(router))
    except KeyError as exc:
        return Response(json.dumps({"error": str(exc.args[0])}), status_code=404,
                        media_type="application/json")
    kwargs = {}
    try:
        if body.get("temperature") not in (None, ""):
            kwargs["temperature"] = max(0.0, min(float(body["temperature"]), 2.0))
        if body.get("max_tokens") not in (None, ""):
            kwargs["max_tokens"] = max(1, min(int(body["max_tokens"]), 32768))
        if body.get("top_p") not in (None, ""):
            kwargs["top_p"] = max(0.0, min(float(body["top_p"]), 1.0))
        # A penalty at 0 is every provider's default, and Gemini rejects the
        # whole request if the field is present at all, so only send one
        # somebody actually moved.
        for name in ("frequency_penalty", "presence_penalty"):
            if body.get(name) not in (None, ""):
                value = max(-2.0, min(float(body[name]), 2.0))
                if value:
                    kwargs[name] = value
    except (TypeError, ValueError):
        return Response(json.dumps({"error": "settings must be numbers"}),
                        status_code=400, media_type="application/json")
    stop = [s.strip() for s in str(body.get("stop") or "").split(",") if s.strip()]
    if stop:
        kwargs["stop"] = stop

    async def one_target_trace(pin: str) -> dict:
        for r in facts.recent_requests(router, limit=200):
            if r.bucket == pin:
                return {"target": pin, "id": r.id, "ok": r.ok, "outcome": r.outcome,
                        "ms": r.ms_total, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
                        "answered_by": (f"{r.answered_by['provider']}/{r.answered_by['model']}"
                                        if r.answered_by else None)}
        return {"target": pin, "ok": False, "outcome": "failed", "answered_by": None}

    async def stream_single():
        async for piece in _stream_chat(router, messages, tier, target, kwargs):
            yield piece
        last = facts.recent_requests(router, limit=1)
        if last:
            r = last[0]
            info = {"id": r.id, "ok": r.ok, "outcome": r.outcome, "ms": r.ms_total,
                    "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
                    "answered_by": (f"{r.answered_by['provider']}/{r.answered_by['model']}"
                                    if r.answered_by else None)}
            yield f"event: flexrouter\ndata: {json.dumps(info)}\n\n"

    async def stream_compare(pins: list[str]):
        yield f"event: targets\ndata: {json.dumps({'targets': pins})}\n\n"
        queue: asyncio.Queue = asyncio.Queue()

        async def run_one(pin: str) -> None:
            async for piece in _stream_chat(router, messages, pin, pin, kwargs):
                await queue.put(piece)
            info = await one_target_trace(pin)
            await queue.put(f"event: flexrouter\ndata: {json.dumps(info)}\n\n")
            await queue.put(None)

        tasks = [asyncio.create_task(run_one(pin)) for pin in pins]
        remaining = len(tasks)
        try:
            while remaining:
                piece = await queue.get()
                if piece is None:
                    remaining -= 1
                    continue
                yield piece
        finally:
            for t in tasks:
                t.cancel()

    stream = stream_compare(compare_pins) if compare_pins is not None else stream_single()

    return StreamingResponse(stream, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@pages.get("/palette.json", include_in_schema=False)
def palette_index() -> Response:
    """Everything the Ctrl+K command bar can jump to."""
    from flexrouter.dashboard import palette
    return Response(json.dumps(palette.items(_live_router())), media_type="application/json")


@pages.get("/", response_class=HTMLResponse, include_in_schema=False)
def overview_page(range: str = DEFAULT_RANGE, fragment: str = "") -> HTMLResponse:
    """The Overview, or - with `fragment=1` - only the part that changes.

    The fragment is what the page polls for. It is the same HTML built by
    the same code as the full page, so the two can never drift; there is no
    second rendering path and no JSON shape to keep in step.
    """
    router = _live_router()
    key = _range(range)[0]
    if fragment:
        return HTMLResponse(overview.inner(router, key))
    return HTMLResponse(page("Overview", "overview", overview.body(router, key)))


@pages.get("/broken", response_class=HTMLResponse, include_in_schema=False)
def broken_page(fragment: str = "") -> HTMLResponse:
    router = _live_router()
    if fragment:
        return HTMLResponse(broken_page_mod.inner(router))
    return HTMLResponse(page("What's broken", "broken", broken_page_mod.body(router)))


def toast_trigger(message: str, kind: str = "ok") -> dict[str, str]:
    """Response headers that make htmx raise a `toast` event on the page,
    which app.js shows in the corner."""
    return {"HX-Trigger": json.dumps({"toast": {"message": message, "kind": kind}})}


def _message_banner(ok: str, message: str) -> str:
    """The outcome a redirect carried back in its querystring.

    Success becomes a toast (app.js reads the hidden seed and shows it).
    Failure stays on the page as well: an error that fades after four
    seconds, while the owner is looking somewhere else, is worse than none.
    """
    if not message:
        return ""
    if ok == "1":
        return tag("div", esc(message), cls="toast-seed", hidden=True,
                   **{"data-kind": "ok"})
    return (tag("div", esc(message), cls="toast-seed", hidden=True, **{"data-kind": "bad"})
            + tag("p", esc(message), cls="state-bad"))


@pages.get("/models_catalog", response_class=HTMLResponse, include_in_schema=False)
def models_page(ok: str = "", message: str = "") -> HTMLResponse:
    banner = _message_banner(ok, message)
    return HTMLResponse(page("Models", "models", _models_body(_live_router(), banner)))


@pages.post("/models_catalog/pending/{provider}/{kind}/{model:path}", include_in_schema=False)
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
        return _redirect_with_message("/models_catalog", ok=False, message=str(e))
    return _redirect_with_message("/models_catalog", ok=True, message=f"{provider}/{model} {action}ed")


@pages.get("/models_catalog/rank", response_class=HTMLResponse, include_in_schema=False)
def models_rank_page(notes: str = "") -> HTMLResponse:
    rows = facts.models(_live_router())
    prompt = ranking.build_prompt(rows, notes)
    return HTMLResponse(page("Rank models", "models", _rank_body(prompt, notes)))


@pages.post("/models_catalog/rank/proposal", response_class=HTMLResponse, include_in_schema=False)
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


@pages.post("/models_catalog/rank/apply", include_in_schema=False)
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
    return _redirect_with_message("/models_catalog", ok=bool(applied), message=message)


@pages.get("/models_catalog/rate-limits", response_class=HTMLResponse, include_in_schema=False)
def models_rate_limits_page() -> HTMLResponse:
    return HTMLResponse(page("Rate limits with an AI", "models", _rate_limits_body("", "")))


@pages.post("/models_catalog/rate-limits/build", response_class=HTMLResponse, include_in_schema=False)
async def models_rate_limits_build(request: Request) -> HTMLResponse:
    form = await request.form()
    docs = form.get("docs") or ""
    idents = [f"{r.provider}/{r.model}" for r in facts.models(_live_router())]
    prompt = ranking.build_rate_limit_prompt(idents, docs) if docs.strip() else ""
    return HTMLResponse(page("Rate limits with an AI", "models", _rate_limits_body(prompt, docs)))


@pages.post("/models_catalog/rate-limits/proposal", response_class=HTMLResponse, include_in_schema=False)
async def models_rate_limits_proposal(request: Request) -> HTMLResponse:
    form = await request.form()
    answer = form.get("answer") or ""
    router = _live_router()
    rows = facts.models(router)
    by_ident = {f"{r.provider}/{r.model}": r for r in rows}
    parsed = ranking.parse_rate_limit_answer(answer, set(by_ident))

    state_dir = router._cfg.state_dir
    skipped_manual = []
    changes = []
    for ident, (proposed_rpm, proposed_tpm) in parsed.items():
        provider, _, model = ident.partition("/")
        if rate_limit_facts.is_manual(state_dir, provider, model):
            skipped_manual.append(ident)
            continue
        row = by_ident[ident]
        rpm = proposed_rpm if proposed_rpm is not None else row.rpm
        tpm = proposed_tpm if proposed_tpm is not None else row.tpm
        if rpm == row.rpm and tpm == row.tpm:
            continue
        changes.append((row, rpm, tpm))

    return HTMLResponse(page("Rate limits with an AI", "models",
                             _rate_limits_proposal_body(changes, skipped_manual)))


@pages.post("/models_catalog/rate-limits/apply", include_in_schema=False)
async def models_rate_limits_apply(request: Request) -> RedirectResponse:
    form = await request.form()
    router = _live_router()
    state_dir = router._cfg.state_dir
    applied = []
    for key in form.keys():
        if not key.startswith("apply:"):
            continue
        ident = key[len("apply:"):]
        rpm_raw = form.get(f"rpm:{ident}")
        tpm_raw = form.get(f"tpm:{ident}")
        if rpm_raw is None or tpm_raw is None:
            continue
        provider, _, model = ident.partition("/")
        try:
            settings_write.set_model_fields(
                provider, model, {"rpm": int(rpm_raw), "tpm": int(tpm_raw)})
        except ValueError:
            continue
        rate_limit_facts.record(state_dir, provider, model, "ai-doc")
        applied.append(ident)

    message = f"{len(applied)} rate limit(s) applied" if applied else "nothing was checked"
    return _redirect_with_message("/models_catalog", ok=bool(applied), message=message)


@pages.post("/models_catalog/import-all", include_in_schema=False)
async def models_import_all(request: Request) -> RedirectResponse:
    """Check every provider with a valid key and add everything it lists.

    Runs the same catalogue check "Check for new models now" does, then
    immediately accepts every resulting `appeared` entry into one bucket -
    the one-at-a-time review in the Pending tray still happens for anyone
    who wants it, this is the "just import all of it" shortcut for a first
    run or a provider that just shipped a dozen new models at once.

    Registered before `/models_catalog/{ident:path}` below on purpose: that catch-all
    would otherwise swallow this POST first (route matching is registration
    order, not specificity) and read "import-all" as a provider name.
    """
    from flexrouter.dashboard.api import run_refresh
    form = await request.form()
    bucket = (form.get("bucket") or "").strip()
    if not bucket:
        return _redirect_with_message("/models_catalog", ok=False, message="Pick a bucket to import into")
    router = _live_router()
    if not router._cfg.experimental_model_discovery:
        return _redirect_with_message(
            "/models_catalog", ok=False,
            message="Model discovery is off. Turn on experimental_model_discovery "
                    "in Settings to use it.")
    state_dir = router._cfg.state_dir
    try:
        await asyncio.to_thread(run_refresh, state_dir)
    except Exception as e:  # noqa: BLE001 - report, don't 500
        return _redirect_with_message("/models_catalog", ok=False, message=f"Discovery failed: {e}")
    count = pending_actions.accept_all_appeared(state_dir, bucket)
    message = (f"Checked every provider with a valid key and imported {count} new "
              f"model(s) into {bucket!r}" if count else
              "Checked every provider with a valid key - nothing new to import")
    return _redirect_with_message("/models_catalog", ok=True, message=message)


def _opt_int(raw) -> Optional[int]:
    """`context`/`rpm`/`tpm`/`score` from the review form: blank or "none"
    (any case) means "not known", same as add_models.parse_answer treats
    the pasted answer. Raises ValueError for anything else unparseable, so
    a bad hand-edit is reported per-row rather than silently ignored."""
    s = (raw or "").strip()
    if not s or s.lower() == "none":
        return None
    return int(s)


def _opt_float(raw) -> Optional[float]:
    """Like `_opt_int`, for `gen_tps` - the one review-form field that's
    legitimately fractional."""
    s = (raw or "").strip()
    if not s or s.lower() == "none":
        return None
    return float(s)


@pages.get("/models_catalog/add-with-ai", response_class=HTMLResponse, include_in_schema=False)
def models_add_with_ai_page(provider: str = "", notes: str = "") -> HTMLResponse:
    router = _live_router()
    providers = add_models.providers_with_keys(router)
    prompt = ""
    error = ""
    if provider:
        if provider not in providers:
            error = f"{provider!r} has no key configured here, so there is nothing to ask about."
        else:
            existing = sorted({r.model for r in facts.models(router) if r.provider == provider})
            prompt = add_models.build_prompt(provider, existing, notes)
    return HTMLResponse(page("Add models with AI", "models",
                             _add_with_ai_body(providers, provider, notes, prompt, error)))


@pages.post("/models_catalog/add-with-ai/review", response_class=HTMLResponse, include_in_schema=False)
async def models_add_with_ai_review(request: Request) -> HTMLResponse:
    form = await request.form()
    answer = form.get("answer") or ""
    router = _live_router()
    state_dir = router._cfg.state_dir
    bucket_names = list(router._cfg.tiers)

    result = add_models.parse_answer(answer)
    existing_chat = {f"{r.provider}/{r.model}": r for r in facts.models(router)}

    rows_info = []
    for i, row in enumerate(result.rows):
        ident = f"{row.provider}/{row.model}"
        if row.kind == "chat":
            old = existing_chat.get(ident)
        else:
            old = parked_models.get(state_dir, row.provider, row.model)
        rows_info.append((i, row, old is not None, old))

    return HTMLResponse(page("Add models with AI", "models",
                             _add_with_ai_review_body(rows_info, result.issues, bucket_names)))


@pages.post("/models_catalog/add-with-ai/apply", include_in_schema=False)
async def models_add_with_ai_apply(request: Request) -> RedirectResponse:
    form = await request.form()
    router = _live_router()
    state_dir = router._cfg.state_dir
    try:
        count = int(form.get("row_count") or 0)
    except ValueError:
        count = 0

    existing_chat = {f"{r.provider}/{r.model}" for r in facts.models(router)}

    added = updated = parked = 0
    errors: list[str] = []
    for i in range(count):
        if not form.get(f"apply:{i}"):
            continue
        provider = (form.get(f"provider:{i}") or "").strip()
        model = (form.get(f"model:{i}") or "").strip()
        kind = (form.get(f"kind:{i}") or "").strip().lower()
        ident = f"{provider}/{model}"
        try:
            if not provider or not model:
                raise ValueError("provider and model are required")
            if kind not in add_models.KINDS:
                raise ValueError(f"unknown kind {kind!r}")
            context = _opt_int(form.get(f"context:{i}"))
            rpm = _opt_int(form.get(f"rpm:{i}"))
            tpm = _opt_int(form.get(f"tpm:{i}"))
            rph = _opt_int(form.get(f"rph:{i}"))
            rpd = _opt_int(form.get(f"rpd:{i}"))
            # rps/tps convert from a day/month allowance into a per-second
            # one and are legitimately fractional (e.g. Mistral's 2.08) -
            # the bug this fixes rejected the AI's own correct answer as
            # "not a number or null" the moment it reached this second,
            # editable-form parse (add_models.py's parse_answer already
            # accepts a float for these two; this handler hadn't).
            rps = _opt_float(form.get(f"rps:{i}"))
            tph = _opt_int(form.get(f"tph:{i}"))
            tpd = _opt_int(form.get(f"tpd:{i}"))
            tps = _opt_float(form.get(f"tps:{i}"))
            gen_tps = _opt_float(form.get(f"gen_tps:{i}"))
            score = _opt_int(form.get(f"score:{i}"))
        except ValueError as e:
            errors.append(f"{ident}: {e}")
            continue
        vision = bool(form.get(f"vision:{i}"))
        free = bool(form.get(f"free:{i}"))
        quotas = {
            k: v for k, v in
            {"rph": rph, "rpd": rpd, "rps": rps, "tph": tph, "tpd": tpd, "tps": tps}.items()
            if v is not None
        }

        if kind == "chat":
            if ident in existing_chat:
                fields: dict = {"vision": vision}
                if score is not None:
                    fields["score"] = score
                if rpm is not None:
                    fields["rpm"] = rpm
                if tpm is not None:
                    fields["tpm"] = tpm
                if context is not None:
                    fields["context_window"] = context
                if quotas:
                    fields["quotas"] = quotas
                if gen_tps is not None:
                    fields["tokens_per_second"] = gen_tps
                if free:
                    fields["price_in"] = 0
                    fields["price_out"] = 0
                try:
                    settings_write.set_model_fields(provider, model, fields)
                except ValueError as e:
                    errors.append(f"{ident}: {e}")
                    continue
                updated += 1
            else:
                bucket = (form.get(f"bucket:{i}") or "").strip()
                if not bucket:
                    errors.append(f"{ident}: choose a bucket")
                    continue
                new_fields: dict = {
                    "provider": provider, "model": model,
                    "score": score if score is not None else router._cfg.unscored_fallback_score,
                    "rpm": rpm if rpm is not None else 60,
                    "tpm": tpm if tpm is not None else 60000,
                    "vision": vision,
                }
                if context is not None:
                    new_fields["context_window"] = context
                if quotas:
                    new_fields["quotas"] = quotas
                if gen_tps is not None:
                    new_fields["tokens_per_second"] = gen_tps
                if free:
                    new_fields["price_in"] = 0
                    new_fields["price_out"] = 0
                try:
                    ov.add_model(bucket, new_fields)
                except ValueError as e:
                    errors.append(f"{ident}: {e}")
                    continue
                added += 1
            if score is not None:
                score_facts.record(state_dir, provider, model, "ai_paste")
            if rpm is not None or tpm is not None:
                rate_limit_facts.record(state_dir, provider, model, "ai_paste")
        else:
            # Never bucketed - a non-chat model has nowhere the engine could
            # ever pick it from (flexrouter/parked_models.py).
            entry = {
                "kind": kind, "context": context, "rpm": rpm, "tpm": tpm,
                "quotas": quotas,
                "vision": vision, "free": free, "score": score,
                "price_in": 0 if free else None, "price_out": 0 if free else None,
                "source": "ai_paste",
            }
            was_update = parked_models.add(state_dir, provider, model, entry)
            if was_update:
                updated += 1
            else:
                parked += 1

    bits = []
    if added:
        bits.append(f"{added} added")
    if updated:
        bits.append(f"{updated} updated")
    if parked:
        bits.append(f"{parked} saved (not routable yet)")
    if errors:
        bits.append(f"{len(errors)} skipped - {'; '.join(errors)}")
    message = ", ".join(bits) if bits else "nothing was checked"
    return _redirect_with_message("/models_catalog", ok=bool(added or updated or parked), message=message)


@pages.post("/models_catalog/{ident:path}", include_in_schema=False)
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
            tokens_per_second = form.get("tokens_per_second")
            if tokens_per_second not in (None, ""):
                fields["tokens_per_second"] = float(tokens_per_second)
            # Every quota field left blank means "no cap on that one" - the
            # whole dict is replaced, not merged, same as every other field
            # here, so clearing a field in the form actually clears it.
            fields["quotas"] = _parse_quota_fields(form)
            fields["vision"] = "vision" in form
            settings_write.set_model_fields(provider, model, fields)
            state_dir = _live_router()._cfg.state_dir
            if "score" in fields:
                score_facts.record(state_dir, provider, model, "manual")
            if "rpm" in fields or "tpm" in fields:
                rate_limit_facts.record(state_dir, provider, model, "manual")
        else:
            raise ValueError(f"unknown action {action!r}")
    except ValueError as e:
        return _redirect_with_message("/models_catalog", ok=False, message=str(e))
    return _redirect_with_message("/models_catalog", ok=True, message=f"{ident} updated")


@pages.get("/requests", response_class=HTMLResponse, include_in_schema=False)
def requests_page(result: str = "", bucket: str = "", provider: str = "", q: str = "",
                  limit: int = requests_page_mod.PAGE, id: str = "",
                  fragment: str = "") -> HTMLResponse:
    """The request log. `fragment=1` is the live block alone (what it polls
    for); `id=` opens that request's journey in the side panel."""
    router = _live_router()
    params = {"result": result if result in {"ok", "failover", "failed"} else "",
              "bucket": bucket, "provider": provider, "q": q,
              "limit": max(requests_page_mod.PAGE, min(int(limit or 0), 5000))}
    if fragment:
        return HTMLResponse(requests_page_mod.log(router, params))
    sheet = ""
    if id:
        journey = facts.request_journey(router, id)
        if journey:
            sheet = requests_page_mod.journey_panel(journey)
    return HTMLResponse(page("Requests", "requests",
                             requests_page_mod.body(router, params), sheet=sheet))


@pages.get("/requests/{request_id}/journey", response_class=HTMLResponse,
           include_in_schema=False)
def request_journey(request_id: str) -> HTMLResponse:
    """Just the side panel, for htmx to drop into the page."""
    journey = facts.request_journey(_live_router(), request_id)
    if journey is None:
        return HTMLResponse(ui.empty("No request with that id on record."), status_code=404)
    return HTMLResponse(requests_page_mod.journey_panel(journey))


@pages.get("/logs", response_class=HTMLResponse, include_in_schema=False)
def logs_page(fragment: str = "", limit: int = logs_page_mod.PAGE) -> HTMLResponse:
    """The server's activity log. Only here when started with --log:
    without it the route answers 404 and the menu shows no link."""
    if not log_setup.enabled():
        return HTMLResponse(
            ui.empty("The server was started without logging. Restart it with "
                     "--log to get an activity log and this page."),
            status_code=404)
    limit = max(10, min(int(limit or 0), 2000))
    if fragment:
        return HTMLResponse(logs_page_mod.rows(limit))
    return HTMLResponse(page("Logs", "logs", logs_page_mod.body(limit)))


@pages.get("/brain", response_class=HTMLResponse, include_in_schema=False)
def brain_page(ok: str = "", message: str = "") -> HTMLResponse:
    return HTMLResponse(page("Error brain", "brain",
                             brain_page_mod.body(_live_router(), _message_banner(ok, message))))


@pages.post("/brain/{fp}/verdict", include_in_schema=False)
async def brain_correct(fp: str, request: Request) -> RedirectResponse:
    """Correct what one learned error means. Stored as the owner's own
    answer, which nothing afterwards overrules."""
    form = await request.form()
    verdict = str(form.get("verdict", ""))
    try:
        _live_router()._error_brain.correct(fp, verdict)
    except KeyError:
        return _redirect_with_message("/brain", False, "That error is no longer on record.")
    except ValueError as exc:
        return _redirect_with_message("/brain", False, str(exc))
    label = brain_page_mod.LABELS.get(verdict, verdict)
    return _redirect_with_message("/brain", True, f"Saved: that error now means \"{label}\".")


@pages.get("/allowance", response_class=HTMLResponse, include_in_schema=False)
def allowance_page(fragment: str = "") -> HTMLResponse:
    router = _live_router()
    if fragment:
        return HTMLResponse(allowance_page_mod.inner(router))
    return HTMLResponse(page("Allowance", "allowance", allowance_page_mod.body(router)))


@pages.get("/buckets", response_class=HTMLResponse, include_in_schema=False)
def buckets_page(ok: str = "", message: str = "") -> HTMLResponse:
    banner = _message_banner(ok, message)
    from flexrouter.dashboard import buckets_page
    return HTMLResponse(page("Buckets", "buckets", buckets_page.body(
        _live_router(), banner, _add_model_form, _add_bucket_form())))


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
        tokens_per_second = form.get("tokens_per_second")
        if tokens_per_second:
            fields["tokens_per_second"] = float(tokens_per_second)
        quotas = _parse_quota_fields(form)
        if quotas:
            fields["quotas"] = quotas
        if "vision" in form:
            fields["vision"] = True
        ov.add_model(bucket, fields)
    except (ValueError, TypeError) as e:
        return _redirect_with_message("/buckets", ok=False, message=str(e))
    return _redirect_with_message(
        "/buckets", ok=True, message=f"{fields['provider']}/{fields['model']} added")


@pages.post("/buckets/{bucket}/strategy", include_in_schema=False)
async def buckets_set_strategy(bucket: str, request: Request) -> RedirectResponse:
    form = await request.form()
    strategy = (form.get("strategy") or "").strip()
    try:
        settings_write.set_bucket_strategy(bucket, strategy)
    except ValueError as e:
        return _redirect_with_message("/buckets", ok=False, message=str(e))
    return _redirect_with_message(
        "/buckets", ok=True, message=f"{bucket!r} now ranks by {strategy}")


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


@pages.post("/providers/add", include_in_schema=False)
async def provider_add_from_preset(request: Request) -> RedirectResponse:
    router = _live_router()
    form = await request.form()
    name = (form.get("name") or "").strip()
    preset = presets.get(name)
    if preset is None:
        return _redirect_with_message("/providers", ok=False,
                                      message=f"unknown preset {name!r}")
    dest = f"/providers/{quote(name, safe=':')}"
    try:
        ov.add_provider(name, {"base_url": preset.base_url,
                               "header_parser": preset.header_parser})
    except ValueError as e:
        return _redirect_with_message("/providers", ok=False, message=str(e))

    # The provider now genuinely exists. A key failure past this point must
    # not read as "nothing happened" - the same reasoning as providers_add.
    secret = (form.get("secret") or "").strip()
    if secret:
        try:
            keystore.add_key(name, secret, (form.get("label") or "").strip())
        except (ValueError, OSError) as e:
            return _redirect_with_message(
                dest, ok=False, message=f"{name} added, but its key failed: {e}")
    if router._cfg.experimental_model_discovery and preset.models_path and secret:
        return RedirectResponse(url=f"{dest}/discover", status_code=303)
    return _redirect_with_message(dest, ok=True, message=f"{name} added")


@pages.get("/providers/add/{name}", response_class=HTMLResponse, include_in_schema=False)
def provider_add_page(name: str, ok: str = "", message: str = "",
                      panel: str = "") -> HTMLResponse:
    """Adding a provider from a preset. `panel=1` returns just the side
    panel for the preset grid to slide in; without it, the same form as a
    whole page, which is what a refresh or a no-JavaScript click gets."""
    preset = presets.get(name)
    if preset is None and panel:
        return HTMLResponse(ui.empty("No such preset."), status_code=404)
    if preset is not None and panel:
        return HTMLResponse(ui.sheet(f"Add {preset.label}", _preset_connect(preset),
                                     close_href="/providers", sub=preset.base_url))
    if preset is None:
        return HTMLResponse(
            page("Providers & keys", "providers",
                 tag("h1", "No such preset", cls="page-title") + tag("p", esc(name))),
            status_code=404,
        )
    return HTMLResponse(
        page(f"Add {preset.label} - Providers & keys", "providers",
             _preset_add_body(preset, _message_banner(ok, message))))


def _preset_add_body(p, banner: str = "") -> str:
    return (
        tag("div", tag("h1", esc(p.label), cls="page-title"), cls="page-head")
        + banner
        + tag("p", esc(p.base_url), cls="lede")
        + tag("div", _panel("Connect it", tag("div", _preset_connect(p), cls="pb")),
              cls="panel-page")
    )


def _preset_connect(p) -> str:
    """Where to get a key, what happens next, and the form - shared by the
    side panel and the full page."""
    where = (
        tag("p", "Get a key: " + tag("a", esc(p.signup_url), href=esc(p.signup_url)))
        if p.signup_url else ""
    )
    discovery = (
        "Paste the key and flexrouter will ask this provider which models "
        "it can reach, then let you pick which to import."
        if p.models_path else
        "This provider has no model list flexrouter knows how to read, so "
        "you will add its models by hand afterwards."
    )
    form = tag(
        "form",
        _input(type="hidden", name="name", value=esc(p.name))
        + _field("Key", _input(type="password", name="secret",
                               placeholder="paste it here"))
        + _field("Label", _input(type="text", name="label",
                                 placeholder="which account this is"))
        + tag("button", "Add and look for models", type="submit"),
        method="post", action="/providers/add", cls="grouped-form",
        **{"data-busy": "Checking the key and asking for models..."},
    )
    return where + tag("p", esc(discovery), cls="note") + form


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
        tokens_per_second = form.get("tokens_per_second")
        if tokens_per_second:
            fields["tokens_per_second"] = float(tokens_per_second)
        quotas = _parse_quota_fields(form)
        if quotas:
            fields["quotas"] = quotas
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
                 tag("h1", "No such provider", cls="page-title") + tag("p", esc(provider))),
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
             _provider_detail_body(detail, editable, bucket_names, banner,
                                   model_reset.preview(router._cfg.state_dir,
                                                       provider=provider)))
    )


@pages.get("/providers/{provider}/discover", response_class=HTMLResponse,
           include_in_schema=False)
async def provider_discover(provider: str) -> HTMLResponse:
    router = _live_router()
    preset = presets.get(provider)
    pcfg = router._cfg.providers.get(provider)
    if pcfg is None:
        return HTMLResponse(
            page("Providers & keys", "providers",
                 tag("h1", "No such provider", cls="page-title") + tag("p", esc(provider))),
            status_code=404)
    if not router._cfg.experimental_model_discovery:
        # No network call to the provider: discovery is experimental and off
        # by default (see FlexConfig.experimental_model_discovery).
        return HTMLResponse(
            page(f"{provider} - Providers & keys", "providers",
                 tag("h1", esc(provider), cls="page-title")
                 + tag("p", "Model discovery is off. Turn on Experimental: automatic "
                            "model discovery (experimental_model_discovery) in Settings "
                            "to ask this provider what it offers.", cls="note")
                 + tag("p", tag("a", "Back to the provider",
                                href=f"/providers/{quote(provider, safe=':')}",
                                cls="button-link"))))
    result = await probe_key(
        pcfg.base_url,
        pcfg.api_keys[0] if pcfg.api_keys else None,
        timeout=router._cfg.probe_timeout_seconds,
        models_path=(preset.models_path if preset else "/models") or "/models",
    )
    return HTMLResponse(
        page(f"{provider} - Providers & keys", "providers",
             _discovered_body(provider, preset, result, list(router._cfg.tiers))))


def _discovered_body(provider: str, preset, result, bucket_names: list) -> str:
    head = tag("div", tag("h1", esc(provider), cls="page-title"), cls="page-head")
    if not result.ok:
        return (head
                + tag("p", esc(result.error or "the provider did not answer"),
                      cls="state-bad")
                + tag("p", tag("a", "Back to the provider",
                               href=f"/providers/{quote(provider, safe=':')}",
                               cls="button-link")))
    if not result.models:
        return (head + tag("p", "This key works, but the provider lists no "
                               "models it can reach.", cls="note"))

    seed_rpm = preset.seed_rpm if preset else 30
    seed_tpm = preset.seed_tpm if preset else 60_000
    options = "".join(tag("option", esc(b), value=esc(b)) for b in bucket_names)
    rows = []
    for model_id in result.models:
        picker = f"<select{attrs({'name': 'bucket'})}>{options}</select>"
        form = tag(
            "form",
            _input(type="hidden", name="model", value=esc(model_id))
            + _input(type="hidden", name="score", value="50")
            + _input(type="hidden", name="rpm", value=esc(seed_rpm))
            + _input(type="hidden", name="tpm", value=esc(seed_tpm))
            + picker
            + tag("button", "Import", type="submit"),
            method="post",
            action=f"/providers/{quote(provider, safe=':')}/models",
        )
        rows.append(tag("tr", tag("td", esc(model_id)) + tag("td", form)))
    table = tag("div", tag("table",
        tag("tr", tag("th", "Model") + tag("th", "Import into")) + "".join(rows),
        cls="matrix"), cls="scroll")
    return (
        head
        + tag("p", esc(f"{len(result.models)} models reachable with this key, "
                       f"answered in {result.latency_ms} ms"), cls="lede")
        + tag("div", _panel(
            "What this key can reach", table,
            sub=f"seeded at {seed_rpm} rpm / {seed_tpm} tpm - correct them "
                f"afterwards on the Models page"), cls="panel-page"))


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


def _reset_models(provider: str | None, dest: str) -> RedirectResponse:
    router = _live_router()
    result = model_reset.reset(router._cfg.state_dir, provider=provider)
    model_reset.forget_live(router, provider)
    router.reload()
    c = result.counts
    message = (f"deleted {c['models']:,} model(s), {c['field_edits']:,} field edit(s) and "
               f"{c['facts']:,} learned fact(s)"
               + (f"; backup in {result.backup_dir}" if result.backup_dir else ""))
    return _redirect_with_message(dest, ok=True, message=message)


@pages.post("/providers/reset-all", include_in_schema=False)
async def providers_reset_all(request: Request) -> RedirectResponse:
    form = await request.form()
    if (form.get("confirm") or "").strip() != "reset all":
        return _redirect_with_message("/providers", ok=False,
                                      message="Nothing reset: type reset all to confirm")
    return _reset_models(None, "/providers")


@pages.post("/providers/{provider}/reset", include_in_schema=False)
async def provider_reset(provider: str, request: Request) -> RedirectResponse:
    form = await request.form()
    dest = f"/providers/{quote(provider, safe=':')}"
    if (form.get("confirm") or "").strip() != provider:
        return _redirect_with_message(dest, ok=False,
                                      message=f"Nothing reset: type {provider} to confirm")
    return _reset_models(provider, dest)


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


def _globs(raw: str) -> list[str]:
    """Split an `allow_models` box into patterns.

    Commas and whitespace both separate, because the owner will use
    whichever they think of first and neither one is wrong.
    """
    parts = [p.strip() for p in raw.replace(",", " ").split()]
    return [p for p in parts if p]


@pages.post("/providers/{provider}/keys", include_in_schema=False)
async def provider_key_add(provider: str, request: Request) -> RedirectResponse:
    form = await request.form()
    dest = f"/providers/{quote(provider, safe=':')}"
    secret = (form.get("secret") or "").strip()
    if not secret:
        return _redirect_with_message(dest, ok=False, message="paste a key first")
    try:
        keystore.add_key(provider, secret, (form.get("label") or "").strip())
    except (ValueError, OSError) as e:
        return _redirect_with_message(dest, ok=False, message=str(e))
    return _redirect_with_message(dest, ok=True, message="key added")


@pages.post("/providers/{provider}/keys/{key_id}/remove", include_in_schema=False)
async def provider_key_remove(provider: str, key_id: str) -> RedirectResponse:
    dest = f"/providers/{quote(provider, safe=':')}"
    removed = keystore.remove_key(provider, key_id)
    return _redirect_with_message(
        dest, ok=removed,
        message="key removed" if removed else "that key is already gone")


def _parse_key_edit_form(form) -> dict:
    """The fields a key's edit form always submits, cast and parsed the
    same way whether the target is a plain save or a save-and-copy."""
    weight = int(form.get("weight") or 1)
    return {
        "label": (form.get("label") or "").strip(),
        "weight": weight,
        "allow_models": _globs(form.get("allow_models") or ""),
        # An unchecked checkbox is simply absent from the form, which is
        # the only way HTML says "false" without JavaScript.  We test
        # truthiness (not key-presence) so that the test client can send
        # an empty string to simulate an unchecked box.
        "enabled": bool(form.get("enabled")),
        "quotas": _parse_quota_fields(form, fields=_KEY_QUOTA_FIELDS),
    }


@pages.post("/providers/{provider}/keys/{key_id}/edit", include_in_schema=False)
async def provider_key_edit(provider: str, key_id: str,
                            request: Request) -> RedirectResponse:
    form = await request.form()
    dest = f"/providers/{quote(provider, safe=':')}"
    try:
        fields = _parse_key_edit_form(form)
    except (TypeError, ValueError):
        return _redirect_with_message(dest, ok=False, message="weight and caps must be numbers")
    updated = keystore.update_key(provider, key_id, **fields)
    if updated is None:
        return _redirect_with_message(dest, ok=False, message="that key is no longer there")
    return _redirect_with_message(dest, ok=True, message="key saved")


@pages.post("/providers/{provider}/keys/{key_id}/copy-to-all", include_in_schema=False)
async def provider_key_copy_to_all(provider: str, key_id: str,
                                   request: Request) -> RedirectResponse:
    """Save this key, then apply its weight, allowed models, caps and
    enabled state to every other key on the same provider - everything an
    account-level credential setting could plausibly need to agree on.
    Never touches label, which is each key's own identity."""
    form = await request.form()
    dest = f"/providers/{quote(provider, safe=':')}"
    try:
        fields = _parse_key_edit_form(form)
    except (TypeError, ValueError):
        return _redirect_with_message(dest, ok=False, message="weight and caps must be numbers")
    updated = keystore.update_key(provider, key_id, **fields)
    if updated is None:
        return _redirect_with_message(dest, ok=False, message="that key is no longer there")
    shared = {k: v for k, v in fields.items() if k != "label"}
    others = [r.id for r in keystore.load_keys().get(provider, []) if r.id != key_id]
    for other_id in others:
        keystore.update_key(provider, other_id, **shared)
    message = (f"key saved and copied to {len(others)} other key(s)"
              if others else "key saved (no other keys on this provider)")
    return _redirect_with_message(dest, ok=True, message=message)


@pages.get("/settings", response_class=HTMLResponse, include_in_schema=False)
def settings_page(ok: str = "", message: str = "") -> HTMLResponse:
    banner = _message_banner(ok, message)
    return HTMLResponse(page("Settings", "settings", settings_page_mod.body(
        _live_router(), banner, _service_keys_section())))


# These fixed addresses are registered before `/settings/{field}` below,
# which would otherwise catch them as a setting called "app-password".

@pages.post("/settings/app-password", response_class=HTMLResponse, include_in_schema=False)
async def settings_app_password() -> HTMLResponse:
    """Make a new random app password and show it exactly once. Nothing in
    the request is read: the password is never typed in (ADR 0015)."""
    secret = app_password.generate()
    body = settings_page_mod.body(_live_router(),
                                  _message_banner("1", "New app password made"),
                                  _service_keys_section(), shown_password=secret)
    return HTMLResponse(page("Settings", "settings", body),
                        headers={"Cache-Control": "no-store"})


@pages.post("/settings/app-password/clear", include_in_schema=False)
async def settings_app_password_clear() -> RedirectResponse:
    app_password.clear()
    return _redirect_with_message("/settings", ok=True, message="Generated app password forgotten")


@pages.post("/settings/dashboard", include_in_schema=False)
async def settings_dashboard(request: Request) -> RedirectResponse:
    form = await request.form()
    current = dashboard_prefs.load()
    try:
        new = dashboard_prefs.Prefs(
            motion=str(form.get("motion", current.motion)),
            refresh_seconds=int(form.get("refresh_seconds", current.refresh_seconds)),
            default_range=str(form.get("default_range", current.default_range)),
            timezone=str(form.get("timezone", current.timezone)).strip() or "local",
        )
        dashboard_prefs.save(new)
    except ValueError as e:
        return _redirect_with_message("/settings", ok=False, message=str(e))
    return _redirect_with_message("/settings", ok=True, message="Dashboard preferences saved")


@pages.get("/settings/backup", include_in_schema=False)
def settings_backup() -> Response:
    """Every dashboard change plus the dashboard's own preferences, as one
    file. Keys are deliberately not included."""
    data = {"flexrouter_backup": 1, "overrides": ov.load_overrides(),
            "dashboard": asdict(dashboard_prefs.load())}
    return Response(json.dumps(data, indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="flexrouter-backup.json"'})


@pages.post("/settings/restore", include_in_schema=False)
async def settings_restore(request: Request) -> RedirectResponse:
    form = await request.form()
    upload = form.get("backup")
    try:
        raw = json.loads((await upload.read()).decode("utf-8")) if upload else None
        if not isinstance(raw, dict) or raw.get("flexrouter_backup") != 1:
            raise ValueError("that file is not a flexrouter backup")
        overrides = raw.get("overrides") or {}
        for section, entries in overrides.items():
            if section == "settings":
                ov.check_fields("settings", entries)
            elif section in ("providers", "models"):
                for fields in (entries or {}).values():
                    ov.check_fields(section, {k: v for k, v in (fields or {}).items()
                                              if k not in ("provider", "model")})
        prefs_in = dashboard_prefs.Prefs(**{k: v for k, v in (raw.get("dashboard") or {}).items()
                                            if k in dashboard_prefs.Prefs.__dataclass_fields__})
        dashboard_prefs.save(prefs_in)
        ov.save_overrides(overrides)
    except (ValueError, TypeError, UnicodeDecodeError) as e:
        return _redirect_with_message("/settings", ok=False, message=f"Not restored: {e}")
    return _redirect_with_message("/settings", ok=True, message="Backup restored")


@pages.post("/settings/reset-all", include_in_schema=False)
async def settings_reset_all() -> RedirectResponse:
    ov.save_overrides({})
    return _redirect_with_message("/settings", ok=True,
                                  message="Every dashboard change undone; your settings file applies")


@pages.post("/settings/refresh-models", include_in_schema=False)
async def settings_refresh_models() -> RedirectResponse:
    import asyncio
    from flexrouter.dashboard.api import run_refresh
    router = _live_router()
    if not router._cfg.experimental_model_discovery:
        return _redirect_with_message(
            "/settings", ok=False,
            message="Model discovery is off. Turn on experimental_model_discovery "
                    "in Settings to use it.")
    try:
        await asyncio.to_thread(run_refresh, router._cfg.state_dir)
    except Exception as e:  # noqa: BLE001 - report, don't 500
        return _redirect_with_message("/settings", ok=False, message=f"Check failed: {e}")
    return _redirect_with_message("/models_catalog", ok=True,
                                  message="Checked every provider; anything new is listed below")


@pages.post("/settings/rescore-models", include_in_schema=False)
async def settings_rescore_models() -> RedirectResponse:
    """Re-score every configured model against Artificial Analysis.

    Uses the saved AA key (service_keys' "aa") and writes new scores to
    overrides.json like every other settings write, so a score can always
    be put back. A model AA's data does not cover keeps the score it has:
    the median placeholder score_with_aa would give it says nothing about
    that model, while the owner's existing number says something.
    """
    from flexrouter.catalogue import _best_score, fetch_aa_lookup
    aa_key = service_keys.resolve("aa")
    if not aa_key:
        return _redirect_with_message(
            "/settings", ok=False,
            message="No Artificial Analysis key - save one under Service keys first")
    router = _live_router()
    try:
        aa_lookup = await fetch_aa_lookup(aa_key)
    except Exception as e:  # noqa: BLE001 - report, don't 500
        return _redirect_with_message("/settings", ok=False,
                                      message=f"Artificial Analysis call failed: {e}")
    state_dir = router._cfg.state_dir
    matched = changed = unmatched = 0
    seen: set[tuple[str, str]] = set()
    for bucket in router._cfg.tiers.values():
        for m in bucket:
            if (m.provider, m.model) in seen:
                continue
            seen.add((m.provider, m.model))
            score = _best_score(m.model, aa_lookup, fallback=-1)
            if score < 0:
                unmatched += 1
                continue
            matched += 1
            if score == m.score:
                continue
            settings_write.set_model_fields(m.provider, m.model, {"score": score})
            score_facts.record(state_dir, m.provider, m.model, "aa")
            changed += 1
    return _redirect_with_message(
        "/settings", ok=True,
        message=f"Artificial Analysis matched {matched} of {len(seen)} models; "
                f"{changed} score(s) updated, {unmatched} kept their old score")


@pages.post("/settings/test-rate-limits", include_in_schema=False)
async def settings_test_rate_limits() -> RedirectResponse:
    """Send one minimal test message to every configured model.

    This adds nothing to how rate limits get learned - `AsyncClient.chat`
    already parses and saves them off every real call (flexrouter/client.py).
    All this does is manufacture one of those calls per model right now,
    pinned the same way `compare:` pins one (`provider/model` as the tier),
    instead of waiting for organic traffic to happen to hit each one.
    A model that fails (no key, quarantined, deleted, out of quota) just
    contributes nothing - it is not reported as broken, only counted.
    """
    router = _live_router()
    store = router._rate_limit_store
    seen: set[tuple[str, str]] = set()
    pins: list[str] = []
    for bucket in router._cfg.tiers.values():
        for m in bucket:
            if (m.provider, m.model) not in seen:
                seen.add((m.provider, m.model))
                pins.append(f"{m.provider}/{m.model}")

    async def probe_one(pin: str) -> str:
        provider, _, model = pin.partition("/")
        try:
            await router.agenerate([{"role": "user", "content": "hi"}], pin,
                                   wait=False, max_tokens=1)
        except (RouterBusy, RouterError):
            return "failed"
        return "learned" if store.has_limits(provider, model) else "silent"

    results = await asyncio.gather(*(probe_one(p) for p in pins))
    failed = sum(1 for r in results if r == "failed")
    silent = [pin for pin, r in zip(pins, results) if r == "silent"]
    message = (f"Sent a test message to {len(pins)} model(s); "
              f"{len(pins) - failed - len(silent)} returned rate-limit numbers, "
              f"{failed} could not be reached right now")
    if silent:
        message += (f". These answered but sent no rate-limit headers at all - "
                    f"their limit isn't learnable this way, read the docs for "
                    f"them instead: {', '.join(silent)}")
    return _redirect_with_message("/settings", ok=True, message=message)


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


