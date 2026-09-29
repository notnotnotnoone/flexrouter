"""What's broken: what only the owner can fix, and what flexrouter is
already handling. Each card says what is wrong in plain words and carries
the one button that fixes it; "handling it" cards count down to recovery.

An unclear error and a model that needs you are fixed right on their
card: Resolve opens the options, and one click saves the answer - a
verdict to the Error brain, or retry / turn off / use-the-new-name for
the model.
"""
from __future__ import annotations

from urllib.parse import quote

from flexrouter.decider import VERDICTS
from flexrouter.dashboard import explain, facts, ui
from flexrouter.dashboard.brain_page import LABELS as VERDICT_LABELS
from flexrouter.dashboard.overview import KIND_LABELS, clock
from flexrouter.dashboard.render import attrs, esc, tag

# kind -> (button label, where it goes). The provider is filled in per card.
_FIX = {
    "provider_needs_you": ("Open provider", "/providers/{p}"),
    "key_needs_you": ("Replace key", "/providers/{p}"),
    "model_needs_you": ("See models", "/models_catalog"),
}

_CHECK = ('<svg class="chip-check" viewBox="0 0 16 16" aria-hidden="true">'
          '<path d="M3.5 8.5l3 3 6-7" pathLength="1"/></svg>')


def _explain_button(prompt: str, label: str, ident: str, small: bool = False) -> str:
    """A copy-prompt button (§8) and the prompt it copies, kept hidden."""
    if not prompt:
        return ""
    src = tag("pre", esc(prompt), id=ident, hidden="")
    return src + ui.button(label, kind="copy" if not small else "ghost",
                           **{"data-copy": f"#{ident}",
                              "title": "Copy a prompt that explains this to a chatbot"})


def _button(field: str, value: str, body: str, guess: bool) -> str:
    """One option chip: a submit button carrying field=value."""
    extra = {"title": "flexrouter's best guess"} if guess else {}
    cls = "verdict-chip" + (" is-guess" if guess else "")
    # Written out, not tag(): `name` is tag()'s own first parameter.
    return (f'<button type="submit" name="{field}"'
            f'{attrs({"value": value, "class": cls, **extra})}>{body}{_CHECK}</button>')


def _hidden(field: str, value: str) -> str:
    return f'<input type="hidden"{attrs({"name": field, "value": value})}>'


def _star(on: bool) -> str:
    return tag("span", "★", cls="chip-star", **{"aria-hidden": "true"}) if on else ""


def _chip(verdict: str, guess: str, probs: dict) -> str:
    p = float(probs.get(verdict) or 0)
    body = tag("span", _star(verdict == guess) + esc(VERDICT_LABELS.get(verdict, verdict)),
               cls="chip-name")
    if p > 0:          # an answer the classifier never weighed shows no number
        body += tag("span", esc(f"{p:.0%}"), cls="chip-pct n")
        body += tag("span", tag("i", "", style=f"--p:{p:.3f}", **{"data-p": f"{p:.3f}"}),
                    cls="chip-bar", **{"aria-hidden": "true"})
    return _button("verdict", verdict, body, verdict == guess)


def _panel(ident: str, question: str, more: tuple, form: str, lead: str = "") -> str:
    """The fold-out under a card: a question, a way out, and the options."""
    head = tag("div", tag("span", esc(question), cls="resolver-q")
               + tag("span", "", cls="spacer")
               + tag("a", esc(more[0]), href=more[1], cls="resolver-more"),
               cls="resolver-head")
    return tag("div", tag("div", lead + head + form, cls="resolver"),
               id=ident, cls="resolver-wrap", inert="")


def _resolver(item, entry, ident: str) -> str:
    """The verdict chips for one unclear error, most likely first.

    Each chip is a submit button, so the form still saves without
    JavaScript (and lands back here); app.js turns the click into an
    in-place save.
    """
    probs = ((entry.decision or {}).get("probabilities") or {}) if entry else {}
    guess = entry.verdict if entry else ""
    order = sorted(VERDICTS, key=lambda v: (-float(probs.get(v) or 0), v != guess,
                                            VERDICTS.index(v)))
    # The card already quotes the short sample; the panel adds the full
    # provider response only when there is more to read.
    raw = entry.raw if entry and entry.raw and entry.raw != entry.sample else ""
    form = tag("form",
               '<input type="hidden" name="next" value="/broken">'
               + tag("div", "".join(_chip(v, guess, probs) for v in order),
                     cls="verdict-chips", role="group",
                     **{"aria-label": "What this error means"}),
               method="post", action=f"/brain/{quote(item.fp, safe='')}/verdict",
               cls="resolver-form", **{"data-resolve": ""})
    lead = tag("pre", esc(raw), cls="resolver-sample") if raw else ""
    return _panel(ident, "What does this mean?", ("Full details in Error brain", "/brain"),
                  form, lead)


# What a model that needs you can have done to it, right from its card.
# value -> (label, one line on what it does)
_MODEL_FIXES = {
    "retry": ("Try it again", "Back to Ready. The next request tests it."),
    "off": ("Turn it off", "Stop routing to it. Undo from Models."),
}


def _model_resolver(item, ident: str) -> str:
    """The options for one model that needs you, the likeliest fix first.

    A did-you-mean suggestion leads when there is one; a model whose
    provider isn't set up can't be retried, so it offers the setup page.
    """
    suggested = (item.action or "").removeprefix("use:") if (item.action or "").startswith("use:") else ""
    no_provider = item.cause == "no_provider"
    chips = []
    if suggested:
        chips.append(_button("do", "use", tag("span", _star(True) + "Use " + tag("span", esc(suggested), cls="chip-id")
                             + tag("span", "The provider's current name for it.", cls="chip-hint"),
                             cls="chip-name"), True))
    if no_provider:
        chips.append(tag("a", tag("span", _star(True) + "Set up " + tag("span", esc(item.provider), cls="chip-id")
                         + tag("span", "Add its key; the model starts working.", cls="chip-hint"),
                         cls="chip-name"),
                         href="/providers", cls="verdict-chip is-guess"))
    guess = "" if suggested or no_provider else ("off" if item.action == "remove" else "retry")
    for value in (("off",) if no_provider else ("retry", "off")):
        label, hint = _MODEL_FIXES[value]
        chips.append(_button("do", value, tag("span", _star(value == guess) + esc(label)
                             + tag("span", esc(hint), cls="chip-hint"), cls="chip-name"),
                             value == guess))
    if guess == "off":      # the likeliest fix goes first
        chips.reverse()
    form = tag("form",
               _hidden("provider", item.provider) + _hidden("model", item.model)
               + tag("div", "".join(chips), cls="verdict-chips is-wide", role="group",
                     **{"aria-label": "What to do about this model"}),
               method="post", action="/broken/model",
               cls="resolver-form", **{"data-resolve": ""})
    return _panel(ident, "What should happen to it?", ("Open in Models", "/models_catalog"), form)


def _card(item, prompt: str = "", n: int = 0, entry=None) -> str:
    where = item.provider or ""
    if item.detail:
        where = f"{where} - {item.detail}" if where else item.detail
    head = tag("div",
               tag("span", esc(KIND_LABELS.get(item.kind, item.kind)), cls="card-kind")
               + (tag("span", esc(where), cls="card-where") if where else ""),
               cls="card-head")
    since = tag("span", esc(f"since {clock(item.since)}"), cls="n") if item.since else ""
    fix = resolver = ""
    if item.kind in _FIX:
        label, href = _FIX[item.kind]
        fix = ui.button(label, href=href.format(p=quote(item.provider or "")),
                        kind="primary" if item.pile == "you" else "ghost")
    panel = f"resolve-{n}"
    if item.kind == "unclear_error" and item.fp:
        resolver = _resolver(item, entry, panel)
    elif item.kind == "model_needs_you" and item.model:
        resolver = _model_resolver(item, panel)
    if resolver:
        fix = ui.button("Resolve", kind="primary",
                        **{"data-resolve-toggle": "", "aria-expanded": "false",
                           "aria-controls": panel})
    wait = ui.countdown(item.until, prefix="Back in") if item.until else ""
    ask = _explain_button(prompt, "Explain with AI", f"explain-{n}", small=True)
    foot = tag("div", since + wait + tag("span", "", cls="spacer") + ask + fix, cls="card-foot")
    key = f"{item.kind}:{item.provider}:{item.detail}"
    return tag("div", head + tag("p", esc(item.reason), cls="card-why") + foot + resolver,
               cls=f"card card-{item.pile}", **{"data-row": key, "data-value": item.reason})


def _column(title: str, items: list, empty: str, pile: str, prompts: dict,
            entries: dict | None = None) -> str:
    entries = entries or {}
    body = ("".join(_card(i, prompts.get(id(i), ""), n=f"{pile}-{k}", entry=entries.get(i.fp))
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
                     _column("Needs you", you, "Nothing needs you.", "you", prompts,
                             router._error_brain._entries)
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


def body(router, banner: str = "") -> str:
    data = facts.broken(router)
    n = len(data["needs_you"])
    status = (f"{n} thing{'s' if n != 1 else ''} need{'s' if n == 1 else ''} you"
              if n else "Nothing needs you right now.")
    head = tag("div", tag("div", tag("h1", "What's broken", cls="page-title")
                          + tag("p", esc(status), cls="page-status"), cls="page-head-text"),
               cls="page-head")
    return head + banner + tag("div", inner(router), id="broken", **{
        "data-live": "", "hx-get": "/broken?fragment=1",
        "hx-trigger": "every 10s [!document.hidden]", "hx-swap": "morph:innerHTML"})
