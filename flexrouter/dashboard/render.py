"""HTML building blocks for the dashboard, plus the site's own menu.

Deliberately not a template engine: the markup stays semantic and the
whole of its appearance lives in static/app.css. Most of this module - `esc`,
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

# (slug, label, group, icon) for the nine areas, in the order they appear in
# the menu. The group is the heading a slug sits under; the icon is a name
# in `ui.ICONS`.
AREAS: list[tuple[str, str, str, str]] = [
    ("overview", "Overview", "The router", "gauge"),
    ("providers", "Providers & keys", "The router", "plug"),
    ("models", "Models", "The router", "boxes"),
    ("buckets", "Buckets", "The router", "layers"),
    ("requests", "Requests", "Traffic", "arrows"),
    ("broken", "What's broken", "Traffic", "alert"),
    ("brain", "Error brain", "Traffic", "brain"),
    ("allowance", "Allowance", "Traffic", "meter"),
    ("settings", "Settings", "System", "settings"),
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


def _version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version
        return version("flexrouter")
    except (ImportError, PackageNotFoundError):
        return ""


def _brand() -> str:
    mark = tag("span", "<i></i><i></i><i></i><i></i>", cls="mark", **{"aria-hidden": "true"})
    word = tag("span", tag("span", "flex", cls="wm-flex") + tag("span", "router", cls="wm-router"),
               cls="wordmark")
    return tag("a", mark + word, href="/", cls="nav-brand", **{"aria-label": "flexrouter home"})


def _nav(current: str, badges: dict) -> str:
    from flexrouter.dashboard import ui  # ui imports this module
    out = []
    seen_group = None
    for slug, label, group, icon in AREAS:
        if group != seen_group:
            out.append(tag("h2", esc(group), cls="nav-group"))
            seen_group = group
        here = slug == current
        count = badges.get(slug) or 0
        badge = tag("span", esc(count), cls="nav-badge") if count else ""
        out.append(tag(
            "a", ui.icon(icon) + tag("span", esc(label)) + badge,
            # The Overview lives at the root, not at /overview, so that the
            # address the owner is given is just the service's own address.
            href="/" if slug == "overview" else f"/{slug}",
            cls="nav-link",
            **{"aria-current": "page" if here else None},
        ))
    out.append(tag("span", "", cls="nav-marker", **{"aria-hidden": "true"}))
    version = _version()
    foot = tag("div",
               tag("span", "", cls="live-dot") + tag("span", "live")
               + (tag("span", esc(f"v{version}")) if version else "")
               + tag("kbd", "Ctrl K"),
               cls="nav-foot")
    return tag("nav", _brand() + tag("div", "".join(out), cls="nav-links") + foot, cls="nav")


def page(title: str, current: str, body: str, *, badges: dict | None = None) -> str:
    """A complete document. `body` is already-rendered HTML.

    Scripts are deferred, so they never hold up the first paint; the page
    is complete HTML before any of them runs.
    """
    from flexrouter.dashboard import assets, prefs, ui  # both import this module
    motion = prefs.load().motion
    scripts = "".join(
        f'<script src="{assets.asset_url(rel)}" defer></script>'
        for rel in ("vendor/htmx.min.js", "vendor/idiomorph-ext.min.js",
                    "vendor/motion.js", "app.js"))
    head = (
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)} - flexrouter</title>"
        # Marks JavaScript as present before the first paint, so blocks that
        # are about to animate in are never shown and then hidden again.
        "<script>document.documentElement.classList.add('js')</script>"
        f'<link rel="preload" href="{assets.asset_url("fonts/GeistMono.woff2")}" '
        'as="font" type="font/woff2" crossorigin>'
        f'<link rel="stylesheet" href="{assets.asset_url("app.css")}">'
        + scripts
    )
    return (
        f'<!doctype html><html lang="en" data-motion="{esc(motion)}">'
        f"<head>{head}</head>"
        '<body hx-boost="true" hx-ext="morph">'
        + ui.sprite()
        + tag("div", _nav(current, badges or {}) + tag("main", body, cls="main"), cls="shell")
        + '<div class="toasts" id="toasts" aria-live="polite"></div>'
        + "</body></html>"
    )
