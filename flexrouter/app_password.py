"""The app password: what an app must send to use flexrouter's /v1.

Two sources, in this order (ADR 0015):
  1. one generated from the dashboard, stored in keys.json like every other
     credential (masked, owner-only file permissions);
  2. `auth_token` in the owner's settings file, as before.

A generated password is never typed in: `generate()` makes a random one
and hands it back exactly once. Anything that could reach the dashboard
could otherwise set a password it already knows.
"""
from __future__ import annotations

import secrets
from typing import Optional

from flexrouter.keys import KeyRecord, load_keys, save_keys

# A pseudo-provider in keys.json. Never a routing target: the router only
# ever looks up providers that are actually configured.
NAME = "flexrouter-app-password"


def current(path=None) -> Optional[str]:
    """The generated password, or None if none has been generated."""
    records = load_keys(path).get(NAME) or []
    return records[0].secret if records else None


def generate(path=None) -> str:
    """A new random password, replacing any previous generated one."""
    secret = "fr-" + secrets.token_urlsafe(32)
    mapping = load_keys(path)
    mapping[NAME] = [KeyRecord(id=f"{NAME}-1", secret=secret, label="app password")]
    save_keys(mapping, path)
    return secret


def clear(path=None) -> None:
    """Forget the generated password; the settings file's auth_token (if
    any) applies again."""
    mapping = load_keys(path)
    if mapping.pop(NAME, None) is not None:
        save_keys(mapping, path)


def effective(settings_token: Optional[str], path=None) -> Optional[str]:
    """The password /v1 checks against: generated first, settings second."""
    return current(path) or settings_token
