from __future__ import annotations
import csv
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

_EVENT_TYPES = {"penalized", "recovered", "rate_limited", "timeout", "server_error",
                "quarantined"}


class EventLogger:
    HEADERS = ["timestamp", "provider", "model", "event_type", "detail", "penalty_seconds"]

    def __init__(self, state_dir: str) -> None:
        self._dir = Path(state_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "events.csv"
        self._recent: deque[dict] = deque(maxlen=500)
        if not self._path.exists():
            with self._path.open("w", newline="") as f:
                csv.DictWriter(f, fieldnames=self.HEADERS).writeheader()

    def record(self, provider: str, model: str, event_type: str,
               detail: str = "", penalty_seconds: int = 0) -> None:
        if event_type not in _EVENT_TYPES:
            raise ValueError(f"Unknown event_type {event_type!r}; expected one of {sorted(_EVENT_TYPES)}")
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": provider,
            "model": model,
            "event_type": event_type,
            "detail": detail,
            "penalty_seconds": penalty_seconds,
        }
        with self._path.open("a", newline="") as f:
            csv.DictWriter(f, fieldnames=self.HEADERS).writerow(row)
        self._recent.append(row)

    def recent(self, n: int = 100) -> list[dict]:
        return list(self._recent)[-n:]
