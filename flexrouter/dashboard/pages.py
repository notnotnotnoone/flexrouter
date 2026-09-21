"""The dashboard's pages.

Server-rendered HTML, no build step, no JavaScript. Every area is a real URL
and every future control will be a real form, so there is no client state
that can disagree with the service - see the Stage 8 roadmap, ruling R2.

Areas not yet built return an honest stub rather than an empty shell. Each
lands in its own sub-plan.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from flexrouter.dashboard import facts
from flexrouter.dashboard.render import AREAS, esc, page, tag

pages = APIRouter()

CSS_PATH = Path(__file__).parent / "wire.css"

# Which sub-plan builds each area, so a stub can say something true.
_PLANNED = {
    "providers": "sub-plan 3",
    "models": "sub-plan 4",
    "buckets": "sub-plan 5",
    "requests": "sub-plan 6",
    "broken": "sub-plan 2",
    "brain": "sub-plan 6",
    "allowance": "sub-plan 6",
    "settings": "sub-plan 7",
}


def _tile(label: str, value: object, note: str = "") -> str:
    return tag(
        "div",
        tag("span", esc(label)) + tag("b", esc(value)) + tag("small", esc(note)),
        cls="tile",
    )


def _overview_body(router) -> str:
    data = facts.overview(router)
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
    for s in facts.provider_summaries(router):
        rows.append(tag("tr", "".join([
            tag("td", esc(s.name)),
            tag("td", esc(s.state), cls=f"state-{s.state}"),
            tag("td", esc(s.base_url)),
            tag("td", esc(f"{s.keys_live} of {s.key_count} in use")),
            tag("td", esc(s.models_total)),
            tag("td", esc(s.quarantine_reason or "")),
        ])))

    buckets = ", ".join(data["service"]["buckets"]) or "none set up"

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
        + tag("p", "The problems only you can fix will appear here once "
                   "What's broken is built.", cls="note")
    )


def _stub_body(slug: str, label: str) -> str:
    where = _PLANNED.get(slug, "a later sub-plan")
    return (
        tag("h1", esc(label))
        + tag("div",
              tag("p", f"This area is not built yet. It arrives in {esc(where)}.")
              + tag("p", "The menu is real so nothing later has to guess at "
                         "the frame, and so a half-finished dashboard is "
                         "never mistaken for a finished one."),
              cls="stub")
    )


@pages.get("/wire.css", include_in_schema=False)
def stylesheet() -> Response:
    return Response(CSS_PATH.read_text(encoding="utf-8"),
                    media_type="text/css; charset=utf-8")


@pages.get("/", response_class=HTMLResponse, include_in_schema=False)
def overview_page() -> HTMLResponse:
    from flexrouter.app import get_router
    return HTMLResponse(
        page("Overview", "overview", _overview_body(get_router()))
    )


def _register_stub(slug: str, label: str) -> None:
    @pages.get(f"/{slug}", response_class=HTMLResponse,
               include_in_schema=False, name=f"page_{slug}")
    def _handler() -> HTMLResponse:
        return HTMLResponse(page(label, slug, _stub_body(slug, label)))


for _slug, _label, _ in AREAS:
    if _slug != "overview":
        _register_stub(_slug, _label)
