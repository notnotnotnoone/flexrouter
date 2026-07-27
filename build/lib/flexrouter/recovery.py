from __future__ import annotations
import json
import os
import time
from pathlib import Path
from typing import Callable, Optional


class PenaltyBox:
    """Tracks per-(provider, model) exponential backoff penalties.

    Uses wall-clock time so penalties survive process restart when a
    state_dir is given. Emits 'penalized'/'recovered' events via on_event.
    """

    def __init__(self, base_seconds: int, max_seconds: int,
                 state_dir: Optional[str] = None,
                 on_event: Optional[Callable[[str, str, str, int], None]] = None) -> None:
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds
        self._on_event = on_event
        self._path = Path(state_dir) / "penalties.json" if state_dir else None
        # key -> (until: epoch float, count: int)
        self._state: dict[str, tuple[float, int]] = {}
        self._load()

    def _key(self, provider: str, model: str) -> str:
        return f"{provider}/{model}"

    def _split(self, key: str) -> tuple[str, str]:
        provider, _, model = key.partition("/")
        return provider, model

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except Exception:
            return
        now = time.time()
        for k, v in raw.items():
            until, count = float(v["until"]), int(v["count"])
            if until > now:  # drop already-expired penalties
                self._state[k] = (until, count)

    def _save(self) -> None:
        if not self._path:
            return
        data = {k: {"until": u, "count": c} for k, (u, c) in self._state.items()}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self._path)

    def _emit(self, provider: str, model: str, event_type: str, secs: int) -> None:
        if self._on_event:
            self._on_event(provider, model, event_type, secs)

    def penalize(self, provider: str, model: str) -> None:
        k = self._key(provider, model)
        _, count = self._state.get(k, (0.0, 0))
        count += 1
        secs = min(self.base_seconds * (2 ** (count - 1)), self.max_seconds)
        self._state[k] = (time.time() + secs, count)
        self._save()
        self._emit(provider, model, "penalized", int(secs))

    def penalize_short(self, provider: str, model: str, seconds: int) -> None:
        k = self._key(provider, model)
        _, count = self._state.get(k, (0.0, 0))
        self._state[k] = (time.time() + seconds, count)
        self._save()
        self._emit(provider, model, "penalized", int(seconds))

    def clear(self, provider: str, model: str) -> None:
        if self._state.pop(self._key(provider, model), None) is not None:
            self._save()
            self._emit(provider, model, "recovered", 0)

    def is_penalized(self, provider: str, model: str) -> bool:
        k = self._key(provider, model)
        if k not in self._state:
            return False
        until, _ = self._state[k]
        if time.time() >= until:
            del self._state[k]
            self._save()
            self._emit(*self._split(k), "recovered", 0)
            return False
        return True

    def penalty_seconds(self, provider: str, model: str) -> int:
        k = self._key(provider, model)
        if k not in self._state:
            return 0
        until, count = self._state[k]
        if time.time() >= until:
            return 0
        return int(min(self.base_seconds * (2 ** (count - 1)), self.max_seconds))

    def penalty_until(self, provider: str, model: str) -> Optional[float]:
        k = self._key(provider, model)
        if k not in self._state:
            return None
        until, _ = self._state[k]
        return until if time.time() < until else None

    def active_penalties(self) -> dict[str, dict]:
        now = time.time()
        return {k: {"until": u, "count": c}
                for k, (u, c) in self._state.items() if u > now}
