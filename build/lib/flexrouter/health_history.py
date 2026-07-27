from __future__ import annotations
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class HealthHistory:
    def __init__(self, state_dir: str, retention_days: int = 30) -> None:
        self._dir = Path(state_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "health_history.jsonl"
        self._retention_days = retention_days

    def record(self, sample: dict) -> None:
        if "timestamp" not in sample:
            sample = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), **sample}
        with self._path.open("a") as f:
            f.write(json.dumps(sample) + "\n")

    def read(self, since_iso: Optional[str] = None) -> list[dict]:
        if not self._path.exists():
            return []
        cutoff = _parse(since_iso) if since_iso else None
        out = []
        for line in self._path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # never crash on a torn line; skip it
            if cutoff and _parse(obj["timestamp"]) < cutoff:
                continue
            out.append(obj)
        return out

    def latest(self) -> Optional[dict]:
        samples = self.read()
        return samples[-1] if samples else None

    def compact(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        retention_cutoff = now - timedelta(days=self._retention_days)
        downsample_cutoff = now - timedelta(hours=24)
        kept: list[dict] = []
        last_bucket: Optional[int] = None
        for obj in self.read():
            ts = _parse(obj["timestamp"])
            if ts < retention_cutoff:
                continue
            if ts < downsample_cutoff:
                bucket = int(ts.timestamp()) // 300  # one per 5 min
                if bucket == last_bucket:
                    continue
                last_bucket = bucket
            kept.append(obj)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(o) + "\n" for o in kept))
        os.replace(tmp, self._path)
