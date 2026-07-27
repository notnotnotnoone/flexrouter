from __future__ import annotations
import json
import os
import time
from pathlib import Path

_WINDOWS_SECONDS: dict[str, int] = {"rph": 3600, "rpd": 86400}


class QuotaTracker:
    """Tracks per-provider/model request counts against configured rpd/rph
    limits, persisted to <state_dir>/quotas.json. Survives process restarts,
    unlike RoutingEngine's in-memory SlidingWindow (rpm/tpm)."""

    def __init__(self, state_dir: str) -> None:
        self._path = Path(state_dir) / "quotas.json"
        self._state: dict[str, list[float]] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                self._state = {k: list(v) for k, v in raw.items()}
            except (json.JSONDecodeError, OSError, ValueError):
                self._state = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state))
        os.replace(tmp, self._path)

    @staticmethod
    def _key(provider: str, model: str) -> str:
        return f"{provider}/{model}"

    def record(self, provider: str, model: str) -> None:
        key = self._key(provider, model)
        now = time.time()
        max_window = max(_WINDOWS_SECONDS.values())
        cutoff = now - max_window
        timestamps = [t for t in self._state.get(key, []) if t > cutoff]
        timestamps.append(now)
        self._state[key] = timestamps
        self._save()

    def is_available(self, provider: str, model: str, quotas: dict[str, int]) -> bool:
        if not quotas:
            return True
        key = self._key(provider, model)
        now = time.time()
        timestamps = self._state.get(key, [])
        for q_type, limit in quotas.items():
            window = _WINDOWS_SECONDS.get(q_type)
            if window is None:
                continue
            cutoff = now - window
            count = sum(1 for t in timestamps if t > cutoff)
            if count >= limit:
                return False
        return True

    def seconds_until_available(self, provider: str, model: str, quotas: dict[str, int]) -> float:
        if not quotas:
            return 0.0
        key = self._key(provider, model)
        now = time.time()
        timestamps = sorted(self._state.get(key, []))
        wait = 0.0
        for q_type, limit in quotas.items():
            window = _WINDOWS_SECONDS.get(q_type)
            if window is None:
                continue
            cutoff = now - window
            in_window = [t for t in timestamps if t > cutoff]
            if len(in_window) >= limit:
                oldest = in_window[0]
                wait = max(wait, (oldest + window) - now)
        return max(0.0, wait)
