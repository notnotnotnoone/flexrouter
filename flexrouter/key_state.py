# flexrouter/key_state.py
"""Per-key runtime state: state/key_state.json, keyed by "provider:key_id".

Replaces blind counter % len(keys) rotation (fault 5 in the v2 design doc)
with real per-key memory that survives a restart: a rejected key benches
only itself, a rate-limited key cools down instead of re-entering rotation
immediately, and every key's recent behaviour (failures, throughput,
latency) is available to a Scheduler (flexrouter/scheduler.py) choosing
between a provider's live keys.

engine.py is not touched anywhere in this stage. This module and
flexrouter/scheduler.py are a layer LocalRouter runs *after* engine.py has
already picked a model, overriding only the api_key field a RouteResult
already has (see ADR 0011).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flexrouter.store import harden, read_json, write_json


@dataclass
class KeyState:
    status: str = "live"  # live | cooling | benched | disabled
    until: Optional[float] = None
    reason: str = ""
    consecutive_failures: int = 0
    failures_24h: int = 0
    requests_today: int = 0
    tokens_today: int = 0
    last_used_at: Optional[float] = None
    active_requests: int = 0
    ema_latency_ms: Optional[float] = None
    day: str = ""


def _today(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).date().isoformat()


class KeyStateStore:
    def __init__(self, state_dir: str) -> None:
        self._path = Path(state_dir) / "key_state.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._states: dict[str, KeyState] = {}
        for k, v in read_json(self._path, default={}).items():
            self._states[k] = KeyState(**v)

    def _key(self, provider: str, key_id: str) -> str:
        return f"{provider}:{key_id}"

    def get(self, provider: str, key_id: str) -> KeyState:
        return self._states.get(self._key(provider, key_id), KeyState())

    def _maybe_reset_day(self, state: KeyState, now: float) -> None:
        today = _today(now)
        if state.day != today:
            state.requests_today = 0
            state.tokens_today = 0
            state.failures_24h = 0
            state.day = today

    def _save(self) -> None:
        write_json(self._path, {k: asdict(v) for k, v in self._states.items()})
        harden(self._path)

    def is_available(self, provider: str, key_id: str, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        k = self._key(provider, key_id)
        state = self._states.get(k)
        if state is None or state.status == "live":
            return True
        if state.status == "cooling":
            if state.until is not None and now >= state.until:
                state.status = "live"
                state.until = None
                self._states[k] = state
                self._save()
                return True
            return False
        return False  # benched | disabled

    def seconds_until_available(self, provider: str, key_id: str,
                                now: Optional[float] = None) -> float:
        now = now if now is not None else time.time()
        if self.is_available(provider, key_id, now=now):
            return 0.0
        state = self.get(provider, key_id)
        if state.status == "cooling" and state.until is not None:
            return max(0.0, state.until - now)
        return float("inf")

    def mark_success(self, provider: str, key_id: str, tokens: int, latency_ms: int,
                     now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        k = self._key(provider, key_id)
        state = self._states.get(k, KeyState())
        self._maybe_reset_day(state, now)
        state.status = "live"
        state.until = None
        state.consecutive_failures = 0
        state.requests_today += 1
        state.tokens_today += tokens
        state.last_used_at = now
        state.ema_latency_ms = (
            latency_ms if state.ema_latency_ms is None
            else 0.3 * latency_ms + 0.7 * state.ema_latency_ms)
        self._states[k] = state
        self._save()

    def mark_cooling(self, provider: str, key_id: str, seconds: float, reason: str,
                     now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        k = self._key(provider, key_id)
        state = self._states.get(k, KeyState())
        self._maybe_reset_day(state, now)
        state.status = "cooling"
        state.until = now + max(seconds, 30.0)
        state.reason = reason
        state.consecutive_failures += 1
        state.failures_24h += 1
        self._states[k] = state
        self._save()

    def mark_benched(self, provider: str, key_id: str, reason: str,
                     now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        k = self._key(provider, key_id)
        state = self._states.get(k, KeyState())
        self._maybe_reset_day(state, now)
        state.status = "benched"
        state.until = None
        state.reason = reason
        state.consecutive_failures += 1
        state.failures_24h += 1
        self._states[k] = state
        self._save()

    def all_unavailable(self, provider: str, key_ids: list[str],
                        now: Optional[float] = None) -> bool:
        if not key_ids:
            return False
        now = now if now is not None else time.time()
        return all(not self.is_available(provider, kid, now=now) for kid in key_ids)

    def all_benched_or_disabled(self, provider: str, key_ids: list[str],
                                now: Optional[float] = None) -> bool:
        if not key_ids:
            return False
        return all(self.get(provider, kid).status in ("benched", "disabled")
                  for kid in key_ids)

    def min_seconds_until_available(self, provider: str, key_ids: list[str],
                                    now: Optional[float] = None) -> float:
        if not key_ids:
            return float("inf")
        now = now if now is not None else time.time()
        return min(self.seconds_until_available(provider, kid, now=now) for kid in key_ids)

    def begin_request(self, provider: str, key_id: str) -> None:
        k = self._key(provider, key_id)
        state = self._states.get(k, KeyState())
        state.active_requests += 1
        self._states[k] = state
        self._save()

    def end_request(self, provider: str, key_id: str) -> None:
        k = self._key(provider, key_id)
        state = self._states.get(k, KeyState())
        state.active_requests = max(0, state.active_requests - 1)
        self._states[k] = state
        self._save()
