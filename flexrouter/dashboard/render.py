"""HTML building blocks for the dashboard, plus the site's own menu.

Deliberately not a template engine. The dashboard is a wireframe the owner
intends to restyle himself, so the markup stays semantic and the whole of
its appearance lives in one stylesheet. Most of this module - `esc`,
`attrs`, `tag` - takes strings and returns strings, knowing nothing about
flexrouter. `AREAS` and `_nav` are the exception: they carry flexrouter's
own menu labels and know that the overview lives at `/`, because the menu
has to live somewhere and every page needs the same one.

Escaping rule: `esc` is applied to every value that came from outside this
module. `tag` does NOT escape its body, because a body is already-rendered
HTML; whoever builds that body escapes the text going into it. Use `text`
for that when you are not already assembling escaped pieces by hand - it
is the same rule with no way to forget the escaping.
"""
from __future__ import annotations

from html import escape

# (slug, label, group) for the nine areas, in the order they appear in the
# menu. The group is the heading a slug sits under.
AREAS: list[tuple[str, str, str]] = [
    ("overview", "Overview", "The router"),
    ("providers", "Providers & keys", "The router"),
    ("models", "Models", "The router"),
    ("buckets", "Buckets", "The router"),
    ("requests", "Requests", "Traffic"),
    ("broken", "What's broken", "Traffic"),
    ("brain", "Error brain", "Traffic"),
    ("allowance", "Allowance", "Traffic"),
    ("settings", "Settings", "System"),
]


def esc(value: object) -> str:
    """HTML-escape any value, quotes included. `None` becomes empty."""
    if value is None:
        return ""
    return escape(str(value), quote=True)


def text(*parts: object) -> str:
    """Escape each part and join them. Use this for anything from outside."""
    return "".join(esc(p) for p in parts)


def attrs(mapping: dict) -> str:
    """Render HTML attributes, leading space included when non-empty.

    `None` and `False` drop the attribute entirely; `True` renders it bare.
    """
    out = []
    for name, value in mapping.items():
        if value is None or value is False:
            continue
        if value is True:
            out.append(f" {esc(name)}")
        else:
            out.append(f' {esc(name)}="{esc(value)}"')
    return "".join(out)


def tag(name: str, body: str = "", **kw) -> str:
    """One element. `cls=` renders as `class=`. The body is NOT escaped."""
    if "cls" in kw:
        kw["class"] = kw.pop("cls")
    return f"<{name}{attrs(kw)}>{body}</{name}>"


def _nav(current: str) -> str:
    out = []
    seen_group = None
    for slug, label, group in AREAS:
        if group != seen_group:
            out.append(tag("h2", esc(group), cls="nav-group"))
            seen_group = group
        here = slug == current
        out.append(tag(
            "a", esc(label),
            # The Overview lives at the root, not at /overview, so that the
            # address the owner is given is just the service's own address.
            href="/" if slug == "overview" else f"/{slug}",
            **{"aria-current": "page" if here else None},
        ))
    return tag("nav", "".join(out), cls="nav")


def page(title: str, current: str, body: str) -> str:
    """A complete document. `body` is already-rendered HTML."""
    return (
        "<!doctype html>"
        '<html lang="en">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)} - flexrouter</title>"
        '<link rel="stylesheet" href="/wire.css">'
        "</head>"
        "<body>"
        + tag("div", _nav(current) + tag("main", body, cls="main"), cls="shell")
        + "</body></html>"
    )
