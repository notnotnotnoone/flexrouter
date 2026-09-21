"""One JSON object per request, appended to state/traces.jsonl.

This is the foundation for the dashboard, the error brain, and the attribute
corrections (spec section 3). engine.py already computes exactly why each
model was passed over (`_skip_reason`, called by `explain_unavailable`) and
V1 discarded the answer — this module only records it; `_router.py` is what
calls `explain_unavailable`.

Every text field is scrubbed here, on the way in, the same discipline
`redact.py` already established for the penalty box: nothing unscrubbed is
ever written to disk, which means the spec's "verbatim provider text" cannot
be taken literally (see ADR 0010, ruling 2).

Rotation is daily-file rather than health_history.py's downsampling: the
live file is always traces.jsonl ("today"); the first write after midnight
UTC renames yesterday's content to traces-YYYY-MM-DD.jsonl and anything
older than the retention window is deleted. Downsampling makes sense for a
health metric sampled many times a second; it would silently thin out
individual requests here, which is the one thing this stage exists to keep.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from flexrouter.redact import scrub

_DATED_FILE = re.compile(r"^traces-(\d{4}-\d{2}-\d{2})\.jsonl$")


def new_trace_id() -> str:
    return "req_" + uuid.uuid4().hex[:24]


def _scrub_entry(entry: dict) -> dict:
    """A deep copy of `entry` with every known text field scrubbed.

    Round-trips through json rather than a manual deep copy: every value
    this module is ever asked to write is itself JSON (it is about to be
    written as a JSON line), so this is exact and needs no recursion of its
    own to maintain.
    """
    out = json.loads(json.dumps(entry))
    for skip in out.get("skipped") or []:
        if "detail" in skip:
            skip["detail"] = scrub(skip["detail"])
    for att in out.get("attempts") or []:
        if "provider_message" in att and att["provider_message"]:
            att["provider_message"] = scrub(att["provider_message"])
    return out


class TraceWriter:
    def __init__(self, state_dir: str, retention_days: int = 30) -> None:
        self._dir = Path(state_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "traces.jsonl"
        self._retention_days = retention_days

    def _rotate_if_new_day(self, now: datetime) -> None:
        if not self._path.exists():
            return
        last_write = datetime.fromtimestamp(self._path.stat().st_mtime, tz=timezone.utc)
        if last_write.date() == now.date():
            return
        dated = self._dir / f"traces-{last_write.date().isoformat()}.jsonl"
        if not dated.exists():
            os.replace(self._path, dated)
        else:
            # Two rotations landed on the same dated name (a restart after a
            # long-untouched file, say) — append rather than clobber a day's
            # existing archive.
            with dated.open("a", encoding="utf-8") as dst, \
                    self._path.open("r", encoding="utf-8") as src:
                dst.write(src.read())
            self._path.unlink()
        self.compact(now=now)

    def compact(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=self._retention_days)).date()
        for f in self._dir.glob("traces-*.jsonl"):
            m = _DATED_FILE.match(f.name)
            if not m:
                continue
            try:
                day = date.fromisoformat(m.group(1))
            except ValueError:
                continue
            if day < cutoff:
                f.unlink(missing_ok=True)

    def write(self, entry: dict) -> None:
        now = datetime.now(timezone.utc)
        self._rotate_if_new_day(now)
        scrubbed = _scrub_entry(entry)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(scrubbed, ensure_ascii=False) + "\n")
