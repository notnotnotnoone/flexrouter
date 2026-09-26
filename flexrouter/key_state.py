# flexrouter/key_state.py
"""Per-key runtime state: state/key_state.json, keyed by "provider:key_id".

Replaces blind counter % len(keys) rotation (fault 5 in the v2 design doc)
with real per-key memory that survives a restart: a rejected key is Needs
you on its own, a rate-limited key is Busy instead of re-entering rotation
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
    # grill-decisions.md §3's words: ready | busy | needs_you | off. The
    # pre-v2.3 words (live | cooling | benched | disabled) are read and
    # translated on load, see _LEGACY.
    status: str = "ready"
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


_LEGACY = {"live": "ready", "cooling": "busy", "benched": "needs_you", "disabled": "off"}


def _today(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).date().isoformat()


class KeyStateStore:
    def __init__(self, state_dir: str) -> None:
        self._path = Path(state_dir) / "key_state.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._states: dict[str, KeyState] = {}
        for k, v in read_json(self._path, default={}).items():
            # A request cannot outlive the process that started it, so a count
            # left on disk by a crash would block the key forever.
            v = {**v, "active_requests": 0}
            v["status"] = _LEGACY.get(v.get("status", "ready"), v.get("status", "ready"))
            self._states[k] = KeyState(**v)

    def _key(self, provider: str, key_id: str) -> str:
        return f"{provider}:{key_id}"

    def get(self, provider: str, key_id: str, now: Optional[float] = None) -> KeyState:
        k = self._key(provider, key_id)
        state = self._states.get(k)
        if state is None:
            # Read-only default for a never-seen key — nothing to reset,
            # nothing to persist.
            return KeyState()
        now = now if now is not None else time.time()
        changed = self._maybe_reset_day(state, now)
        self._states[k] = state
        if changed:
            self._save()
        return state

    def _maybe_reset_day(self, state: KeyState, now: float) -> bool:
        today = _today(now)
        if state.day != today:
            state.requests_today = 0
            state.tokens_today = 0
            state.failures_24h = 0
            state.day = today
            return True
        return False

    def _save(self) -> None:
        write_json(self._path, {k: asdict(v) for k, v in self._states.items()})
        harden(self._path)

    def is_available(self, provider: str, key_id: str, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        k = self._key(provider, key_id)
        state = self._states.get(k)
        if state is None or state.status == "ready":
            return True
        if state.status == "busy":
            if state.until is not None and now >= state.until:
                state.status = "ready"
                state.until = None
                self._states[k] = state
                self._save()
                return True
            return False
        return False  # needs_you | off

    def seconds_until_available(self, provider: str, key_id: str,
                                now: Optional[float] = None) -> float:
        now = now if now is not None else time.time()
        if self.is_available(provider, key_id, now=now):
            return 0.0
        state = self.get(provider, key_id, now=now)
        if state.status == "busy" and state.until is not None:
            return max(0.0, state.until - now)
        return float("inf")

    def mark_success(self, provider: str, key_id: str, tokens: int, latency_ms: int,
                     now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        k = self._key(provider, key_id)
        state = self._states.get(k, KeyState())
        self._maybe_reset_day(state, now)
        state.status = "ready"
        state.until = None
        state.reason = ""
        state.consecutive_failures = 0
        state.requests_today += 1
        state.tokens_today += tokens
        state.last_used_at = now
        state.ema_latency_ms = (
            latency_ms if state.ema_latency_ms is None
            else 0.3 * latency_ms + 0.7 * state.ema_latency_ms)
        self._states[k] = state
        self._save()

    def mark_busy(self, provider: str, key_id: str, seconds: float, reason: str,
                  now: Optional[float] = None) -> None:
        """Rate-limited: back in `seconds` (the provider's figure, else 60s)."""
        now = now if now is not None else time.time()
        k = self._key(provider, key_id)
        state = self._states.get(k, KeyState())
        self._maybe_reset_day(state, now)
        state.status = "busy"
        state.until = now + max(seconds, 1.0)
        state.reason = reason
        state.consecutive_failures += 1
        state.failures_24h += 1
        self._states[k] = state
        self._save()

    def mark_needs_you(self, provider: str, key_id: str, reason: str,
                       now: Optional[float] = None) -> None:
        """The provider rejected this key. No timer: it needs a new key."""
        now = now if now is not None else time.time()
        k = self._key(provider, key_id)
        state = self._states.get(k, KeyState())
        self._maybe_reset_day(state, now)
        state.status = "needs_you"
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

    def all_need_you_or_off(self, provider: str, key_ids: list[str],
                                now: Optional[float] = None) -> bool:
        if not key_ids:
            return False
        now = now if now is not None else time.time()
        return all(self.get(provider, kid, now=now).status in ("needs_you", "off")
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
