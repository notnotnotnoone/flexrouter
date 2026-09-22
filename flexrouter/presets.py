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
from dataclasses import dataclass
from importlib import resources
from typing import Optional


@dataclass(frozen=True)
class Preset:
    name: str
    label: str
    base_url: str
    signup_url: str = ""
    models_path: Optional[str] = None
    header_parser: str = "openai_compatible"
    free: bool = True
    seed_rpm: int = 30
    seed_tpm: int = 60_000
    free_filter: str = "all"


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
        seed_rpm=int(raw.get("seed_rpm", 30)),
        seed_tpm=int(raw.get("seed_tpm", 60_000)),
        free_filter=str(raw.get("free_filter") or "all"),
    )


def shipped() -> dict[str, Preset]:
    """The presets that come with flexrouter. Never written to."""
    raw = json.loads(
        resources.files("flexrouter.data").joinpath("presets.json")
        .read_text(encoding="utf-8")
    )
    return {name: _coerce(name, body) for name, body in raw.items()}
