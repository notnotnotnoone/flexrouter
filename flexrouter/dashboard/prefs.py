"""The dashboard's own preferences, in <home>/dashboard.json.

Not router configuration, so it does not belong in overrides.json: how the
dashboard animates changes nothing about how requests are routed.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from flexrouter import home

MOTIONS = ("full", "reduced", "off")
# Must match pages.RANGES; a test holds the two together.
RANGE_KEYS = ("24h", "7d", "30d", "all")


@dataclass(frozen=True)
class Prefs:
    motion: str = "full"
    refresh_seconds: int = 10
    default_range: str = "24h"
    timezone: str = "local"


def _path(path: Path | None) -> Path:
    return Path(path) if path else home.home_dir() / "dashboard.json"


def problems(p: Prefs) -> list[str]:
    """What is wrong with `p`, in words; empty when it is fine."""
    out = []
    if p.motion not in MOTIONS:
        out.append(f"motion must be one of {', '.join(MOTIONS)}")
    if (not isinstance(p.refresh_seconds, int) or isinstance(p.refresh_seconds, bool)
            or not 2 <= p.refresh_seconds <= 3600):
        out.append("refresh_seconds must be a whole number from 2 to 3600")
    if p.default_range not in RANGE_KEYS:
        out.append(f"default_range must be one of {', '.join(RANGE_KEYS)}")
    if not isinstance(p.timezone, str) or not p.timezone.strip():
        out.append("timezone must be 'local' or an IANA name like Europe/London")
    return out


def load(path: Path | None = None) -> Prefs:
    """The saved preferences. A missing or damaged file, or any one bad
    value in it, falls back to the default for that value rather than
    breaking every page of the dashboard."""
    try:
        raw = json.loads(_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Prefs()
    if not isinstance(raw, dict):
        return Prefs()
    result = Prefs()
    for f in fields(Prefs):
        if f.name in raw:
            candidate = replace(result, **{f.name: raw[f.name]})
            if not problems(candidate):
                result = candidate
    return result


def save(prefs: Prefs, path: Path | None = None) -> None:
    found = problems(prefs)
    if found:
        raise ValueError("; ".join(found))
    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(prefs), indent=2), encoding="utf-8")
    os.replace(tmp, target)
