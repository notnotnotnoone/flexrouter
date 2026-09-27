"""Who you can add, and what filling in their form looks like.

This replaced `catalogue.PROVIDERS`, a Python list holding a callable.
That shape had two costs: the dashboard could not render it (a function
does not serialise), and the owner could not add an entry without
editing installed source. A preset is data now, and `catalogue.py` reads
this module rather than the other way round.

A preset is a *template*, not a contract. Everything it carries fills in
a form the owner can then edit; nothing here is enforced at routing
time. The one field that is genuinely a capability is `models_path`:
`None` means this provider has no model list we know how to read, and
the dashboard hides discovery rather than offering a button that cannot
work.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path
from typing import Optional

from flexrouter import home


@dataclass(frozen=True)
class Preset:
    name: str
    label: str
    base_url: str
    signup_url: str = ""
    models_path: Optional[str] = None
    header_parser: str = "openai_compatible"
    free: bool = True
    free_filter: str = "all"
    # Provider facts (grill-decisions.md §19): shipped, hand-maintained, and
    # dated. `checked` is when someone last compared them with the
    # provider's own pages; None means nobody has yet.
    key_url: str = ""
    rate_limit_page_url: str = ""
    docs_url: str = ""
    # "rolling", or "HH:MM Area/City" when the provider's day starts over
    # at a fixed time (Google: midnight Pacific). See flexrouter/resets.py.
    daily_reset: str = "rolling"
    # Whether failed attempts count toward the provider's limits. None is
    # unknown - not the same as no.
    counts_failed_requests: Optional[bool] = None
    limit_scope: str = ""  # per key / per project / per account ...
    reports_remaining_per_day: bool = False
    known_quirks: tuple[str, ...] = ()
    free_tier_note: str = ""
    checked: Optional[str] = None


def _coerce(name: str, raw: dict) -> Preset:
    """Build one Preset, letting every absent field fall to its default.

    `models_path` is read explicitly so an empty string collapses to
    `None`: a preset that says `""` means "no discovery", and treating it
    as a path would send a request to the bare base URL.
    """
    path = (raw.get("models_path") or "").strip()
    return Preset(
        name=name,
        label=str(raw.get("label") or name),
        base_url=str(raw.get("base_url") or ""),
        signup_url=str(raw.get("signup_url") or ""),
        models_path=path or None,
        header_parser=str(raw.get("header_parser") or "openai_compatible"),
        free=bool(raw.get("free", True)),
        free_filter=str(raw.get("free_filter") or "all"),
        key_url=str(raw.get("key_url") or ""),
        rate_limit_page_url=str(raw.get("rate_limit_page_url") or ""),
        docs_url=str(raw.get("docs_url") or ""),
        daily_reset=str(raw.get("daily_reset") or "rolling"),
        counts_failed_requests=(None if raw.get("counts_failed_requests") is None
                                else bool(raw["counts_failed_requests"])),
        limit_scope=str(raw.get("limit_scope") or ""),
        reports_remaining_per_day=bool(raw.get("reports_remaining_per_day", False)),
        known_quirks=tuple(str(q) for q in (raw.get("known_quirks") or ())),
        free_tier_note=str(raw.get("free_tier_note") or ""),
        checked=raw.get("checked") or None,
    )


def shipped() -> dict[str, Preset]:
    """The presets that come with flexrouter. Never written to."""
    raw = json.loads(
        resources.files("flexrouter.data").joinpath("presets.json")
        .read_text(encoding="utf-8")
    )
    return {name: _coerce(name, body) for name, body in raw.items()}


def _owner_raw(path=None) -> tuple[dict, list[str]]:
    """Load the owner's presets file, reporting file-level problems."""
    target = Path(path) if path else home.presets_path()
    if not target.exists():
        return {}, []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        return {}, [f"{target.name}: {e}"]
    return (raw if isinstance(raw, dict) else {}), []


def _merge(base: dict[str, Preset], raw: dict) -> tuple[dict[str, Preset], list[str]]:
    """Layer the owner's file over the shipped set, entry by entry.

    A bad entry is dropped and named, never raised: this file is
    hand-edited, and one typo must not take the Providers page down with
    it. The shipped set is what remains, which is always a working app.
    """
    out = dict(base)
    problems: list[str] = []
    for name, body in raw.items():
        if not isinstance(body, dict):
            problems.append(f"{name}: expected a block of fields")
            continue
        try:
            if name in out:
                # A patch, not a replacement - see the test.
                merged = {**asdict(out[name]), **body}
                merged.pop("name", None)
                out[name] = _coerce(name, merged)
            else:
                out[name] = _coerce(name, body)
        except (TypeError, ValueError) as e:
            problems.append(f"{name}: {e}")
    return out, problems


def all(path=None) -> dict[str, Preset]:
    owner, _ = _owner_raw(path)
    found, _ = _merge(shipped(), owner)
    return found


def problems(path=None) -> list[str]:
    owner, file_problems = _owner_raw(path)
    _, merge_problems = _merge(shipped(), owner)
    return file_problems + merge_problems


def get(name: str, path=None) -> Optional[Preset]:
    return all(path).get(name)
