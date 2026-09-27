"""The Allowance page: how much free-tier headroom is left, and when it comes back.

grill-decisions.md §19: one group per provider and no grand total, so a
14,400-a-day Gemma can't swamp twenty-a-day Geminis in one big number. Each
group says when that provider's day starts over, in your own time, and one
row per real limit. Our own counts include failed attempts (the provider
counts them too) and say so; a figure the provider reported is shown as the
provider's, because it's the truth. Every group ends with the reminder that
flexrouter only counts what it sent.
"""
from __future__ import annotations

from datetime import datetime

from flexrouter.dashboard import facts, ui
from flexrouter.dashboard.overview import num
from flexrouter.dashboard.render import esc, tag


def _local_clock(epoch: float) -> str:
    """The reset as a local 24-hour clock, like every other time shown."""
    return datetime.fromtimestamp(epoch).strftime("%H:%M")


def _nice_date(iso: str) -> str:
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.day} {d.strftime('%b')} {d.year}"


def _limit_row(model: str, lim: dict, label: str, counts_failed) -> str:
    frac = lim["used"] / lim["limit"] if lim["limit"] else None
    unit_word = "tokens" if lim["unit"] == "tokens" else "requests"
    window = "today" if lim["window"] == "day" and lim["aligned"] else f"per {lim['window']}"
    figs = f"{num(lim['used'])} / {num(lim['limit'])}"
    if lim.get("failed"):
        figs += tag("small", f"{lim['failed']} failed")
    note = ""
    if lim["frees_at"] and lim["used"] >= lim["limit"]:
        note = ("Used up. flexrouter skips it until "
                + ("the reset. " if lim["aligned"] else "one frees up. ")
                + ui.countdown(lim["frees_at"], prefix="in"))
    elif lim.get("failed"):
        why = (f"{esc(label)} counts failed tries too" if counts_failed
               else "Failed tries are included, in case they count")
        note = (f"{why}: {lim['failed']} of these {num(lim['used'])} failed. "
                + tag("span", "our count", cls="lim-src"))
    return tag("div",
               tag("div", tag("span", esc(model), cls="lim-model")
                   + tag("span", f"{unit_word} {window}", cls="lim-window"), cls="lim-name")
               + ui.meter(frac, label=f"{model} {unit_word} {window}")
               + tag("div", figs, cls="lim-figs")
               + (tag("div", note, cls="lim-note") if note else ""),
               cls="lim-row", **{"data-row": f"lim:{model}:{lim['unit']}:{lim['window']}",
                                 "data-value": lim["used"]})


def _says_row(model: str, says: dict, label: str) -> str:
    return tag("div",
               tag("div", tag("span", esc(model), cls="lim-model")
                   + tag("span", esc(f"{says['what']} left"), cls="lim-window"), cls="lim-name")
               + ui.meter(None, label=f"{model} {says['what']} left")
               + tag("div", f"{num(says['remaining'])} left", cls="lim-figs")
               + tag("div", f"{esc(label)} sends what's left with every reply, so this is "
                            f"{esc(label)}'s number. "
                     + tag("span", f"{esc(label)} says", cls="lim-src theirs")
                     + (" " + ui.countdown(says["resets_at"], prefix="resets in")
                        if says["resets_at"] else ""),
                     cls="lim-note"),
               cls="lim-row", **{"data-row": f"says:{model}:{says['what']}",
                                 "data-value": says["remaining"]})


def _reset_line(g: dict) -> str:
    if g["next_reset"]:
        return tag("div",
                   tag("span", f"Resets at {esc(g['reset_words'])}, "
                       + tag("b", esc(_local_clock(g["next_reset"]))) + " your time")
                   + ui.countdown(g["next_reset"], prefix="in"),
                   cls="allow-reset")
    return tag("div", tag("span", "Rolling: each request frees up a day after it was sent."),
               cls="allow-reset")


def _foot(g: dict) -> str:
    bits = ["Counts only what flexrouter sent."]
    if g["counts_failed"]:
        bits.append(f"{esc(g['label'])} counts failed tries too.")
    elif g["counts_failed"] is False:
        bits.append(f"{esc(g['label'])} doesn't count failed tries.")
    if g["limits_url"]:
        bits.append(tag("a", f"{esc(g['label'])}'s own numbers ↗", href=g["limits_url"],
                        target="_blank", rel="noopener"))
    bits.append(f"checked {esc(_nice_date(g['checked']))}" if g["checked"]
                else "provider facts not checked yet")
    return tag("p", " ".join(bits[:-1]) + " · " + bits[-1], cls="allow-foot")


def _provider_box(g: dict) -> str:
    busy, quiet = [], []
    for m in g["models"]:
        rows = "".join(_limit_row(m["model"], lim, g["label"], g["counts_failed"])
                       for lim in m["limits"])
        rows += "".join(_says_row(m["model"], says, g["label"]) for says in m["provider_says"])
        if m["exhausted_until"]:
            rows += tag("div",
                        tag("span", "▲ USED UP", cls="status status-bad")
                        + tag("span", esc(f"{m['model']}: {g['label']} says there is nothing "
                                          "left; flexrouter is sending these requests to the "
                                          "next model in the bucket"), cls="dim")
                        + ui.countdown(m["exhausted_until"], prefix="Back in"),
                        cls="lim-out")
        if not rows:
            continue
        used = any(lim["used"] for lim in m["limits"]) or m["provider_says"] \
            or m["exhausted_until"]
        (busy if used else quiet).append((m["model"], rows))
    if not busy and not quiet:
        body = tag("p", "No limits known for these models yet. Set a cap on Models, or send "
                        "a request and the provider's own numbers appear here.", cls="note")
    else:
        body = "".join(rows for _, rows in busy)
        if quiet:
            if busy:
                names = " · ".join(name for name, _ in quiet[:3])
                body += ui.fold(f"{len(quiet)} more model{'s' if len(quiet) != 1 else ''}",
                                "".join(rows for _, rows in quiet), note=names)
            else:
                body += "".join(rows for _, rows in quiet)
    keys = f"{g['keys']} key" + ("" if g["keys"] == 1 else "s")
    sub = f"{g['limit_scope']} · {keys}" if g["limit_scope"] else keys
    return ui.box(g["label"], _reset_line(g) + body + _foot(g), sub=sub,
                  cls="allow-group", **{"data-enter": "", "data-box": f"prov-{g['provider']}"})


def body(router) -> str:
    groups = facts.allowance_groups(router)
    head = tag("div", tag("div", tag("h1", "Allowance", cls="page-title")
                          + tag("p", "Free-tier headroom per provider, and when it comes back.",
                                cls="page-status"), cls="page-head-text"),
               cls="page-head")
    if not groups:
        return head + ui.empty("No models configured yet.",
                               action=ui.button("Add a provider", href="/providers", kind="primary"))
    return (head
            + tag("div", inner(router), id="allow", cls="ov",
                  **{"data-live": "", "hx-get": "/allowance?fragment=1",
                     "hx-trigger": "every 15s [!document.hidden]",
                     "hx-swap": "morph:innerHTML"}))


def inner(router) -> str:
    groups = facts.allowance_groups(router)
    return tag("div", "".join(_provider_box(g) for g in groups), cls="allow-grid")
