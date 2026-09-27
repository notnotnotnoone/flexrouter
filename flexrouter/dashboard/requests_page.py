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
             q: str = "", client: str = "", limit: int = PAGE) -> tuple[list, int]:
    """Rows matching the filters, newest first, and how many matched in all.

    Filters look back over the last few thousand requests rather than only
    the last page, so "show me failures" finds failures that are not recent.
    """
    rows = facts.recent_requests(router, limit=5000)
    q = q.strip().lower()

    def keep(r) -> bool:
        if result and r.outcome != result:
            return False
        if client and r.client != client:
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
    href = f"/requests/{r.id}"
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


def _secs(ms) -> str:
    return f"{(ms or 0) / 1000:.1f}s"


def _msg(who: str, text: str, cls: str = "") -> str:
    return tag("div", tag("span", esc(who), cls="rq-who") + tag("div", esc(text), cls="rq-text"),
               cls=("rq-msg " + cls).strip())


def _tried_first(failed: list[dict]) -> str:
    """Each failed attempt, and the time the caller lost to it: any wait for
    a free slot before it, plus the attempt itself (papercut 9)."""
    items, lost = "", 0
    for s in failed:
        waited = (s.get("waited_ms") or 0) + (s.get("ms") or 0)
        lost += waited
        status = str(s["status"]) if s.get("status") else "no status"
        why = (s.get("message") or s.get("verdict", "").replace("_", " ") or "failed").strip()
        items += tag("li", tag("span", esc(f"{s['provider']}/{s['model']}"))
                     + tag("span", esc(f"{status} {why.splitlines()[0][:160]}"), cls="bad")
                     + tag("span", esc(f"waited {_secs(waited)}"), cls="rq-wait"))
    plural = "" if len(failed) == 1 else "s"
    return ui.fold("Tried first", tag("ul", items, cls="rq-tried"),
                   note=f"{len(failed)} model{plural} · waited {_secs(lost)}")


def _passed_over(skipped: list[dict]) -> str:
    items = "".join(
        tag("li", tag("span", esc(f"{s['provider']}/{s['model']}"))
            + tag("span", esc(s["detail"] or s["reason"].replace("_", " ")), cls="dim"))
        for s in skipped)
    return ui.fold("Passed over", tag("ul", items, cls="rq-tried"),
                   note=f"{len(skipped)} not tried")


def journey_panel(j: dict, close_href: str = "/requests") -> str:
    """The request sheet (grill-decisions.md §7): a one-line header, then
    Thinking, You, Reply, and what was tried before the answer."""
    steps = j["steps"]
    answered = next((s for s in steps if s["kind"] == "answered"), None)
    failed = [s for s in steps if s["kind"] == "failed"]
    skipped = [s for s in steps if s["kind"] == "skipped"]
    who = f"{answered['provider']}/{answered['model']}" if answered else "nothing answered"
    line = tag("div",
               tag("span", _OUTCOME_WORD[j["outcome"]], cls=f"outcome outcome-{j['outcome']}")
               + tag("span", esc(j["bucket"] or "-"))
               + tag("span", "→", cls="arrow") + tag("span", esc(who))
               + tag("span", esc(_secs(j["ms_total"])), cls="n")
               + tag("span", esc(clock(j["at"])), cls="n"),
               cls="rq-line")
    sub = tag("div", tag("span", esc(j["id"]), cls="n")
              + tag("span", esc(f" · {j['tokens_in']:,} in / {j['tokens_out']:,} out"), cls="n"),
              cls="rq-line")

    convo = j.get("conversation")
    parts = ""
    if convo:
        if convo.get("reasoning"):
            parts += ui.fold("Thinking", tag("div", esc(convo["reasoning"]), cls="rq-think"),
                             note=f"{len(convo['reasoning']):,} characters")
        msgs = "".join(_msg("You" if m["role"] == "user" else m["role"].title() or "?",
                            m["content"], "rq-you" if m["role"] == "user" else "")
                       for m in convo.get("messages") or [])
        if answered:
            msgs += _msg("Reply", convo.get("reply") or "(no text: the reply was tool calls)")
        else:
            gave_up = failed[-1]["message"] if failed else "Every option was tried or passed over."
            msgs += tag("div", tag("span", "Reply", cls="rq-who")
                        + tag("div", esc(gave_up or "Nothing answered."), cls="rq-text bad"),
                        cls="rq-msg")
        parts += tag("div", msgs, cls="rq-convo")
    else:
        parts += tag("p", "The prompt and reply weren't saved for this request "
                     "(saving was off, or it's older than the keep window in Settings).",
                     cls="dim rq-convo")
    if failed:
        parts += _tried_first(failed)
    if skipped:
        parts += _passed_over(skipped)
    return ui.sheet("Request", line + sub + parts, close_href=close_href,
                    sub="What was asked, what came back, and what was tried first")


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
