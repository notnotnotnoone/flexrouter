from __future__ import annotations
import json
import os
import time
from pathlib import Path

_WINDOWS_SECONDS: dict[str, int] = {
    "rps": 1, "rph": 3600, "rpd": 86400,
    "tps": 1, "tph": 3600, "tpd": 86400,
}


class QuotaTracker:
    """Tracks per-provider/model request *and* token counts against
    configured rps/rph/rpd/tps/tph/tpd limits, persisted to
    <state_dir>/quotas.json. Survives process restarts, unlike
    RoutingEngine's in-memory SlidingWindow (rpm/tpm), which only ever
    covers one short rolling window.

    Each call to `record()` appends one `[timestamp, tokens]` event per
    provider/model. A quota key starting with `r` (rps/rph/rpd) is checked
    by counting events in its window; one starting with `t` (tps/tph/tpd)
    is checked by summing the `tokens` field of events in that window -
    same stored events, two ways of reading them.
    """

    def __init__(self, state_dir: str) -> None:
        self._path = Path(state_dir) / "quotas.json"
        self._state: dict[str, list[list[float]]] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                state: dict[str, list[list[float]]] = {}
                for key, events in raw.items():
                    normalized = []
                    for event in events:
                        if isinstance(event, (list, tuple)) and len(event) >= 2:
                            normalized.append([float(event[0]), int(event[1])])
                        else:
                            # Pre-existing quotas.json from before token
                            # tracking: a bare timestamp, no token count.
                            normalized.append([float(event), 0])
                    state[key] = normalized
                self._state = state
            except (json.JSONDecodeError, OSError, ValueError, TypeError, IndexError):
                self._state = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state))
        os.replace(tmp, self._path)

    @staticmethod
    def _key(provider: str, model: str) -> str:
        return f"{provider}/{model}"

    def record(self, provider: str, model: str, tokens: int = 0) -> None:
        key = self._key(provider, model)
        now = time.time()
        max_window = max(_WINDOWS_SECONDS.values())
        cutoff = now - max_window
        events = [e for e in self._state.get(key, []) if e[0] > cutoff]
        events.append([now, tokens])
        self._state[key] = events
        self._save()

    @staticmethod
    def _amount_in_window(q_type: str, events: list[list[float]]) -> int:
        """Requests (`r...`) count events; tokens (`t...`) sum them."""
        if q_type.startswith("t"):
            return sum(tokens for _, tokens in events)
        return len(events)

    def is_available(self, provider: str, model: str, quotas: dict[str, int]) -> bool:
        if not quotas:
            return True
        key = self._key(provider, model)
        now = time.time()
        events = self._state.get(key, [])
        for q_type, limit in quotas.items():
            window = _WINDOWS_SECONDS.get(q_type)
            if window is None:
                continue
            cutoff = now - window
            in_window = [e for e in events if e[0] > cutoff]
            if self._amount_in_window(q_type, in_window) >= limit:
                return False
        return True

    def seconds_until_available(self, provider: str, model: str, quotas: dict[str, int]) -> float:
        if not quotas:
            return 0.0
        key = self._key(provider, model)
        now = time.time()
        events = sorted(self._state.get(key, []), key=lambda e: e[0])
        wait = 0.0
        for q_type, limit in quotas.items():
            window = _WINDOWS_SECONDS.get(q_type)
            if window is None:
                continue
            cutoff = now - window
            in_window = [e for e in events if e[0] > cutoff]
            if q_type.startswith("t"):
                remaining = sum(tokens for _, tokens in in_window)
                if remaining < limit:
                    continue
                # Tokens fall out of the window oldest-first; find the
                # moment enough of them have expired to be under limit
                # again.
                for ts, tokens in in_window:
                    remaining -= tokens
                    if remaining < limit:
                        wait = max(wait, (ts + window) - now)
                        break
            else:
                if len(in_window) < limit:
                    continue
                oldest = in_window[0][0]
                wait = max(wait, (oldest + window) - now)
        return max(0.0, wait)
