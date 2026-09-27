"""Undo for the dashboard's dangerous buttons (US-87).

A dangerous action runs at once and the page offers Undo for a few
seconds instead of asking "are you sure?" first. Before the action writes,
`snapshot()` keeps the exact bytes of every file it is about to change;
`restore()` puts them back. Held in memory only, for a minute: an undo is
for the mistake you just made, not a history.
"""
from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Iterable, Optional

TTL_SECONDS = 60.0
_MAX = 20

# token -> (made at, {path: bytes, or None when the file didn't exist})
_stash: dict[str, tuple[float, dict[Path, Optional[bytes]]]] = {}


def _prune(now: float) -> None:
    for token in [t for t, (at, _) in _stash.items() if now - at > TTL_SECONDS]:
        del _stash[token]
    while len(_stash) > _MAX:
        del _stash[min(_stash, key=lambda t: _stash[t][0])]


def snapshot(paths: Iterable[Path | str]) -> str:
    """Remember these files as they are now; returns the token for Undo."""
    now = time.time()
    _prune(now)
    saved: dict[Path, Optional[bytes]] = {}
    for p in paths:
        path = Path(p)
        saved[path] = path.read_bytes() if path.exists() else None
    token = secrets.token_urlsafe(9)
    _stash[token] = (now, saved)
    return token


def restore(token: str) -> bool:
    """Put the files back. False when the token is unknown or too old."""
    _prune(time.time())
    held = _stash.pop(token, None)
    if held is None:
        return False
    for path, data in held[1].items():
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return True
