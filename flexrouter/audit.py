from __future__ import annotations
import csv
import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

_HEADERS = ["timestamp", "tier", "provider", "model",
            "prompt_tokens", "completion_tokens", "cost_usd", "latency_ms", "status"]


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
    ) -> None:
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
        }
        with self._csv_path.open("a", newline="") as f:
            csv.DictWriter(f, fieldnames=_HEADERS).writerow(row)

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
        self._health_path.write_text(json.dumps(self.snapshot(), indent=2))
