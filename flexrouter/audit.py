from __future__ import annotations
import csv
import json
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

_HEADERS = ["timestamp", "tier", "provider", "model",
            "prompt_tokens", "completion_tokens", "cost_usd", "latency_ms",
            "status", "request_id"]

# `request_id` was added after the first release. It is last so that anything
# reading the older nine columns by position still lines up, and
# `_migrate_header` below brings an existing file forward rather than writing
# ten values under a nine-column header.
_LEGACY_HEADERS = _HEADERS[:-1]


class AuditLogger:
    def __init__(self, state_dir: str) -> None:
        self._dir = Path(state_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._csv_path = self._dir / "audit.csv"
        self._health_path = self._dir / "health.json"
        self._recent: deque[dict] = deque(maxlen=500)
        self._total_cost: float = 0.0
        self._provider_cost: dict[str, float] = {}
        self._session_start = datetime.now(timezone.utc).isoformat()

        # Write CSV header if new file
        if not self._csv_path.exists():
            with self._csv_path.open("w", newline="") as f:
                csv.DictWriter(f, fieldnames=_HEADERS).writeheader()
        else:
            self._migrate_header()

    def _migrate_header(self) -> None:
        """Bring a pre-`request_id` audit file up to the current columns.

        Without this, an existing log keeps its nine-column header while
        every new row carries ten values, and `DictReader` starts filing
        the tenth under the key `None`. Rewriting once on startup is
        cheap and leaves the old rows readable, with an empty request id -
        which is the truth about them: those requests were never traced.
        """
        with self._csv_path.open(newline="") as f:
            first = f.readline()
        if not first.strip():
            with self._csv_path.open("w", newline="") as f:
                csv.DictWriter(f, fieldnames=_HEADERS).writeheader()
            return

        header = next(csv.reader([first]), [])
        if header == _HEADERS or "request_id" in header:
            return
        if header != _LEGACY_HEADERS:
            # Someone else's columns. Leave the file alone rather than
            # rewriting a file this class does not understand.
            return

        with self._csv_path.open(newline="") as f:
            rows = list(csv.DictReader(f))
        tmp = self._csv_path.with_suffix(".migrating")
        with tmp.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_HEADERS)
            w.writeheader()
            for row in rows:
                row.setdefault("request_id", "")
                w.writerow(row)
        os.replace(tmp, self._csv_path)

    def log(
        self,
        tier: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        latency_ms: int,
        status: str,
        request_id: str = "",
    ) -> None:
        """One attempt.

        `request_id` is the trace id of the request this attempt belongs to,
        so several rows can be recognised as one request that failed over.
        It defaults to empty rather than being required: a caller that
        forgets should lose a statistic, not raise mid-request.
        """
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tier": tier,
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            "status": status,
            "request_id": request_id,
        }
        with self._csv_path.open("a", newline="") as f:
            csv.DictWriter(f, fieldnames=_HEADERS).writerow(row)

        # Only update in-memory state after successful disk write
        self._recent.append(row)
        self._total_cost += cost_usd
        self._provider_cost[provider] = self._provider_cost.get(provider, 0.0) + cost_usd
        self._write_health()

    def recent_logs(self, n: int = 50) -> list[dict]:
        entries = list(self._recent)
        return entries[-n:]

    def snapshot(self) -> dict:
        return {
            "total_cost_usd": round(self._total_cost, 6),
            "session_start": self._session_start,
            "providers": {
                p: {"daily_cost_usd": round(c, 6)}
                for p, c in self._provider_cost.items()
            },
        }

    def _write_health(self) -> None:
        tmp = self._health_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.snapshot(), indent=2))
        os.replace(tmp, self._health_path)
