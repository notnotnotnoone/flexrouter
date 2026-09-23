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
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from flexrouter import keys as keystore
from flexrouter import overrides as ov
from flexrouter import presets
from flexrouter.probe import probe_key
from flexrouter import score_facts
from flexrouter import service_keys
from flexrouter.dashboard import (charts, facts, keytest, pending_actions,
                                  ranking, settings_write, stats)
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


# (key, label, hours, bucket hours, label format). `None` hours means "back
# to the first thing ever logged". Buckets get coarser as the range grows:
# a month at one point per hour is 720 points of mush on an 880-wide chart.
RANGES: list[tuple[str, str, object, int, str]] = [
    ("24h", "24 hours", 24, 1, "%H:%M"),
    ("7d", "7 days", 24 * 7, 6, "%a %H:%M"),
    ("30d", "30 days", 24 * 30, 24, "%d %b"),
    ("all", "All", None, 24, "%d %b"),
]
_RANGE_BY_KEY = {r[0]: r for r in RANGES}
DEFAULT_RANGE = "24h"


def _range(key: str):
    """The range `key` names, falling back to 24 hours.

    A querystring is user input: an unknown value shows the default rather
    than raising, the same way a bad page number shows page one.
    """
    return _RANGE_BY_KEY.get(key or "", _RANGE_BY_KEY[DEFAULT_RANGE])


def _range_switcher(current: str) -> str:
    """Real links, one per range. No JavaScript is involved in switching -
    the range lives in the URL, so it survives a reload and can be
    bookmarked."""
    out = []
    for key, label, _h, _b, _f in RANGES:
        here = key == current
        out.append(tag(
            "a", esc(label),
            href="/" if key == DEFAULT_RANGE else f"/?range={key}",
            cls="range-link",
            **{"aria-current": "true" if here else None},
        ))
    return tag("nav", "".join(out), cls="ranges", **{"aria-label": "Time range"})


def _panel(title: str, body: str, *, sub: str = "", action: str = "") -> str:
    """A titled block on the Overview. `body` is already-rendered HTML."""
    head = tag("h2", esc(title))
    if sub:
        head += tag("span", esc(sub), cls="sub")
    if action:
        head += tag("span", "", cls="spacer") + action
    return tag("section", tag("div", head, cls="ph") + body, cls="panel")


def _num(value: object) -> str:
    """A whole number with thousands separators, escaped."""
    try:
        return esc(f"{int(value):,}")
    except (TypeError, ValueError):
        return esc(value)


def _clock(iso: str) -> str:
    """A stored UTC timestamp as a local wall clock.

    Traces are written in UTC and the Overview's chart buckets by local
    hour (`stats.recent_window`). Printing the raw UTC time next to that
    chart would put two different clocks on one screen, and the reader has
    no way to tell which one a row belongs to.
    """
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError):
        return iso or ""
    return dt.strftime("%H:%M:%S")


def _pct(fraction) -> str:
    if fraction is None:
        return "-"
    return f"{fraction * 100:.1f}%".replace(".0%", "%")


def _spend(window: dict) -> tuple[str, str]:
    """What the window cost, and the note that goes under it.

    Three different answers, and they must not be confused:
      - nothing ran                -> no figure at all
      - it ran, nothing was priced -> "not priced", NOT "$0.00"
      - it ran and was priced      -> the money

    Most traffic here is on a free tier, but not all of it, so "$0.00" is a
    claim the dashboard is only allowed to make when somebody has actually
    told it what the models cost.
    """
    if window["requests"] == 0:
        return "-", ""
    if not window["any_priced"]:
        return "not priced", "set prices on Models"
    spent = window["spend_usd"]
    figure = f"${spent:,.2f}" if spent >= 0.01 else f"${spent:,.4f}"
    if window["priced_rows"] < window["requests"]:
        return figure, f"{window['priced_rows']:,} of {window['requests']:,} priced"
    return figure, "all traffic priced"


def _bucket_word(window: dict) -> str:
    """What one block on the chart covers, said in words.

    The strips and the chart share a bucket size, and a strip that claims
    to be hourly when each block is six hours is a quiet lie about when
    something broke.
    """
    n = window["bucket_hours"]
    if n >= 24:
        return "day" if n == 24 else f"{n // 24} days"
    return "hour" if n == 1 else f"{n} hours"


def _chart_series(window: dict) -> list[dict]:
    """The providers the chart draws, folded to the number of colours.

    A seventh provider would reuse a colour and quietly lie about who
    answered, so everything past the sixth busiest becomes one "other"
    band. The table underneath still lists every provider by name.
    """
    provs = window["providers"]
    if len(provs) <= charts.MAX_SERIES:
        return provs
    head = provs[:charts.MAX_SERIES - 1]
    tail = provs[charts.MAX_SERIES - 1:]
    hours = window["hours"]
    return head + [{
        "name": f"{len(tail)} others",
        "values": [sum(p["values"][i] for p in tail) for i in range(hours)],
        "requests": sum(p["requests"] for p in tail),
    }]


def _series_colors(series: list[dict]) -> dict:
    return {s["name"]: charts.color_for(i) for i, s in enumerate(series)}


def _swatch(color: str) -> str:
    return tag("span", "", cls="swatch", style=f"background:{color}")


def _overview_verdict(data: dict, summaries: list, window: dict,
                      label: str) -> str:
    p = data["providers"]
    needs_you = data["needs_you"]

    if needs_you:
        thing = "thing" if needs_you == 1 else "things"
        headline = f"{needs_you} {thing} need{'s' if needs_you == 1 else ''} you."
    elif p["bad"]:
        headline = "Serving, with a provider down."
    else:
        headline = "Serving normally."

    # The headline already says how much needs the owner, so the tags only
    # carry what it doesn't: how the providers themselves are doing.
    tags = [tag("span", esc(f"{p['ok']} of {p['total']} providers fine"),
                cls="tag state-ok")]
    if p["warn"]:
        tags.append(tag("span", esc(f"{p['warn']} unsettled"), cls="tag state-warn"))
    if p["bad"]:
        tags.append(tag("span", esc(f"{p['bad']} down"), cls="tag state-bad"))

    if window["requests"] == 0:
        story = (f"Nothing has come through in the last {label}. Point an app "
                 "at this address and the rest of this page fills in.")
    else:
        busiest = window["providers"][0]
        share = busiest["requests"] / window["requests"]
        story = (f"{window['requests']:,} requests in the last {label}, "
                 f"{_pct(window['answered_rate'])} answered. "
                 f"{busiest['name']} carried {_pct(share)} of them.")
        if window["failovers"]:
            plural = "" if window["failovers"] == 1 else "s"
            story += (f" {window['failovers']:,} request{plural} changed model "
                      "mid-flight and still got an answer.")

    spend_figure, spend_note = _spend(window)
    minis = [
        ("Median reply",
         f"{window['latency']['p50']} ms" if window["latency"]["p50"] is not None else "-",
         ""),
        ("Slowest 5%",
         f"{window['latency']['p95']} ms" if window["latency"]["p95"] is not None else "-",
         ""),
        ("Failovers", f"{window['failovers']:,}",
         f"{window['gave_up']:,} gave up" if window["gave_up"] else ""),
        ("Busiest " + ("day" if window["bucket_hours"] >= 24 else "hour"
                       if window["bucket_hours"] == 1 else "block"),
         f"{window['peak_hour']:,} req", ""),
        ("Spent", spend_figure, spend_note),
    ]
    mini_html = "".join(
        tag("div",
            tag("span", esc(name)) + tag("b", esc(value))
            + (tag("small", esc(note)) if note else ""),
            cls="mini")
        for name, value, note in minis
    )

    return tag(
        "div",
        tag("div", tag("strong", esc(headline)) + "".join(tags), cls="verdict-line")
        + tag("p", esc(story))
        + tag("div", mini_html, cls="minis"),
        cls="panel verdict",
    )


def _overview_tally(data: dict, window: dict, label: str) -> str:
    m, k = data["models"], data["keys"]
    splits = [
        (f"{m['available']}", f" / {m['total']}", "Models up"),
        (f"{k['live']}", f" / {k['total']}", "Keys live"),
        (_pct(window["answered_rate"]), "", "Answered"),
    ]
    split_html = "".join(
        tag("div",
            tag("b", esc(big) + tag("i", esc(rest)))
            + tag("span", esc(label)),
            )
        for big, rest, label in splits
    )
    return tag(
        "div",
        tag("span", _num(window["requests"]), cls="tally-big")
        + tag("span", esc(f"requests in the last {label}"), cls="tally-sub")
        + tag("div", split_html, cls="tally-split"),
        cls="panel tally",
    )


def _chart_table(window: dict, series: list[dict]) -> str:
    """The same numbers as the chart, as a table.

    A chart is a picture; this is what a screen reader, a copy-paste and a
    "what exactly was that spike" all need.
    """
    header = tag("tr", "".join(
        [tag("th", "Hour")]
        + [tag("th", esc(s["name"])) for s in series]
        + [tag("th", "Total"), tag("th", "Failed")]
    ))
    rows = [header]
    for i, label in enumerate(window["labels"]):
        rows.append(tag("tr", "".join(
            [tag("td", esc(label))]
            + [tag("td", esc(s["values"][i])) for s in series]
            + [tag("td", esc(window["totals_by_hour"][i])),
               tag("td", esc(window["fails_by_hour"][i]))]
        )))
    return tag(
        "details",
        tag("summary", "View as a table")
        + tag("div", tag("table", "".join(rows)), cls="scroll"),
        cls="table-view",
    )


def _overview_chart(window: dict) -> str:
    if window["requests"] == 0:
        return _panel(
            "Who answered, hour by hour",
            tag("div", tag("p", "No traffic yet in this window.", cls="note"), cls="pb"),
        )

    series = _chart_series(window)
    colors = _series_colors(series)
    legend = "".join(
        tag("span",
            _swatch(colors[s["name"]])
            + tag("span", esc(s["name"]))
            + tag("span", _num(s["requests"]), cls="n"),
            cls="legend-item")
        for s in series
    )
    svg = charts.stacked_hours(series, window["labels"], window["fails_by_hour"])
    return _panel(
        "Who answered, hour by hour",
        tag("div", tag("div", svg, cls="plot") + _chart_table(window, series), cls="pb"),
        action=tag("div", legend, cls="legend"),
    )


def _overview_providers(summaries: list, window: dict, label: str) -> str:
    by_name = {p["name"]: p for p in window["providers"]}
    series = _chart_series(window)
    colors = _series_colors(series)
    hours = window["hours"]

    header = tag("tr", "".join(tag("th", h) for h in [
        "Provider", "State", "Keys", "Requests", "Answered",
        f"Last {label}", "Why",
    ]))
    rows = [header]
    for s in summaries:
        seen = by_name.get(s.name)
        color = colors.get(s.name, charts.IDLE_COLOR)
        values = seen["values"] if seen else [0] * hours
        states = seen["states"] if seen else ["none"] * hours
        reqs = seen["requests"] if seen else 0
        failed = seen["failed"] if seen else 0
        answered = _pct((reqs - failed) / reqs) if reqs else "-"

        strip = tag("span", "".join(
            tag("span", "", cls=f"seg seg-{st}") for st in states), cls="strip")
        shape = tag("div", charts.sparkline(values, color) + strip, cls="shape")

        rows.append(tag("tr", "".join([
            tag("td",
                tag("span", _swatch(color) + esc(s.name), cls="prov-name")
                + tag("span", esc(s.base_url), cls="prov-url")),
            tag("td", tag("span", esc(s.state), cls=f"state-{s.state}")),
            tag("td", esc(f"{s.keys_live} of {s.key_count} in use"), cls="dim"),
            tag("td", _num(reqs), cls="num"),
            tag("td", esc(answered), cls="num"),
            tag("td", shape),
            tag("td", esc(s.quarantine_reason or ""), cls="dim"),
        ])))

    return _panel(
        "Providers",
        tag("div", tag("table", "".join(rows), cls="matrix"), cls="scroll"),
        sub=f"each block is one {_bucket_word(window)}; grey means no traffic",
        action=tag("a", "Manage keys", href="/providers", cls="button-link"),
    )


def _overview_needs_you(broken_data: dict) -> str:
    items = broken_data["needs_you"]
    if not items:
        return _panel(
            "Needs you",
            tag("div", tag("p", "Nothing needs you right now.", cls="note"), cls="pb"),
        )

    body = ""
    for item in items:
        where = item.provider
        if item.detail:
            where = f"{where} - {item.detail}" if where else item.detail
        what = tag("b", esc(_KIND_LABELS.get(item.kind, item.kind)))
        if where:
            what += tag("span", esc(where))
        body += tag(
            "div",
            tag("span", "", cls="todo-mark")
            + tag("span", what, cls="todo-where")
            + tag("span", esc(item.reason), cls="todo-why"),
            cls="todo-item",
        )
    return _panel(
        "Needs you",
        body,
        action=tag("a", "What's broken", href="/broken", cls="button-link"),
    )


def _overview_buckets(router, window: dict, label: str) -> str:
    counts = {b["bucket"]: b["requests"] for b in window["buckets"]}
    names = list(router._cfg.tiers)
    for name in counts:
        if name not in names:
            names.append(name)
    if not names:
        return _panel("Buckets your apps can ask for",
                      tag("div", tag("p", "No buckets set up.", cls="note"), cls="pb"))

    peak = max([counts.get(n, 0) for n in names] or [0])
    ordered = sorted(names, key=lambda n: (-counts.get(n, 0), n))
    rows = ""
    for name in ordered:
        n = counts.get(name, 0)
        width = (n / peak * 100) if peak else 0
        rows += tag(
            "div",
            tag("span", esc(name), cls="bucket-name")
            + tag("span", tag("span", "", cls="bucket-fill",
                              style=f"width:{width:.1f}%"), cls="bucket-track")
            + tag("span", _num(n), cls="bucket-n"),
            cls="bucket-row",
        )
    return _panel("Buckets your apps can ask for",
                  tag("div", rows, cls="pb"),
                  sub=f"requests in the last {label}")


def _overview_latency(window: dict) -> str:
    models = window["latency"]["per_model"]
    if not models:
        return _panel("How fast each model replied",
                      tag("div", tag("p", "No answered requests yet.", cls="note"), cls="pb"))

    top = max(m["p95"] for m in models) or 1
    rows = ""
    for m in models:
        rows += tag(
            "div",
            tag("div",
                tag("div", esc(m["model"]), cls="lat-name")
                + tag("div",
                      tag("span", "", cls="lat-track")
                      + tag("span", "", cls="lat-p95",
                            style=f"width:{m['p95'] / top * 100:.1f}%")
                      + tag("span", "", cls="lat-p50",
                            style=f"width:{m['p50'] / top * 100:.1f}%"),
                      cls="lat-bar"))
            + tag("div", esc(f"{m['p50']:,}") + tag("span", esc(f" / {m['p95']:,}"), cls="dim"),
                  cls="lat-figs"),
            cls="lat-row",
        )
    return _panel("How fast each model replied",
                  tag("div", rows, cls="pb"),
                  sub="median / slowest 5%, in ms")


def _overview_feed(router) -> str:
    rows_data = facts.recent_requests(router, limit=8)
    if not rows_data:
        return _panel("Last requests",
                      tag("div", tag("p", "Nothing has come through yet.", cls="note"), cls="pb"))

    rows = ""
    for r in rows_data:
        answered = (f"{r.answered_by['provider']}/{r.answered_by['model']}"
                    if r.answered_by else "nothing answered")
        hops = ""
        if r.skipped_count:
            plural = "" if r.skipped_count == 1 else "s"
            hops = tag("span", esc(f" · {r.skipped_count} hop{plural}"), cls="hop")
        rows += tag(
            "div",
            tag("span", esc(_clock(r.at)), cls="feed-time")
            + tag("span", esc(r.bucket), cls="feed-bucket")
            + tag("span", esc(answered) + hops, cls="feed-model")
            + tag("span", esc(f"{r.ms_total:,} ms"),
                  cls="feed-ms state-ok" if r.ok else "feed-ms state-bad"),
            cls="feed-row",
        )
    return _panel("Last requests",
                  tag("div", rows, cls="pb"),
                  sub="newest first",
                  action=tag("a", "Open Requests", href="/requests", cls="button-link"))


def _overview_inner(router, range_key: str) -> str:
    """Everything inside `.ov` — the part that live-updating replaces.

    Split out from `_overview_body` so the poll can ask for exactly this
    and swap it in, rather than re-parsing a whole document to find the
    piece that changed.
    """
    key, label, hours, bucket_hours, fmt = _range(range_key)
    summaries = facts.provider_summaries(router)
    broken_data = facts.broken(router)
    data = facts.overview(router, summaries=summaries, broken_data=broken_data)
    window = stats.recent_window(router._cfg.state_dir, hours=hours,
                                 bucket_hours=bucket_hours, label_format=fmt)
    said = label.lower()

    banner = tag("div",
                 _overview_verdict(data, summaries, window, said)
                 + _overview_tally(data, window, said),
                 cls="ov-banner")

    row2 = tag("div",
               _overview_chart(window)
               + tag("div",
                     _overview_needs_you(broken_data)
                     + _overview_buckets(router, window, said),
                     cls="ov-stack"),
               cls="ov-row")

    row4 = tag("div",
               _overview_latency(window) + _overview_feed(router),
               cls="ov-duo")

    return banner + row2 + _overview_providers(summaries, window, said) + row4


def _overview_body(router, range_key: str = DEFAULT_RANGE) -> str:
    key = _range(range_key)[0]
    header = tag(
        "div",
        tag("h1", "Overview")
        + _range_switcher(key)
        + tag("span", "", cls="spacer")
        + tag("span", tag("span", "", cls="lamp") + "live", cls="live-note"),
        cls="page-head",
    )
    # `data-range` is what the poller re-requests; `data-poll` how often.
    return header + tag(
        "div", _overview_inner(router, key),
        cls="ov", id="ov", **{"data-range": key, "data-poll": "10"},
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
        tag("td", esc(s.state), cls=f"state-{s.state}"),
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
        + tag("a", "Add", href=f"/providers/add/{quote(p.name)}", cls="button-link")
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
        tag("div", tag("h1", "Providers & keys"), cls="page-head")
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
        tag("div", tag("h1", esc(detail.name)), cls="page-head")
        + banner
        + tag("p", esc(detail.base_url), cls="lede")
        + quarantine_note
        + tag("div",
              models_panel + add_model_panel + keys_panel + edit_panel,
              cls="panel-page")
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
        return HTMLResponse(_overview_inner(router, key))
    return HTMLResponse(page("Overview", "overview", _overview_body(router, key)))


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
def provider_add_page(name: str, ok: str = "", message: str = "") -> HTMLResponse:
    preset = presets.get(name)
    if preset is None:
        return HTMLResponse(
            page("Providers & keys", "providers",
                 tag("h1", "No such preset") + tag("p", esc(name))),
            status_code=404,
        )
    return HTMLResponse(
        page(f"Add {preset.label} - Providers & keys", "providers",
             _preset_add_body(preset, _message_banner(ok, message))))


def _preset_add_body(p, banner: str = "") -> str:
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
    )
    return (
        tag("div", tag("h1", esc(p.label)), cls="page-head")
        + banner
        + tag("p", esc(p.base_url), cls="lede")
        + tag("div",
              _panel("Connect it",
                     tag("div", where + tag("p", esc(discovery), cls="note") + form,
                         cls="pb")),
              cls="panel-page")
    )


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


@pages.get("/providers/{provider}/discover", response_class=HTMLResponse,
           include_in_schema=False)
async def provider_discover(provider: str) -> HTMLResponse:
    router = _live_router()
    preset = presets.get(provider)
    pcfg = router._cfg.providers.get(provider)
    if pcfg is None:
        return HTMLResponse(
            page("Providers & keys", "providers",
                 tag("h1", "No such provider") + tag("p", esc(provider))),
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
    head = tag("div", tag("h1", esc(provider)), cls="page-head")
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


