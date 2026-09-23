"""Where the dashboard's static files live, and how pages link to them.

Every link carries a hash of the file's content, so a browser never keeps
an old `app.css` after an upgrade and never re-downloads an unchanged one.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"


@lru_cache(maxsize=None)
def _digest(path: Path, mtime_ns: int) -> str:
    # mtime is part of the cache key only so an edited file gets re-hashed.
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def asset_url(rel: str) -> str:
    """`/static/<rel>?v=<hash>` for a file under `static/`."""
    path = STATIC_DIR / rel
    if not path.is_file():
        raise FileNotFoundError(path)
    return f"/static/{rel}?v={_digest(path, path.stat().st_mtime_ns)}"
