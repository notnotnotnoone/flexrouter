"""Credentials for services the router calls on its own behalf - not chat
providers, so they don't belong on the Providers & keys page, but the
storage flexrouter/keys.py already built for a provider key is exactly
right for these too: masked everywhere, never re-shown in full, owner-only
file permissions.

Lookup order matches PLAN-V2.md §1's rule for every key in this app -
typed into the dashboard (which lands in the keys file) first, the
environment variable second, first hit wins. Before this module existed,
AA_API_KEY only ever read the environment, which quietly broke that rule
for the one service key the app actually used.

Each entry here is a pseudo-provider in keys.json (name "aa", "decider"),
never an entry in `flexrouter._cfg.providers` - resolve_keys() and the
router's own key-state machinery only ever look up providers that are
actually configured, so an extra name in keys.json for a service that
isn't a routing target is simply never touched by any of that.
"""
from __future__ import annotations

import os
from typing import Optional

from flexrouter.keys import KeyRecord, add_key, load_keys, save_keys

# name -> (label, environment variable fallback)
SERVICES: dict[str, tuple[str, str]] = {
    "aa": ("Artificial Analysis (scores newly discovered models)", "AA_API_KEY"),
    "decider": ("Decider (classifies unfamiliar provider errors; set "
                "decider_base_url and decider_model in Settings too)",
                "DECIDER_API_KEY"),
}


def resolve(name: str, path=None) -> Optional[str]:
    """The one key to use for `name`: dashboard/keys-file first, its
    environment variable second, `None` if neither is set."""
    vault = load_keys(path)
    for record in vault.get(name) or []:
        if record.enabled and record.secret:
            return record.secret
    _, env_var = SERVICES.get(name, (None, None))
    return os.environ.get(env_var) if env_var else None


def current_record(name: str, path=None) -> Optional[KeyRecord]:
    records = load_keys(path).get(name) or []
    return records[0] if records else None


def set_key(name: str, secret: str, path=None) -> KeyRecord:
    """Replace whatever key this service has on file - there is only ever
    one, unlike a provider's rotation of several keys, so this clears any
    existing record rather than appending another one."""
    mapping = load_keys(path)
    mapping[name] = []
    save_keys(mapping, path)
    return add_key(name, secret, path=path)


def clear_key(name: str, path=None) -> bool:
    mapping = load_keys(path)
    had_one = bool(mapping.get(name))
    mapping[name] = []
    save_keys(mapping, path)
    return had_one
