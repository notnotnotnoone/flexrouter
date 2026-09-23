"""state/rate_limit_facts.json: where each model's configured rpm/tpm came
from.

Same shape and same reason as score_facts.py, one level over: this is not
about the numbers `RateLimitStore` learns from live response headers (those
always win when present, regardless of this file - see rate_limits.py), it
is about the rpm/tpm sitting in config/overrides.json, which starts as a
preset's seed and can be hand-edited on the Models page or proposed by the
docs-reading AI on `/models/rate-limits`. `manual` means the owner typed it
in; `ai-doc` means the AI proposed it from pasted provider docs and the
owner accepted it. A model with no entry here has never had either happen -
its rpm/tpm is still whatever the preset seeded.

`is_manual()` is what `/models/rate-limits/proposal` checks before proposing
a new value for a model - a limit the owner set by hand is left alone, same
rule as `score_facts.is_manual()` for the ranking flow.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flexrouter.store import harden, read_json, write_json


@dataclass
class RateLimitSource:
    source: str  # manual | ai-doc
    at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(state_dir: str) -> Path:
    p = Path(state_dir) / "rate_limit_facts.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load(state_dir: str) -> dict[str, RateLimitSource]:
    raw = read_json(_path(state_dir), default={})
    return {ident: RateLimitSource(**entry) for ident, entry in (raw or {}).items()}


def _save(state_dir: str, data: dict[str, RateLimitSource]) -> None:
    p = _path(state_dir)
    write_json(p, {ident: asdict(s) for ident, s in data.items()})
    harden(p)


def record(state_dir: str, provider: str, model: str, source: str,
          at: Optional[str] = None) -> None:
    data = load(state_dir)
    data[f"{provider}/{model}"] = RateLimitSource(source=source, at=at or _now_iso())
    _save(state_dir, data)


def is_manual(state_dir: str, provider: str, model: str) -> bool:
    entry = load(state_dir).get(f"{provider}/{model}")
    return entry is not None and entry.source == "manual"


def get(state_dir: str, provider: str, model: str) -> Optional[RateLimitSource]:
    return load(state_dir).get(f"{provider}/{model}")
