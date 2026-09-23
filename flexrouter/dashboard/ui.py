"""The dashboard's building blocks.

Every function takes plain values, escapes any text it is given, and
returns HTML. Pages compose these instead of hand-building markup, so the
look lives in static/app.css and the structure lives here.
"""
from __future__ import annotations

import math

from flexrouter.dashboard.render import attrs, esc, tag

# Lucide icons (ISC licence, https://lucide.dev): the inner markup of a
# 24x24 stroke icon. Only the icons the dashboard actually uses.
ICONS: dict[str, str] = {
    "gauge": '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
    "plug": '<path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/>'
            '<path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>',
    "boxes": '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>'
             '<rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
    "layers": '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/>'
              '<path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/>'
              '<path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
    "arrows": '<path d="m16 3 4 4-4 4"/><path d="M20 7H4"/><path d="m8 21-4-4 4-4"/><path d="M4 17h16"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
             '<path d="M12 9v4"/><path d="M12 17h.01"/>',
    "brain": '<path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/>'
             '<path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/>',
    "meter": '<path d="M3 3v18h18"/><path d="M7 16v-4"/><path d="M11 16V8"/>'
             '<path d="M15 16v-6"/><path d="M19 16V5"/>',
    "terminal": '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
    "settings": '<path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>'
               '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
}

_STATUS = {
    "ok": ("●", "OK"), "warn": ("◆", "ATTENTION"), "bad": ("▲", "BROKEN"),
    "idle": ("○", "IDLE"), "none": ("○", "NO TRAFFIC"),
}


def box(title: str, body: str, *, sub: str = "", action: str = "",
        id: str | None = None, cls: str = "", **extra) -> str:
    """A bordered block with a mono title bar. `body`/`action` are HTML."""
    head = tag("h2", esc(title))
    if sub:
        head += tag("span", esc(sub), cls="box-sub")
    if action:
        head += tag("div", action, cls="box-action")
    return tag("section",
               tag("div", head, cls="box-head") + tag("div", body, cls="box-body"),
               cls=f"box {cls}".strip(), id=id, **extra)


def stat(label: str, value: str, *, key: str, note: str = "",
         trend: str = "", spark: str = "") -> str:
    """One headline figure. `data-value` is what the live refresh compares
    to decide whether this figure changed and should flash."""
    inner = (tag("span", esc(label), cls="stat-label")
             + tag("b", esc(value), cls="stat-value", **{"data-value": value})
             + (tag("div", spark, cls="stat-spark") if spark else "")
             + (tag("small", esc(note), cls=f"stat-note {trend}".strip()) if note else ""))
    return tag("div", inner, cls="stat", **{"data-stat": key})


def meter(fraction: float | None, *, cells: int = 20, label: str = "",
          share: bool = False) -> str:
    """The block meter: `cells` squares, lit up to `fraction`.

    Anything above zero lights at least one cell - a nearly-empty meter
    that shows nothing reads as "unused", which is a different fact.
    `None` means the total isn't known, and the meter claims nothing.

    `share=True` is for a comparison (this bucket against the busiest one),
    not a limit: a full meter there is not a warning, so it never turns
    amber or red.
    """
    common = f'role="meter" aria-valuemin="0" aria-valuemax="100" aria-label="{esc(label)}"'
    if fraction is None:
        return f'<div class="meter" {common} data-level="none">{"<i></i>" * cells}</div>'
    f = min(max(float(fraction), 0.0), 1.0)
    on = min(cells, math.ceil(f * cells)) if 0 < f < 1 / cells else round(f * cells)
    level = "share" if share else "full" if f >= 1 else "warn" if f >= 0.8 else "ok"
    pct = round(f * 100)
    body = '<i class="on"></i>' * on + "<i></i>" * (cells - on)
    return (f'<div class="meter" {common} aria-valuenow="{pct}" '
            f'data-level="{level}" data-value="{pct}">{body}</div>')


def tag_(text: str, kind: str = "") -> str:
    """A small mono label, e.g. VISION or PROVIDER SAYS."""
    return tag("span", esc(text), cls=f"tag {kind}".strip())


def status(state: str) -> str:
    """A state as glyph + word, never colour alone."""
    key = state if state in _STATUS else "idle"
    glyph, word = _STATUS[key]
    return tag("span", f"{glyph} {word}", cls=f"status status-{key}")


def button(label: str, *, href: str | None = None, kind: str = "",
           icon_name: str = "", **kw) -> str:
    body = (icon(icon_name) if icon_name else "") + tag("span", esc(label))
    cls = f"btn {kind}".strip()
    if href is not None:
        return f'<a class="{cls}" href="{esc(href)}"{attrs(kw)}>{body}</a>'
    return f'<button type="button" class="{cls}"{attrs(kw)}>{body}</button>'


def sheet(title: str, body: str, *, close_href: str, sub: str = "") -> str:
    """The side panel: slides in from the right over a dimmed page.

    Closing is a real link back to the page without the panel, so it works
    with or without JavaScript and the back button does the same thing.
    """
    close = (f'<a class="sheet-close" href="{esc(close_href)}" aria-label="Close" '
             f'data-sheet-close>{icon("x")}</a>')
    head = tag("div",
               tag("div", tag("h2", esc(title), id="sheet-title")
                   + (tag("p", esc(sub), cls="sheet-sub") if sub else ""))
               + close,
               cls="sheet-head")
    return (f'<a class="sheet-backdrop" href="{esc(close_href)}" tabindex="-1" '
            f'aria-hidden="true" data-sheet-close></a>'
            + tag("aside", head + tag("div", body, cls="sheet-body"),
                  cls="sheet", role="dialog", **{"aria-modal": "true",
                                                 "aria-labelledby": "sheet-title"}))


def empty(message: str, *, action: str = "") -> str:
    """What a block says when it has nothing to show, and what to do next."""
    return tag("div", tag("p", esc(message)) + action, cls="empty")


def icon(name: str) -> str:
    ICONS[name]  # an unknown icon is a bug; fail loudly rather than draw nothing
    return f'<svg class="icon" aria-hidden="true"><use href="#i-{name}"/></svg>'


def sprite() -> str:
    """Every icon as a <symbol>, once per page, for `icon()` to point at."""
    symbols = "".join(
        f'<symbol id="i-{n}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="1.75" stroke-linecap="square" stroke-linejoin="miter">{d}</symbol>'
        for n, d in ICONS.items())
    return f'<svg width="0" height="0" style="position:absolute" aria-hidden="true">{symbols}</svg>'
