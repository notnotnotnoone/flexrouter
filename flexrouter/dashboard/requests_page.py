"""The Requests page: every request, newest first, and each one's journey.

The log is live - it re-fetches itself and morphs in place, so a new
request slides in at the top without the page moving. Filters are a plain
GET form, so every filtered view is a URL. Clicking a row opens a side
panel with that request's journey: what was passed over, each attempt that
failed, and what finally answered - read straight from its trace.
"""
from __future__ import annotations

from urllib.parse import urlencode

from flexrouter.dashboard import facts, prefs, ui
from flexrouter.dashboard.overview import clock, num
from flexrouter.dashboard.render import esc, tag

RESULTS = (("", "Any result"), ("ok", "OK"), ("failover", "Failover"), ("failed", "Failed"))
_OUTCOME_WORD = {"ok": "OK", "failover": "FAILOVER", "failed": "FAILED"}
PAGE = 50


def _answered(r) -> str:
    return (f"{r.answered_by['provider']}/{r.answered_by['model']}"
            if r.answered_by else "nothing answered")


def filtered(router, *, result: str = "", bucket: str = "", provider: str = "",
             q: str = "", limit: int = PAGE) -> tuple[list, int]:
    """Rows matching the filters, newest first, and how many matched in all.

    Filters look back over the last few thousand requests rather than only
    the last page, so "show me failures" finds failures that are not recent.
    """
    rows = facts.recent_requests(router, limit=5000)
    q = q.strip().lower()

    def keep(r) -> bool:
        if result and r.outcome != result:
            return False
        if bucket and r.bucket != bucket:
            return False
        if provider and (not r.answered_by or r.answered_by.get("provider") != provider):
            return False
        if q and q not in f"{r.id} {r.bucket} {_answered(r)}".lower():
            return False
        return True

    matched = [r for r in rows if keep(r)]
    return matched[:limit], len(matched)


def _query(**params) -> str:
    clean = {k: v for k, v in params.items() if v not in ("", None, PAGE)}
    return urlencode(clean)


def _filters(router, current: dict) -> str:
    buckets = [""] + list(router._cfg.tiers)
    providers = [""] + list(router._cfg.providers)

    def select(name, options, labels=None):
        opts = "".join(
            f'<option value="{esc(v)}"{" selected" if current.get(name) == v else ""}>'
            f'{esc((labels or {}).get(v, v) or "Any " + name)}</option>'
            for v in options)
        return f'<select name="{name}" aria-label="{esc(name)}">{opts}</select>'

    results = dict(RESULTS)
    return tag(
        "form",
        ui.icon("search")
        + f'<input type="search" name="q" value="{esc(current.get("q", ""))}" '
          'placeholder="Search id, bucket or model" aria-label="Search" data-page-search>'
        + select("result", [v for v, _ in RESULTS], results)
        + select("bucket", buckets)
        + select("provider", providers),
        cls="filters", method="get", action="/requests",
        **{"hx-get": "/requests", "hx-trigger": "change, input changed delay:300ms from:[name=q]",
           "hx-target": "#req-log", "hx-select": "#req-log", "hx-swap": "outerHTML",
           "hx-push-url": "true"},
    )


def _row(r) -> str:
    href = f"/requests?id={r.id}"
    open_link = tag("a", esc(clock(r.at)), href=href, cls="row-link",
                    **{"hx-get": f"/requests/{r.id}/journey", "hx-target": "#sheet-root",
                       "hx-swap": "innerHTML", "hx-push-url": href})
    hops = ""
    if r.attempt_count:
        plural = "" if r.attempt_count == 1 else "s"
        hops = tag("span", esc(f" · {r.attempt_count} failed attempt{plural}"), cls="hop")
    return tag("tr", "".join([
        tag("td", open_link, cls="mono dim"),
        tag("td", esc(r.bucket), cls="mono"),
        tag("td", esc(_answered(r)) + hops, cls="mono"),
        tag("td", esc(f"{r.tokens_in:,} / {r.tokens_out:,}"), cls="num mono"),
        tag("td", esc(f"{r.ms_total:,} ms"), cls="num mono"),
        tag("td", tag("span", _OUTCOME_WORD[r.outcome], cls=f"outcome outcome-{r.outcome}")),
        tag("td", esc(r.id), cls="mono faint"),
    ]), cls="req-row", **{"data-row": f"req:{r.id}", "data-value": r.id})


def log(router, params: dict) -> str:
    """The live block: the table plus 'load more'. Polls itself with the
    same filters, so a filtered view stays filtered as it updates."""
    limit = params.get("limit") or PAGE
    rows, total = filtered(router, result=params.get("result", ""),
                           bucket=params.get("bucket", ""),
                           provider=params.get("provider", ""),
                           q=params.get("q", ""), limit=limit)
    query = _query(result=params.get("result"), bucket=params.get("bucket"),
                   provider=params.get("provider"), q=params.get("q"), limit=limit)
    poll = "/requests?" + (query + "&" if query else "") + "fragment=1"
    if not rows:
        anything = params.get("result") or params.get("bucket") or params.get("provider") or params.get("q")
        inner = ui.empty("No requests match these filters." if anything
                         else "Nothing has come through yet. Point an app at this address "
                              "and requests appear here as they happen.")
    else:
        header = tag("tr", "".join(tag("th", h, cls=c) for h, c in [
            ("Time", ""), ("Bucket", ""), ("Answered by", ""), ("Tokens in / out", "num"),
            ("Took", "num"), ("Result", ""), ("Request", ""),
        ]))
        more = ""
        if total > len(rows):
            more_q = _query(result=params.get("result"), bucket=params.get("bucket"),
                            provider=params.get("provider"), q=params.get("q"),
                            limit=limit + PAGE)
            more = tag("div", ui.button(f"Load {min(PAGE, total - len(rows))} more",
                                        href=f"/requests?{more_q}"), cls="load-more")
        inner = (tag("div", tag("table", tag("thead", header)
                                + tag("tbody", "".join(_row(r) for r in rows)),
                                cls="log"), cls="scroll")
                 + more)
    refresh = max(5, prefs.load().refresh_seconds // 2)
    return tag("div", inner, id="req-log", cls="box flush",
               **{"data-live": "", "hx-get": poll,
                  "hx-trigger": f"every {refresh}s [!document.hidden]",
                  "hx-swap": "morph:innerHTML", "data-enter": ""})


_STEP = {
    "skipped": ("○", "Passed over"),
    "failed": ("✕", "Failed"),
    "answered": ("●", "Answered"),
    "gave_up": ("▲", "Nothing answered"),
}


def journey_panel(j: dict, close_href: str = "/requests") -> str:
    facts_row = "".join(
        tag("div", tag("span", esc(k), cls="stat-label") + tag("b", esc(v)), cls="jfact")
        for k, v in [("Bucket", j["bucket"] or "-"), ("Took", f"{j['ms_total']:,} ms"),
                     ("Tokens", f"{j['tokens_in']:,} in / {j['tokens_out']:,} out"),
                     ("When", clock(j["at"]))])
    steps = ""
    for n, s in enumerate(j["steps"], 1):
        glyph, word = _STEP[s["kind"]]
        if s["kind"] == "gave_up":
            what = tag("div", "Every option was tried or passed over.", cls="jstep-detail")
            where = ""
        else:
            where = tag("div", esc(f"{s['provider']}/{s['model']}"), cls="jstep-where")
            if s["kind"] == "skipped":
                what = tag("div", esc(s["detail"] or s["reason"].replace("_", " ")),
                           cls="jstep-detail") + ui.tag_(s["reason"].replace("_", " "))
            elif s["kind"] == "failed":
                status = f"HTTP {s['status']}" if s.get("status") else "no status"
                what = (tag("pre", esc(s["message"]), cls="jstep-msg") if s["message"] else "") \
                    + ui.tag_(status, "bad") + (ui.tag_(s["verdict"].replace("_", " "), "violet")
                                               if s["verdict"] else "") \
                    + (tag("span", esc(f"{s['ms']:,} ms"), cls="n") if s.get("ms") is not None else "")
            else:
                what = tag("span", esc(f"{(s.get('ms') or 0):,} ms total"), cls="n")
        steps += tag("li",
                     tag("span", esc(str(n)), cls="jstep-n")
                     + tag("div",
                           tag("div", tag("span", f"{glyph} {esc(word).upper()}",
                                          cls=f"jstep-kind jstep-{s['kind']}") + where,
                               cls="jstep-head") + what,
                           cls="jstep-body"),
                     cls=f"jstep jstep-{s['kind']}")
    outcome = tag("span", _OUTCOME_WORD[j["outcome"]], cls=f"outcome outcome-{j['outcome']}")
    body = (tag("div", outcome + tag("span", esc(j["id"]), cls="n"), cls="jtop")
            + tag("div", facts_row, cls="jfacts")
            + tag("h3", "Journey", cls="sheet-h")
            + tag("ol", steps, cls="journey"))
    return ui.sheet("Request", body, close_href=close_href,
                    sub="What flexrouter tried, in order")


def body(router, params: dict) -> str:
    rows, total = filtered(router, limit=5000)
    fo = sum(1 for r in rows if r.outcome == "failover")
    bad = sum(1 for r in rows if r.outcome == "failed")
    status = (f"{num(total)} requests on record · {num(fo)} failovers · {num(bad)} failed"
              if total else "Every request flexrouter handles, newest first.")
    head = tag("div",
               tag("div", tag("h1", "Requests", cls="page-title")
                   + tag("p", status, cls="page-status"), cls="page-head-text")
               + tag("div", tag("span", tag("span", "", cls="live-dot") + "live", cls="live"),
                     cls="page-actions"),
               cls="page-head")
    return head + _filters(router, params) + log(router, params)
