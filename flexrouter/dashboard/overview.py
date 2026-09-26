"""The Overview: what the router has been doing, on one screen.

Layout (design spec §6, the approved sketch):

    page head   // OVERVIEW                        [24H][7D][30D][ALL]  LIVE
    #ov         verdict line and the story of the window
                five stats: requests · answered · failovers · median · spent
                who answered, hour by hour  |  needs you
                buckets (block meters)
                providers (status, hour strip)
                last requests  |  how fast

Everything inside `#ov` is what the live refresh re-fetches and morphs in
place, so it is built by exactly one function (`inner`) for both the page
and the refresh - the live view cannot drift from the served one.
"""
from __future__ import annotations

from datetime import datetime

from flexrouter.dashboard import charts, facts, prefs, stats, ui
from flexrouter.dashboard.render import esc, tag

# (key, label, hours, bucket_hours, label_format)
RANGES: list[tuple[str, str, object, int, str]] = [
    ("24h", "24 hours", 24, 1, "%H:%M"),
    ("7d", "7 days", 24 * 7, 6, "%a %H:%M"),
    ("30d", "30 days", 24 * 30, 24, "%d %b"),
    ("all", "All", None, 24, "%d %b"),
]
_RANGE_BY_KEY = {r[0]: r for r in RANGES}
DEFAULT_RANGE = "24h"

KIND_LABELS = {
    "provider_needs_you": "provider needs you",
    "model_needs_you": "model needs you",
    "key_needs_you": "key needs you",
    "model_busy": "model busy",
    "model_struggling": "model struggling",
    "key_busy": "key busy",
    "unclear_error": "an error it isn't sure about",
}


def range_for(key: str):
    """The range `key` names, falling back to 24 hours.

    A querystring is user input: an unknown value shows the default rather
    than raising, the same way a bad page number shows page one.
    """
    return _RANGE_BY_KEY.get(key or "", _RANGE_BY_KEY[DEFAULT_RANGE])


# ── small formatting helpers ──────────────────────────────────────────

def num(value: object) -> str:
    """A whole number with thousands separators, escaped."""
    try:
        return esc(f"{int(value):,}")
    except (TypeError, ValueError):
        return esc(value)


def clock(iso: str) -> str:
    """A stored UTC timestamp as a local wall clock.

    Traces are written in UTC and the chart buckets by local hour
    (`stats.recent_window`). Printing the raw UTC time next to that chart
    would put two different clocks on one screen.
    """
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError):
        return iso or ""
    return dt.strftime("%H:%M:%S")


def pct(fraction) -> str:
    if fraction is None:
        return "-"
    return f"{fraction * 100:.1f}%".replace(".0%", "%")


def spend(window: dict) -> tuple[str, str]:
    """What the window cost, and the note that goes under it.

    Three different answers, and they must not be confused:
      - nothing ran                -> no figure at all
      - it ran, nothing was priced -> "not priced", NOT "$0.00"
      - it ran and was priced      -> the money
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


def bucket_word(window: dict) -> str:
    """What one block on the chart covers, said in words. The strips and
    the chart share a bucket size, and a strip that claims to be hourly
    when each block is six hours is a quiet lie about when something broke."""
    n = window["bucket_hours"]
    if n >= 24:
        return "day" if n == 24 else f"{n // 24} days"
    return "hour" if n == 1 else f"{n} hours"


def chart_series(window: dict) -> list[dict]:
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


# ── the page head ─────────────────────────────────────────────────────

def range_switcher(current: str) -> str:
    """Real links, one per range, so the range lives in the URL."""
    out = []
    for key, label, _h, _b, _f in RANGES:
        short = {"24h": "24H", "7d": "7D", "30d": "30D", "all": "ALL"}[key]
        out.append(tag(
            "a", esc(short),
            href="/" if key == DEFAULT_RANGE else f"/?range={key}",
            cls="range-link", title=label,
            **{"aria-current": "true" if key == current else None},
        ))
    return tag("nav", "".join(out), cls="ranges", **{"aria-label": "Time range"})


def _head(key: str) -> str:
    return tag(
        "div",
        tag("div", tag("h1", "Overview", cls="page-title"), cls="page-head-text")
        + tag("div",
              range_switcher(key)
              + tag("span", tag("span", "", cls="live-dot") + "live", cls="live"),
              cls="page-actions"),
        cls="page-head",
    )


# ── blocks inside #ov ─────────────────────────────────────────────────

def _verdict(data: dict, window: dict, label: str) -> str:
    p = data["providers"]
    needs_you = data["needs_you"]
    if needs_you:
        thing = "thing" if needs_you == 1 else "things"
        headline = f"{needs_you} {thing} need{'s' if needs_you == 1 else ''} you."
        state = "warn"
    elif p["bad"]:
        headline, state = "Serving, with a provider down.", "bad"
    else:
        headline, state = "Serving normally.", "ok"

    tags = [ui.tag_(f"{p['ok']} of {p['total']} providers fine", "ok")]
    if p["warn"]:
        tags.append(ui.tag_(f"{p['warn']} unsettled", "warn"))
    if p["bad"]:
        tags.append(ui.tag_(f"{p['bad']} down", "bad"))

    if window["requests"] == 0:
        story = (f"Nothing has come through in the last {label}. Point an app "
                 "at this address and the rest of this page fills in.")
    else:
        busiest = window["providers"][0]
        share = busiest["requests"] / window["requests"]
        story = (f"{window['requests']:,} requests in the last {label}, "
                 f"{pct(window['answered_rate'])} answered. "
                 f"{busiest['name']} carried {pct(share)} of them.")
        if window["failovers"]:
            plural = "" if window["failovers"] == 1 else "s"
            story += (f" {window['failovers']:,} request{plural} changed model "
                      "mid-flight and still got an answer.")

    return tag("div",
               tag("div", tag("strong", esc(headline)) + "".join(tags), cls="verdict-line")
               + tag("p", esc(story), cls="verdict-story"),
               cls=f"verdict verdict-{state}", **{"data-enter": ""})


def _stats(data: dict, window: dict, label: str) -> str:
    spend_figure, spend_note = spend(window)
    lat = window["latency"]
    spark = (charts.sparkline(window["totals_by_hour"], "var(--green)", width=200, height=22)
             if window["requests"] else "")
    failover_note = (f"{window['gave_up']:,} gave up" if window["gave_up"]
                     else "changed model, still answered")
    return tag("div", "".join([
        ui.stat("Requests", f"{window['requests']:,}", key="requests",
                note=f"last {label}", spark=spark),
        ui.stat("Answered", pct(window["answered_rate"]), key="answered",
                note=f"{data['models']['available']} of {data['models']['total']} models up"),
        ui.stat("Failovers", f"{window['failovers']:,}", key="failovers", note=failover_note),
        ui.stat("Median reply", f"{lat['p50']:,} ms" if lat["p50"] is not None else "-",
                key="median",
                note=f"slowest 5%: {lat['p95']:,} ms" if lat["p95"] is not None else ""),
        ui.stat("Spent", spend_figure, key="spent", note=spend_note),
    ]), cls="stats", **{"data-enter": ""})


def _chart_table(window: dict, series: list[dict]) -> str:
    """The same numbers as the chart, as a table - what a screen reader, a
    copy-paste and a "what exactly was that spike" all need."""
    header = tag("tr", "".join(
        [tag("th", "Hour")]
        + [tag("th", esc(s["name"])) for s in series]
        + [tag("th", "Total"), tag("th", "Failed")]
    ))
    rows = [header]
    for i, lab in enumerate(window["labels"]):
        rows.append(tag("tr", "".join(
            [tag("td", esc(lab))]
            + [tag("td", esc(s["values"][i])) for s in series]
            + [tag("td", esc(window["totals_by_hour"][i])),
               tag("td", esc(window["fails_by_hour"][i]))]
        )))
    return tag("details",
               tag("summary", "View as a table")
               + tag("div", tag("table", "".join(rows)), cls="scroll"),
               cls="table-view")


def _chart(window: dict) -> str:
    title = "Who answered, " + ("hour by hour" if window["bucket_hours"] == 1
                                else f"{bucket_word(window)} by {bucket_word(window)}"
                                if window["bucket_hours"] >= 24 else "block by block")
    if window["requests"] == 0:
        return ui.box(title, ui.empty("No traffic yet in this window."),
                      **{"data-enter": "", "data-box": "chart"})
    series = chart_series(window)
    colors = _series_colors(series)
    legend = "".join(
        tag("span", _swatch(colors[s["name"]]) + tag("span", esc(s["name"]))
            + tag("span", num(s["requests"]), cls="n"), cls="legend-item")
        for s in series)
    svg = charts.stacked_hours(series, window["labels"], window["fails_by_hour"])
    return ui.box(title, tag("div", svg, cls="plot") + _chart_table(window, series),
                  action=tag("div", legend, cls="legend"),
                  **{"data-enter": "", "data-box": "chart"})


def _needs_you(broken_data: dict) -> str:
    items = broken_data["needs_you"]
    if not items:
        return ui.box("Needs you", ui.empty("Nothing needs you right now."),
                      **{"data-enter": "", "data-box": "needs-you"})
    body = ""
    for item in items:
        where = item.provider
        if item.detail:
            where = f"{where} - {item.detail}" if where else item.detail
        what = tag("b", esc(KIND_LABELS.get(item.kind, item.kind)))
        if where:
            what += tag("span", esc(where))
        body += tag("div",
                    tag("span", "", cls="todo-mark")
                    + tag("span", what, cls="todo-where")
                    + ui.button("Fix", href="/broken", kind="primary")
                    + tag("span", esc(item.reason), cls="todo-why"),
                    cls="todo-item")
    return ui.box("Needs you", body, cls="flush",
                  action=tag("span", esc(len(items)), cls="nav-badge"),
                  **{"data-enter": "", "data-box": "needs-you"})


def _buckets(router, window: dict, label: str) -> str:
    counts = {b["bucket"]: b["requests"] for b in window["buckets"]}
    names = list(router._cfg.tiers)
    for name in counts:
        if name not in names:
            names.append(name)
    if not names:
        return ui.box("Buckets", ui.empty("No buckets set up.",
                                          action=ui.button("Set up buckets", href="/buckets")),
                      **{"data-enter": "", "data-box": "buckets"})
    peak = max([counts.get(n, 0) for n in names] or [0])
    ordered = sorted(names, key=lambda n: (-counts.get(n, 0), n))
    rows = ""
    for name in ordered:
        n = counts.get(name, 0)
        rows += tag("div",
                    tag("span", esc(name), cls="bucket-name")
                    + ui.meter(n / peak if peak else 0, label=name, share=True)
                    + tag("span", "", cls="spacer")
                    + tag("span", num(n), cls="bucket-n", **{"data-value": n}),
                    cls="bucket-row", **{"data-row": f"bucket:{name}"})
    return ui.box("Buckets", rows,
                  sub=f"requests in the last {label}, against the busiest bucket",
                  action=ui.button("Manage", href="/buckets", kind="ghost"),
                  **{"data-enter": "", "data-box": "buckets"})


def _providers(summaries: list, window: dict, label: str) -> str:
    by_name = {p["name"]: p for p in window["providers"]}
    colors = _series_colors(chart_series(window))
    hours = window["hours"]
    header = tag("tr", "".join(tag("th", h, cls=c) for h, c in [
        ("Provider", ""), ("State", ""), ("Keys", ""), ("Requests", "num"),
        ("Answered", "num"), (f"Last {label}", ""), ("Why", ""),
    ]))
    rows = [header]
    for s in summaries:
        seen = by_name.get(s.name)
        color = colors.get(s.name, charts.IDLE_COLOR)
        values = seen["values"] if seen else [0] * hours
        states = seen["states"] if seen else ["none"] * hours
        reqs = seen["requests"] if seen else 0
        failed = seen["failed"] if seen else 0
        answered = pct((reqs - failed) / reqs) if reqs else "-"
        strip = tag("span", "".join(tag("span", "", cls=f"seg seg-{st}") for st in states),
                    cls="strip")
        shape = tag("div", charts.sparkline(values, color, width=190) + strip, cls="shape")
        rows.append(tag("tr", "".join([
            tag("td", tag("span", _swatch(color) + esc(s.name), cls="prov-name")
                + tag("span", esc(s.base_url), cls="prov-url")),
            tag("td", ui.status(s.state)),
            tag("td", esc(f"{s.keys_ready} of {s.key_count} ready"), cls="dim"),
            tag("td", num(reqs), cls="num"),
            tag("td", esc(answered), cls="num"),
            tag("td", shape),
            tag("td", esc(s.status_reason or ""), cls="dim"),
        ]), **{"data-row": f"provider:{s.name}", "data-value": f"{reqs}/{failed}/{s.state}"}))
    if len(rows) == 1:
        body = tag("div", ui.empty("No providers yet.",
                                   action=ui.button("Add a provider", href="/providers",
                                                    kind="primary", icon_name="plus")),
                   cls="box-body")
        return ui.box("Providers", body, cls="flush",
                      sub=f"each block is one {bucket_word(window)}",
                      **{"data-enter": "", "data-box": "providers"})
    return ui.box("Providers",
                  tag("div", tag("table", "".join(rows), cls="matrix"), cls="scroll"),
                  cls="flush",
                  sub=f"each block is one {bucket_word(window)}; dark means no traffic",
                  action=ui.button("Manage keys", href="/providers", kind="ghost"),
                  **{"data-enter": "", "data-box": "providers"})


def _latency(window: dict) -> str:
    models = window["latency"]["per_model"]
    if not models:
        return ui.box("How fast", ui.empty("No answered requests yet."),
                      **{"data-enter": "", "data-box": "latency"})
    top = max(m["p95"] for m in models) or 1
    rows = ""
    for m in models:
        rows += tag("div",
                    tag("div",
                        tag("div", esc(m["model"]), cls="lat-name")
                        + tag("div",
                              tag("span", "", cls="lat-track")
                              + tag("span", "", cls="lat-p95",
                                    style=f"width:{m['p95'] / top * 100:.1f}%")
                              + tag("span", "", cls="lat-p50",
                                    style=f"width:{m['p50'] / top * 100:.1f}%"),
                              cls="lat-bar"))
                    + tag("div", esc(f"{m['p50']:,}")
                          + tag("span", esc(f" / {m['p95']:,}"), cls="dim"), cls="lat-figs"),
                    cls="lat-row",
                    **{"data-row": f"lat:{m['model']}", "data-value": f"{m['p50']}/{m['p95']}"})
    return ui.box("How fast", rows, sub="median / slowest 5%, ms",
                  **{"data-enter": "", "data-box": "latency"})


def _feed(router) -> str:
    rows_data = facts.recent_requests(router, limit=8)
    if not rows_data:
        return ui.box("Last requests", ui.empty("Nothing has come through yet."),
                      **{"data-enter": "", "data-box": "feed"})
    rows = ""
    for r in rows_data:
        answered = (f"{r.answered_by['provider']}/{r.answered_by['model']}"
                    if r.answered_by else "nothing answered")
        hops = ""
        if r.skipped_count:
            plural = "" if r.skipped_count == 1 else "s"
            hops = tag("span", esc(f" · {r.skipped_count} hop{plural}"), cls="hop")
        rows += tag("div",
                    tag("span", esc(clock(r.at)), cls="feed-time")
                    + tag("span", esc(r.bucket), cls="feed-bucket")
                    + tag("span", esc(answered) + hops, cls="feed-model")
                    + tag("span", esc(f"{r.ms_total:,} ms"),
                          cls="feed-ms status-ok" if r.ok else "feed-ms status-bad"),
                    cls="feed-row", **{"data-row": f"req:{r.id or r.at}", "data-value": r.id or r.at})
    return ui.box("Last requests", rows, sub="newest first",
                  action=ui.button("Open requests", href="/requests", kind="ghost"),
                  **{"data-enter": "", "data-box": "feed"})


# ── the two entry points ──────────────────────────────────────────────

def inner(router, range_key: str) -> str:
    """Everything inside `#ov` - what the live refresh morphs in place."""
    key, label, hours, bucket_hours, fmt = range_for(range_key)
    summaries = facts.provider_summaries(router)
    broken_data = facts.broken(router)
    data = facts.overview(router, summaries=summaries, broken_data=broken_data)
    window = stats.recent_window(router._cfg.state_dir, hours=hours,
                                 bucket_hours=bucket_hours, label_format=fmt)
    said = label.lower()
    return (
        _verdict(data, window, said)
        + _stats(data, window, said)
        + tag("div", _chart(window) + tag("div", _needs_you(broken_data), cls="ov-stack"),
              cls="ov-row")
        + _buckets(router, window, said)
        + _providers(summaries, window, said)
        + tag("div", _feed(router) + _latency(window), cls="ov-duo")
    )


def body(router, range_key: str = DEFAULT_RANGE) -> str:
    """The page head plus `#ov`, which polls itself for updates.

    `data-live` marks this element as a poller for app.js, so a refresh is
    never mistaken for a page navigation (which would replay entrances).
    """
    key = range_for(range_key)[0]
    refresh = prefs.load().refresh_seconds
    poll_url = f"/?range={key}&fragment=1"
    return _head(key) + tag(
        "div", inner(router, key),
        cls="ov", id="ov",
        **{"data-range": key, "data-poll": str(refresh), "data-live": "",
           "hx-get": poll_url,
           "hx-trigger": f"every {refresh}s [!document.hidden]",
           "hx-swap": "morph:innerHTML"},
    )
