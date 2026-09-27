"""What's broken: what only the owner can fix, and what flexrouter is
already handling. Each card says what is wrong in plain words and carries
the one button that fixes it; "handling it" cards count down to recovery.
"""
from __future__ import annotations

from urllib.parse import quote

from flexrouter.dashboard import explain, facts, ui
from flexrouter.dashboard.overview import KIND_LABELS, clock
from flexrouter.dashboard.render import esc, tag

# kind -> (button label, where it goes). The provider is filled in per card.
_FIX = {
    "provider_needs_you": ("Open provider", "/providers/{p}"),
    "key_needs_you": ("Replace key", "/providers/{p}"),
    "unclear_error": ("Review in Error brain", "/brain"),
    "model_needs_you": ("See models", "/models_catalog"),
}


def _explain_button(prompt: str, label: str, ident: str, small: bool = False) -> str:
    """A copy-prompt button (§8) and the prompt it copies, kept hidden."""
    if not prompt:
        return ""
    src = tag("pre", esc(prompt), id=ident, hidden="")
    return src + ui.button(label, kind="copy", icon_name="" if small else "copy",
                           **{"data-copy": f"#{ident}",
                              "title": "Copy a prompt that explains this to a chatbot"})


def _card(item, prompt: str = "", n: int = 0) -> str:
    where = item.provider or ""
    if item.detail:
        where = f"{where} - {item.detail}" if where else item.detail
    head = tag("div",
               tag("span", esc(KIND_LABELS.get(item.kind, item.kind)), cls="card-kind")
               + (tag("span", esc(where), cls="card-where") if where else ""),
               cls="card-head")
    since = tag("span", esc(f"since {clock(item.since)}"), cls="n") if item.since else ""
    fix = ""
    if item.kind in _FIX:
        label, href = _FIX[item.kind]
        fix = ui.button(label, href=href.format(p=quote(item.provider or "")),
                        kind="primary" if item.pile == "you" else "ghost")
    wait = ui.countdown(item.until, prefix="Back in") if item.until else ""
    ask = _explain_button(prompt, "Explain with AI", f"explain-{n}", small=True)
    foot = tag("div", since + wait + tag("span", "", cls="spacer") + ask + fix, cls="card-foot")
    key = f"{item.kind}:{item.provider}:{item.detail}"
    return tag("div", head + tag("p", esc(item.reason), cls="card-why") + foot,
               cls=f"card card-{item.pile}", **{"data-row": key, "data-value": item.reason})


def _column(title: str, items: list, empty: str, pile: str, prompts: dict) -> str:
    body = ("".join(_card(i, prompts.get(id(i), ""), n=f"{pile}-{k}")
                    for k, i in enumerate(items)) if items else ui.empty(empty))
    count = tag("span", esc(len(items)), cls=f"col-count col-count-{pile}")
    return tag("section", tag("div", tag("h2", esc(title)) + count, cls="col-head")
               + tag("div", body, cls="col-body"),
               cls=f"col col-{pile}", **{"data-enter": ""})


def inner(router) -> str:
    data = facts.broken(router)
    you, service = data["needs_you"], data["handling_itself"]
    if not you and not service:
        return tag("div",
                   tag("div", tag("span", "●", cls="clear-dot") + tag("h2", "All clear"),
                       cls="clear-head")
                   + tag("p", "Every provider is answering and nothing is waiting on you. "
                              "Problems show up here the moment they happen.", cls="dim"),
                   cls="all-clear", **{"data-enter": ""})
    prompts = _prompts(router, you + service)
    everything = explain.build_prompt(router, traces=prompts.pop("traces"))
    top = tag("div", tag("span", "", cls="spacer")
              + _explain_button(everything, "Explain errors with AI", "explain-all"),
              cls="explain-bar") if everything else ""
    return top + tag("div",
                     _column("Needs you", you, "Nothing needs you.", "you", prompts)
                     + _column("Handling it", service, "Nothing in progress.", "service",
                               prompts),
                     cls="cols")


def _prompts(router, items: list) -> dict:
    """One prompt per explainable row, keyed by id(item); traces read once."""
    traces = facts._read_traces(router, 5000)
    wanted = {explain.row_key(i) for i in explain.problems(router)}
    out: dict = {"traces": traces}
    for item in items:
        if explain.row_key(item) in wanted and not item.kind.startswith("key"):
            out[id(item)] = explain.build_prompt(router, only=explain.row_key(item),
                                                 traces=traces)
    return out


def body(router) -> str:
    data = facts.broken(router)
    n = len(data["needs_you"])
    status = (f"{n} thing{'s' if n != 1 else ''} need{'s' if n == 1 else ''} you"
              if n else "Nothing needs you right now.")
    head = tag("div", tag("div", tag("h1", "What's broken", cls="page-title")
                          + tag("p", esc(status), cls="page-status"), cls="page-head-text"),
               cls="page-head")
    return head + tag("div", inner(router), id="broken", **{
        "data-live": "", "hx-get": "/broken?fragment=1",
        "hx-trigger": "every 10s [!document.hidden]", "hx-swap": "morph:innerHTML"})
