"""Reset one provider's models - or every provider's - and everything
flexrouter learned about them, for the dashboard's Danger zone.

"Models" are the dashboard-added bucket entries and per-model field edits
in overrides.json, plus parked non-chat models. "What it learned" is every
state file keyed by "provider/model": scores and their source, rate limits
(learned and recorded), capability facts, quarantine and penalties, and the
catalogue's pending suggestions (keyed by provider).

Never touched: keys and key health, providers, settings, buckets, and the
request history (traces, audit, events, the Error brain). config.yaml is
never written - the dashboard doesn't edit it anywhere.

Every file that is about to change is copied to state/backups/reset-<time>/
first, so a reset can be undone by hand.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flexrouter import home
from flexrouter.store import read_json, write_json

# State files keyed by "provider/model".
MODEL_KEYED = ("score_facts", "rate_limit_facts", "model_facts", "rate_limits",
               "quarantine", "penalties", "parked_models")
# State files keyed by provider.
PROVIDER_KEYED = ("catalog_pending",)


@dataclass
class ResetResult:
    counts: dict = field(default_factory=dict)
    backup_dir: Optional[Path] = None


def matches(key: str, provider: Optional[str]) -> bool:
    """Does a "provider/model" (or bare provider) key belong to `provider`?
    None means every provider."""
    return provider is None or key == provider or key.startswith(provider + "/")


def _plan(state_dir: str, overrides_path, provider: Optional[str]):
    """(new file contents by path, counts) - nothing written."""
    ov_path = Path(overrides_path) if overrides_path else home.overrides_path()
    changes: dict[Path, object] = {}
    counts = {"models": 0, "field_edits": 0, "facts": 0}

    ov = read_json(ov_path, default={}) or {}
    new_models = {bucket: [m for m in ms if not matches(m.get("provider", ""), provider)]
                  for bucket, ms in (ov.get("new_models") or {}).items()}
    edits = {k: v for k, v in (ov.get("models") or {}).items() if not matches(k, provider)}
    counts["models"] = (sum(len(ms) for ms in (ov.get("new_models") or {}).values())
                        - sum(len(ms) for ms in new_models.values()))
    counts["field_edits"] = len(ov.get("models") or {}) - len(edits)
    if counts["models"] or counts["field_edits"]:
        changes[ov_path] = {**ov, "new_models": new_models, "models": edits}

    for name in MODEL_KEYED + PROVIDER_KEYED:
        path = Path(state_dir) / f"{name}.json"
        data = read_json(path, default=None)
        if not isinstance(data, dict):
            continue
        kept = {k: v for k, v in data.items() if not matches(k, provider)}
        if len(kept) != len(data):
            counts["facts"] += len(data) - len(kept)
            changes[path] = kept
    return changes, counts


def preview(state_dir: str, overrides_path=None, provider: Optional[str] = None) -> dict:
    return _plan(state_dir, overrides_path, provider)[1]


def reset(state_dir: str, overrides_path=None, provider: Optional[str] = None) -> ResetResult:
    changes, counts = _plan(state_dir, overrides_path, provider)
    if not changes:
        return ResetResult(counts)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = Path(state_dir) / "backups" / f"reset-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    for path in changes:
        shutil.copy2(path, backup / path.name)
    for path, data in changes.items():
        write_json(path, data)
    return ResetResult(counts, backup)


def forget_live(router, provider: Optional[str]) -> None:
    """Drop the running router's in-memory quarantine and penalties for
    `provider`: PenaltyBox is loaded once at startup, and would otherwise
    write the old entries straight back on its next save."""
    box = router._penalties
    for store in (box._state, box._quarantine):
        for key in [k for k in store if matches(k, provider)]:
            del store[key]
