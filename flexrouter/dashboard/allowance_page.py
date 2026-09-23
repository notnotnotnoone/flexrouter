"""The Allowance page: how much free-tier headroom is left, and when it comes back.

Top: every provider's daily caps stacked into one bar - the whole pitch of
flexrouter, as a picture. Below: one box per provider with a block meter per
limit and a live countdown. Limits the owner set are metered exactly;
limits a provider reported are shown as "N left" with a countdown, because
providers rarely say out of how many.
"""
from __future__ import annotations

from flexrouter.dashboard import charts, facts, ui
from flexrouter.dashboard.overview import num
from flexrouter.dashboard.render import esc, tag


def _stack(stack: dict, colors: dict) -> str:
    if not stack["parts"]:
        return ui.box("Stacked daily headroom", ui.empty(
            "No daily caps set yet. Give a model a requests-per-day cap on Models "
            "and its headroom stacks up here.",
            action=ui.button("Set caps on Models", href="/models")),
            **{"data-enter": "", "data-box": "stack"})
    used_share = stack["total"] - stack["left"]
    bar = ui.stacked_bar([(p["provider"], p["left"], colors[p["provider"]]) for p in stack["parts"]],
                         empty_share=used_share)
    legend = "".join(
        tag("span", tag("span", "", cls="swatch", style=f"background:{colors[p['provider']]}")
            + tag("span", esc(p["provider"])) + tag("span", f"{num(p['left'])} / {num(p['total'])}",
                                                    cls="n"), cls="legend-item")
        for p in stack["parts"])
    plural = "" if stack["providers"] == 1 else "s"
    headline = tag("div",
                   tag("b", num(stack["left"]), cls="stack-big", **{"data-value": stack["left"]})
                   + tag("span", f"free requests left today across {stack['providers']} "
                                 f"provider{plural}", cls="stack-sub")
                   + tag("span", f"of {num(stack['total'])} a day", cls="n"),
                   cls="stack-head")
    return ui.box("Stacked daily headroom",
                  headline + bar + tag("div", legend, cls="legend stack-legend"),
                  sub="only daily caps you have set are counted",
                  **{"data-enter": "", "data-box": "stack", "data-stat": "stack-left"})


def _limit_row(model: str, lim: dict) -> str:
    frac = lim["used"] / lim["limit"] if lim["limit"] else None
    left = max(lim["limit"] - lim["used"], 0)
    note = (ui.countdown(lim["frees_at"], prefix="Frees up in") if lim["frees_at"]
            else tag("span", f"{num(left)} left · rolling {lim['window']}", cls="n"))
    return tag("div",
               tag("div", tag("span", esc(model), cls="lim-model")
                   + tag("span", f"per {lim['window']}", cls="lim-window"), cls="lim-name")
               + ui.meter(frac, label=f"{model} per {lim['window']}")
               + tag("span", f"{num(lim['used'])} / {num(lim['limit'])}", cls="lim-figs")
               + ui.tag_("your limit")
               + tag("div", note, cls="lim-note"),
               cls="lim-row", **{"data-row": f"lim:{model}:{lim['window']}",
                                 "data-value": lim["used"]})


def _says_row(model: str, says: dict) -> str:
    return tag("div",
               tag("div", tag("span", esc(model), cls="lim-model")
                   + tag("span", esc(says["what"]), cls="lim-window"), cls="lim-name")
               + ui.meter(None, label=f"{model} {says['what']}")
               + tag("span", f"{num(says['remaining'])} left", cls="lim-figs")
               + ui.tag_("provider says", "blue")
               + tag("div", ui.countdown(says["resets_at"]), cls="lim-note"),
               cls="lim-row", **{"data-row": f"says:{model}:{says['what']}",
                                 "data-value": says["remaining"]})


def _provider_box(g: dict, color: str) -> str:
    rows = ""
    exhausted = ""
    for m in g["models"]:
        for lim in m["limits"]:
            rows += _limit_row(m["model"], lim)
        for says in m["provider_says"]:
            rows += _says_row(m["model"], says)
        if m["exhausted_until"]:
            exhausted += tag("div",
                             tag("span", "▲ USED UP", cls="status status-bad")
                             + tag("span", esc(f"{m['model']}: the provider says there is nothing "
                                               "left; flexrouter is sending these requests to "
                                               "the next model in the bucket"), cls="dim")
                             + ui.countdown(m["exhausted_until"], prefix="Back in"),
                             cls="lim-out")
    if not rows:
        rows = tag("p", "No limits known for these models yet. Set a cap on Models, or send "
                        "a request and the provider's own numbers appear here.", cls="note")
    title = tag("span", "", cls="swatch", style=f"background:{color}")
    keys = f"{g['keys']} key" + ("" if g["keys"] == 1 else "s")
    return ui.box(g["provider"], exhausted + rows, sub=keys,
                  action=title, **{"data-enter": "", "data-box": f"prov-{g['provider']}"})


def body(router) -> str:
    groups = facts.allowance_groups(router)
    stack = facts.allowance_stack(router)
    status = (f"{num(stack['left'])} of {num(stack['total'])} daily requests left"
              if stack["total"] else "Free-tier headroom, and when it comes back.")
    head = tag("div", tag("div", tag("h1", "Allowance", cls="page-title")
                          + tag("p", esc(status), cls="page-status"), cls="page-head-text"),
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
    stack = facts.allowance_stack(router)
    colors = {g["provider"]: charts.color_for(i) for i, g in enumerate(groups)}
    return _stack(stack, colors) + tag("div", "".join(
        _provider_box(g, colors[g["provider"]]) for g in groups), cls="allow-grid")
