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
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from flexrouter import keys as keystore
from flexrouter import overrides as ov
from flexrouter import presets
from flexrouter.probe import probe_key
from flexrouter import score_facts
from flexrouter import service_keys
from flexrouter.dashboard import (facts, keytest, overview, pending_actions,
                                  ranking, settings_write, ui)
from flexrouter.dashboard import allowance_page as allowance_page_mod
from dataclasses import asdict

from fastapi.responses import Response

from flexrouter import app_password
from flexrouter.dashboard import prefs as dashboard_prefs
from flexrouter.dashboard import settings_page as settings_page_mod
from flexrouter.dashboard import brain_page as brain_page_mod
from flexrouter.dashboard import broken_page as broken_page_mod
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
    )


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
    edit_form = tag(
        "form",
        _field("Label", _input(type="text", name="label", value=esc(k.label)))
        + _field("Weight", _input(type="number", name="weight", value=esc(k.weight), min="1"))
        + _field("Only these models",
                 _input(type="text", name="allow_models",
                        value=esc(" ".join(k.allow_models)),
                        placeholder="* (all)"))
        + tag("label",
              _input(type="checkbox", name="enabled",
                     **({"checked": True} if k.enabled else {}))
              + "enabled", cls="check-field")
        + tag("button", "Save", type="submit"),
        method="post",
        action=f"/providers/{quote(detail.name, safe=':')}/keys/{quote(k.id, safe=':')}/edit",
        cls="grouped-form key-edit",
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
                          banner: str = "") -> str:
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
        + _field("Score", _input(type="number", name="score", value=esc(r.score)))
        + _field("Requests a minute", _input(type="number", name="rpm", value=esc(r.rpm)))
        + _field("Tokens a minute", _input(type="number", name="tpm", value=esc(r.tpm)))
        + _field("Context window", _input(type="number", name="context_window",
                                          value=esc(r.context_window)))
        + tag("label", _input(type="checkbox", name="vision",
                              **{"checked": True} if vision_checked else {})
              + "Can see images", cls="check-field")
        + tag("button", "Save", type="submit"),
        method="post", action=f"/models/{ident}", cls="grouped-form",
    )
    disable = tag(
        "form",
        _input(type="hidden", name="action", value="disable")
        + tag("button", "Disable this model", type="submit", cls="danger"),
        method="post", action=f"/models/{ident}",
    )
    return edit + disable


def _models_body(router, banner: str = "") -> str:
    rows_data = facts.models(router)

    header = tag("tr", "".join(
        tag("th", h, cls=c, **({"data-sort": k} if k else {}))
        for h, c, k in [("Model", "", "model"), ("Buckets", "", ""), ("Score", "", "score"),
                        ("Can do", "", ""), ("State", "", "state"), ("", "", "")]))
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
            tag("td", tag("div", caps, cls="caps")),
            tag("td", ui.status("ok" if ok else "bad")
                + (tag("div", esc(r.why), cls="m-why") if r.why else "")),
            tag("td", tag("button", ui.icon("arrow-right"), type="button", cls="m-open",
                          **{"aria-label": f"Details for {r.model}", "data-toggle": f"m-{n}"})),
        ]), cls="m-row", **{"data-search": search, "data-provider": r.provider,
                            "data-score": r.score, "data-model": r.model,
                            "data-state": r.state, "data-toggle-row": f"m-{n}"}))
        detail = tag("div",
                     tag("div",
                         tag("h4", "Change this model")
                         + _model_edit_form(r)
                         + (tag("p", esc(f"Priced at ${r.price_in}/M in, ${r.price_out}/M out"),
                                cls="note") if r.price_in is not None else ""),
                         cls="m-detail-body"),
                     cls="m-detail-inner")
        rows.append(tag("tr", tag("td", detail, colspan="6"), cls="m-detail", id=f"m-{n}",
                        hidden=True))
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

    n_pending = sum(len(b.get(kind) or []) for b in pending.values() if isinstance(b, dict)
                    for kind in ("appeared", "vanished", "changed"))
    status = f"{len(rows_data)} models in use · {len(disabled)} disabled"
    if n_pending:
        status += f" · {n_pending} catalogue changes waiting"
    head = tag("div",
               tag("div", tag("h1", "Models", cls="page-title")
                   + tag("p", esc(status), cls="page-status"), cls="page-head-text")
               + tag("div", ui.button("Rank models with an AI", href="/models/rank",
                                      icon_name="brain"), cls="page-actions"),
               cls="page-head")
    legend = tag("p", "Tag styles say where a fact came from: "
                 + tag("span", "solid", cls="cap cap-published") + " the provider says so, "
                 + tag("span", "outlined", cls="cap cap-observed") + " seen working, "
                 + tag("span", "faded", cls="cap cap-guessed") + " a guess, "
                 + tag("span", "underlined", cls="cap cap-manual") + " set by you.",
                 cls="note cap-legend")
    return (
        head + banner + filters
        + tag("div", tag("div", table, cls="scroll"), cls="box flush", **{"data-enter": ""})
        + legend
        + ui.box("Disabled models", disabled_section, **{"data-enter": ""})
        + ui.box("Pending catalogue changes",
                 tag("p", "What the last catalogue check found. Accepting a new model adds it "
                          "to the bucket you choose; accepting a vanished one disables it; "
                          "accepting a changed field applies the provider's new value.",
                     cls="note") + _pending_body(pending, bucket_names),
                 cls="tray" + (" has-items" if n_pending else ""),
                 id="pending", **{"data-enter": ""})
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
        tag("h1", "Rank models with an AI", cls="page-title")
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
        tag("h1", "Proposed scores", cls="page-title")
        + tag("p", "Nothing here is applied until you press the button "
                   "below. Uncheck any row you don't want changed.",
              cls="lede")
        + skipped_note
        + body
    )


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
        tag("div", tag("h2", "Service keys"), cls="box-head")
        + tag("div",
              tag("p", "Keys flexrouter uses for its own calls, not for chat: scoring a "
                       "newly found model, and the Error brain's classifier. Masked "
                       "everywhere, never shown in full again.", cls="set-help")
              + tag("table", "".join(rows)), cls="box-body")
    )


@pages.get("/playground", response_class=HTMLResponse, include_in_schema=False)
def playground_page() -> HTMLResponse:
    from flexrouter.dashboard import playground_page as pg
    return HTMLResponse(page("Playground", "playground", pg.body(_live_router())))


@pages.post("/playground/chat", include_in_schema=False)
async def playground_chat(request: Request):
    """Stream one Playground turn through the same in-process path /v1 uses.

    Not behind the app password: the dashboard is local and open by design
    (ADR 0009), and a browser has no way to hold that password anyway.
    After the model's stream ends, one `event: flexrouter` says who answered,
    read from the trace the router just wrote.
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
    try:
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
    except (TypeError, ValueError):
        return Response(json.dumps({"error": "temperature and max tokens must be numbers"}),
                        status_code=400, media_type="application/json")

    async def stream():
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

    return StreamingResponse(stream(), media_type="text/event-stream",
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


@pages.post("/providers/add", include_in_schema=False)
async def provider_add_from_preset(request: Request) -> RedirectResponse:
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
    if preset.models_path and secret:
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
             _provider_detail_body(detail, editable, bucket_names, banner))
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


@pages.post("/providers/{provider}/keys/{key_id}/edit", include_in_schema=False)
async def provider_key_edit(provider: str, key_id: str,
                            request: Request) -> RedirectResponse:
    form = await request.form()
    dest = f"/providers/{quote(provider, safe=':')}"
    try:
        weight = int(form.get("weight") or 1)
    except (TypeError, ValueError):
        return _redirect_with_message(dest, ok=False, message="weight must be a number")
    updated = keystore.update_key(
        provider, key_id,
        label=(form.get("label") or "").strip(),
        weight=weight,
        allow_models=_globs(form.get("allow_models") or ""),
        # An unchecked checkbox is simply absent from the form, which is
        # the only way HTML says "false" without JavaScript.  We test
        # truthiness (not key-presence) so that the test client can send
        # an empty string to simulate an unchecked box.
        enabled=bool(form.get("enabled")),
    )
    if updated is None:
        return _redirect_with_message(dest, ok=False, message="that key is no longer there")
    return _redirect_with_message(dest, ok=True, message="key saved")


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
    try:
        await asyncio.to_thread(run_refresh, _live_router()._cfg.state_dir)
    except Exception as e:  # noqa: BLE001 - report, don't 500
        return _redirect_with_message("/settings", ok=False, message=f"Check failed: {e}")
    return _redirect_with_message("/models", ok=True,
                                  message="Checked every provider; anything new is listed below")


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


