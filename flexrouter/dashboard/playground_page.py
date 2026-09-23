"""The Playground: talk to a bucket or one model through flexrouter itself.

Laid out like an AI provider's playground - settings on the left, chat on
the right. Messages go through the same in-process path as
/v1/chat/completions (app._stream_chat), so they are real traffic: they
show up on Requests, the Overview and Allowance like any app's. After the
answer, one extra event says who answered, so each reply can carry an
"answered by" strip that opens the request's journey.
"""
from __future__ import annotations

from flexrouter.dashboard import ui
from flexrouter.dashboard.render import esc, tag


def _targets(router) -> str:
    opts = '<option value="auto">auto (best bucket)</option>'
    opts += "".join(f'<option value="{esc(b)}">bucket: {esc(b)}</option>'
                    for b in router._cfg.tiers)
    seen = set()
    for models in router._cfg.tiers.values():
        for mc in models:
            ident = f"{mc.provider}/{mc.model}"
            if ident not in seen:
                seen.add(ident)
                opts += f'<option value="{esc(ident)}">model: {esc(ident)}</option>'
    return f'<select name="target" id="pg-target" data-remember>{opts}</select>'


def _field(label: str, control: str, hint: str = "") -> str:
    return tag("label",
               tag("span", esc(label), cls="pg-label") + control
               + (tag("small", esc(hint), cls="pg-hint") if hint else ""),
               cls="pg-field")


def body(router) -> str:
    head = tag("div", tag("div", tag("h1", "Playground", cls="page-title")
                          + tag("p", "Real requests through flexrouter. They count like any "
                                     "app's.", cls="page-status"), cls="page-head-text"),
               cls="page-head")
    settings = tag("form",
                   _field("Send to", _targets(router),
                          "A bucket tries its models in order; a model is tried alone.")
                   + _field("System prompt",
                            '<textarea name="system" id="pg-system" rows="5" data-remember '
                            'placeholder="Describe how the model should behave"></textarea>')
                   + _field("Temperature",
                            '<div class="pg-range"><input type="range" name="temperature" '
                            'id="pg-temp" min="0" max="2" step="0.1" value="0.7" data-remember>'
                            '<output for="pg-temp" id="pg-temp-out">0.7</output></div>')
                   + _field("Max tokens",
                            '<input type="number" name="max_tokens" id="pg-max" min="1" '
                            'max="32768" value="1024" data-remember>'),
                   cls="pg-settings", id="pg-settings", onsubmit="return false",
                   **{"hx-boost": "false"})
    chat = tag("div",
               tag("div",
                   ui.empty("Your conversation will appear here. Nothing is saved: "
                            "clearing or leaving the page forgets it."),
                   cls="pg-log", id="pg-log", **{"aria-live": "polite"})
               + tag("form",
                     '<textarea id="pg-input" rows="3" placeholder="Ask anything" '
                     'aria-label="Message"></textarea>'
                     + tag("div",
                           tag("span", "Enter to send, Shift+Enter for a new line", cls="n")
                           + tag("span", "", cls="spacer")
                           + tag("button", "Clear", type="button", id="pg-clear", cls="ghost-btn")
                           + tag("button", "Send", type="submit", id="pg-send", cls="primary-btn"),
                           cls="pg-actions"),
                     cls="pg-composer", id="pg-composer",
                     # app.js sends this itself; htmx must not turn it into a navigation
                     **{"hx-boost": "false"}),
               cls="pg-chat")
    return head + tag("div", ui.box("Settings", settings, **{"data-enter": ""}) + chat,
                      cls="pg-layout")
