from __future__ import annotations
import json
import os
import time
from pathlib import Path
from typing import Optional

from flexrouter import resets

_WINDOWS_SECONDS: dict[str, int] = {
    "rps": 1, "rpm": 60, "rph": 3600, "rpd": 86400,
    "tps": 1, "tpm": 60, "tph": 3600, "tpd": 86400,
}


class QuotaTracker:
    """Tracks per-provider/model request *and* token counts against
    configured rps/rpm/rph/rpd/tps/tpm/tph/tpd limits, persisted to
    <state_dir>/quotas.json. Survives process restarts, unlike
    RoutingEngine's in-memory SlidingWindow (rpm/tpm), which only ever
    covers one short rolling window.

    Each call to `record()` appends one `[timestamp, tokens]` event per
    provider/model. A quota key starting with `r` (rps/rph/rpd) is checked
    by counting events in its window; one starting with `t` (tps/tph/tpd)
    is checked by summing the `tokens` field of events in that window -
    same stored events, two ways of reading them.

    grill-decisions.md §19: every attempt the provider answered is an event,
    failures included (a third field, 1, marks them), and a daily cap
    (rpd/tpd) counts from the provider's own reset - midnight Pacific for
    Google - rather than the last 24 hours, when its preset names one.
    """

    def __init__(self, state_dir: str, daily_resets: Optional[dict[str, str]] = None) -> None:
        self._path = Path(state_dir) / "quotas.json"
        self._state: dict[str, list[list[float]]] = {}
        # provider -> presets.Preset.daily_reset ("rolling" or "HH:MM Zone")
        self.daily_resets: dict[str, str] = dict(daily_resets or {})
        self._load()

    def window_start(self, provider: str, q_type: str, now: float) -> float:
        """Where `q_type`'s window begins for this provider right now."""
        if q_type in ("rpd", "tpd"):
            start = resets.last_reset(self.daily_resets.get(provider), now)
            if start is not None:
                return start
        return now - _WINDOWS_SECONDS[q_type]

    def window_end(self, provider: str, q_type: str, event_ts: float, now: float) -> float:
        """When an event in `q_type`'s window stops counting."""
        if q_type in ("rpd", "tpd"):
            nxt = resets.next_reset(self.daily_resets.get(provider), now)
            if nxt is not None:
                return nxt
        return event_ts + _WINDOWS_SECONDS[q_type]

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                state: dict[str, list[list[float]]] = {}
                for key, events in raw.items():
                    normalized = []
                    for event in events:
                        if isinstance(event, (list, tuple)) and len(event) >= 3:
                            normalized.append([float(event[0]), int(event[1]), int(event[2])])
                        elif isinstance(event, (list, tuple)) and len(event) >= 2:
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

    # A key's own rps/rph/rpd/tps/tph/tpd cap (KeyRecord.quotas) is tracked
    # in the same store, under a namespace no real model name can collide
    # with, so it gets identical window logic without a second tracker.
    @staticmethod
    def _key_ns(key_id: str) -> str:
        return f"__key__:{key_id}"

    def key_is_available(self, provider: str, key_id: str, quotas: dict[str, int]) -> bool:
        return self.is_available(provider, self._key_ns(key_id), quotas)

    def key_seconds_until_available(self, provider: str, key_id: str,
                                     quotas: dict[str, int]) -> float:
        return self.seconds_until_available(provider, self._key_ns(key_id), quotas)

    def record_key(self, provider: str, key_id: str, tokens: int = 0,
                   failed: bool = False) -> None:
        self.record(provider, self._key_ns(key_id), tokens, failed=failed)

    def record(self, provider: str, model: str, tokens: int = 0, failed: bool = False) -> None:
        key = self._key(provider, model)
        now = time.time()
        # A provider day can run 25h across a clock change; keep a little
        # more than a day so an aligned window never loses its start.
        cutoff = now - 2 * max(_WINDOWS_SECONDS.values())
        events = [e for e in self._state.get(key, []) if e[0] > cutoff]
        events.append([now, tokens, 1] if failed else [now, tokens])
        self._state[key] = events
        self._save()

    @staticmethod
    def _amount_in_window(q_type: str, events: list[list[float]]) -> int:
        """Requests (`r...`) count events; tokens (`t...`) sum them."""
        if q_type.startswith("t"):
            return sum(e[1] for e in events)
        return len(events)

    def failed_in_window(self, provider: str, model: str, q_type: str,
                         now: Optional[float] = None) -> int:
        """How many of the window's requests were failed attempts."""
        now = time.time() if now is None else now
        start = self.window_start(provider, q_type, now)
        return sum(1 for e in self._state.get(self._key(provider, model), [])
                   if e[0] > start and len(e) > 2 and e[2])

    def is_available(self, provider: str, model: str, quotas: dict[str, int]) -> bool:
        if not quotas:
            return True
        key = self._key(provider, model)
        now = time.time()
        events = self._state.get(key, [])
        for q_type, limit in quotas.items():
            if q_type not in _WINDOWS_SECONDS:
                continue
            cutoff = self.window_start(provider, q_type, now)
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
            if q_type not in _WINDOWS_SECONDS:
                continue
            cutoff = self.window_start(provider, q_type, now)
            in_window = [e for e in events if e[0] > cutoff]
            if q_type.startswith("t"):
                remaining = sum(e[1] for e in in_window)
                if remaining < limit:
                    continue
                # Tokens fall out of the window oldest-first; find the
                # moment enough of them have expired to be under limit
                # again. An aligned day frees everything at once.
                for e in in_window:
                    remaining -= e[1]
                    if remaining < limit:
                        wait = max(wait, self.window_end(provider, q_type, e[0], now) - now)
                        break
            else:
                if len(in_window) < limit:
                    continue
                oldest = in_window[0][0]
                wait = max(wait, self.window_end(provider, q_type, oldest, now) - now)
        return max(0.0, wait)
