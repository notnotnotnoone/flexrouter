"""state/score_facts.json: where each model's current score came from.

Separate from model_facts.py's capability facts - a score is not a learned
capability, it is a ranking decision, and this file only ever records two
things about it: who set it last (`manual`, from the dashboard's own model
edit form; or `ai-ranked`, from Stage 8's copy-paste ranking flow) and when.
A model with no entry here has a score that came from config.yaml or an
`add_model()` call and has never been touched by either of those.

`is_manual()` is what the ranking flow (flexrouter/dashboard/ranking.py)
checks before proposing a new score for a model - a score the owner set by
hand is left alone, the same rule model_facts.py already applies to
capabilities (spec §4b, ADR 0013).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flexrouter.store import harden, read_json, write_json


@dataclass
class ScoreSource:
    source: str  # manual | ai-ranked
    at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(state_dir: str) -> Path:
    p = Path(state_dir) / "score_facts.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load(state_dir: str) -> dict[str, ScoreSource]:
    raw = read_json(_path(state_dir), default={})
    return {ident: ScoreSource(**entry) for ident, entry in (raw or {}).items()}


def _save(state_dir: str, data: dict[str, ScoreSource]) -> None:
    p = _path(state_dir)
    write_json(p, {ident: asdict(s) for ident, s in data.items()})
    harden(p)


def record(state_dir: str, provider: str, model: str, source: str,
          at: Optional[str] = None) -> None:
    data = load(state_dir)
    data[f"{provider}/{model}"] = ScoreSource(source=source, at=at or _now_iso())
    _save(state_dir, data)


def is_manual(state_dir: str, provider: str, model: str) -> bool:
    entry = load(state_dir).get(f"{provider}/{model}")
    return entry is not None and entry.source == "manual"


def get(state_dir: str, provider: str, model: str) -> Optional[ScoreSource]:
    return load(state_dir).get(f"{provider}/{model}")
