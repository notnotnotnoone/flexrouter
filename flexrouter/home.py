"""The one fixed place every setting and credential lives.

There is no cwd search chain. Each consuming project used to end up with its
own flexrouter.yaml and its own copy of the keys; that is fault 1 in the v2
spec, and deleting the search is the fix.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PORT = 4891

STARTER_CONFIG = """\
# flexrouter settings.
#
# This file is yours. flexrouter never rewrites it, so your comments and
# layout survive forever. Anything you change in the dashboard is saved
# separately in overrides.json and layered on top of this at load time.
#
# Your API keys do NOT belong in here. They live in keys.json next door,
# locked to your user account. Add one with:  flexrouter keys add <provider>

settings:
  port: 4891
  # Optional. Set this and every app pointed at flexrouter must send it as
  # its API key. Leave it out and anything on this machine can use the
  # service. Your provider keys do not go here - they live in keys.json.
  # auth_token: pick-something-long

# Add providers here, or let the daily catalogue check discover their models.
providers: {}

# Buckets are the names your code asks for, e.g. router.generate(tier="smart").
buckets:
  smart: []
  fast: []
  long: []
"""


def home_dir() -> Path:
    """The flexrouter home. FLEXROUTER_HOME overrides it, always.

    An empty or whitespace-only FLEXROUTER_HOME counts as not set, and the
    platform default is used. `Path("")` is the current directory, so honouring
    an empty value would put the home wherever the process happened to be
    standing — the per-project layout this module exists to abolish.
    """
    env = (os.environ.get("FLEXROUTER_HOME") or "").strip()
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "flexrouter"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "flexrouter"
    return Path.home() / ".config" / "flexrouter"


def config_path() -> Path:
    return home_dir() / "config.yaml"


def keys_path() -> Path:
    return home_dir() / "keys.json"


def overrides_path() -> Path:
    return home_dir() / "overrides.json"


def state_dir() -> Path:
    return home_dir() / "state"


def ensure_home() -> Path:
    """Create the home and a starter settings file if they are not there yet."""
    root = home_dir()
    root.mkdir(parents=True, exist_ok=True)
    state_dir().mkdir(parents=True, exist_ok=True)
    cfg = config_path()
    if not cfg.exists():
        cfg.write_text(STARTER_CONFIG, encoding="utf-8")
    return root
