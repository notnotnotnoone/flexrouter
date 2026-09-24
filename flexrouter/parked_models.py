"""state/parked_models.json: models the "Add models with AI" flow (see
flexrouter/dashboard/add_models.py) learned about but can never route to.

A bucket only ever holds `chat` models (flexrouter/engine.py picks one to
answer a `router.generate()` call) - an embedding, speech_to_text,
text_to_speech or image model has nowhere to go, and must never be put in
one anyway (a bucket that mixes kinds would let the engine "select" a
model that cannot answer a chat request at all). This file is where those
rows land instead, with every field the AI gave for them, so the owner can
see what's known and nothing pasted back is silently thrown away.

Keyed by `provider/model`, the same identity everything else in flexrouter
uses. Saving an already-parked identity again overwrites its old fields -
this is a set of facts, not a log.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from flexrouter.store import harden, read_json, write_json


def _path(state_dir: str) -> Path:
    p = Path(state_dir) / "parked_models.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load(state_dir: str) -> dict:
    return read_json(_path(state_dir), default={})


def _save(state_dir: str, data: dict) -> None:
    p = _path(state_dir)
    write_json(p, data)
    harden(p)


def add(state_dir: str, provider: str, model: str, fields: dict) -> bool:
    """Save (or overwrite) one parked model. Returns True if this identity
    was already parked (an update), False if it's new."""
    data = load(state_dir)
    ident = f"{provider}/{model}"
    existed = ident in data
    entry = {"provider": provider, "model": model, **fields}
    data[ident] = entry
    _save(state_dir, data)
    return existed


def get(state_dir: str, provider: str, model: str) -> Optional[dict]:
    return load(state_dir).get(f"{provider}/{model}")


def all(state_dir: str) -> list[dict]:
    return sorted(load(state_dir).values(), key=lambda e: (e["provider"], e["model"]))
