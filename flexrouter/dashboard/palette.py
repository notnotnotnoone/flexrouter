"""What Ctrl+K can find: every page, provider, model, bucket, setting and
action, as one list. The browser does the searching; this only says what
exists and where each thing lives.
"""
from __future__ import annotations

from urllib.parse import quote

from flexrouter.dashboard.render import _page_href, areas
from flexrouter.dashboard.settings_page import META


def items(router) -> list[dict]:
    out: list[dict] = []
    for slug, label, _group, _icon in areas():
        out.append({"kind": "page", "label": label, "href": _page_href(slug)})
    for name in sorted(router._cfg.providers):
        out.append({"kind": "provider", "label": name, "href": f"/providers/{quote(name)}"})
    seen = set()
    for bucket, models in router._cfg.tiers.items():
        out.append({"kind": "bucket", "label": bucket, "href": "/buckets"})
        for mc in models:
            ident = f"{mc.provider}/{mc.model}"
            if ident not in seen:
                seen.add(ident)
                out.append({"kind": "model", "label": ident, "href": "/models_catalog"})
    for name, (_group, label, *_rest) in META.items():
        out.append({"kind": "setting", "label": label, "hint": name,
                    "href": f"/settings#set-{name}"})
    out += [
        {"kind": "action", "label": "Add a provider", "href": "/providers"},
        {"kind": "action", "label": "Show failed requests", "href": "/requests?result=failed"},
        {"kind": "action", "label": "Show failovers", "href": "/requests?result=failover"},
        {"kind": "action", "label": "Make a new app password", "href": "/settings#g-app-password"},
        {"kind": "action", "label": "Download a settings backup", "href": "/settings/backup",
         "download": True},
        {"kind": "action", "label": "Rank models with an AI", "href": "/models_catalog/rank"},
    ]
    if router._cfg.experimental_model_discovery:
        out.append({"kind": "action", "label": "Check for new models",
                    "href": "/settings#g-about"})
    return out
