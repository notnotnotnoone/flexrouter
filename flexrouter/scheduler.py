"""Choosing between a provider's live keys.

engine.py already chose *which model*; this module chooses *which key* to
use for it, given real per-key state (flexrouter/key_state.py) instead of
the blind counter % len(keys) rotation it replaces (spec §5, fault 5 in the
motivation section). Nothing here is called from engine.py — LocalRouter
calls pick_key() itself, after engine.select() has already returned
(ADR 0011).
"""
from __future__ import annotations

import random
from typing import Optional

from flexrouter.key_state import KeyStateStore
from flexrouter.keys import KeyRecord, allows


class RoundRobinCounters:
    """Per-provider cursor for the round_robin strategy.

    A plain dict would work too, but a caller reading `pick_key`'s signature
    should not be able to accidentally share position across two unrelated
    LocalRouter instances (e.g. two routers in the same test process) by
    forgetting to pass one in — see the ValueError this forces in pick_key.
    """
    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def next(self, provider: str, n: int) -> int:
        i = self._counters.get(provider, 0)
        self._counters[provider] = i + 1
        return i % n


def _most_headroom(live: list[KeyRecord], provider: str, states: KeyStateStore) -> KeyRecord:
    def sort_key(r: KeyRecord):
        s = states.get(provider, r.id)
        return (s.active_requests, s.tokens_today)
    return min(live, key=sort_key)


def _round_robin(live: list[KeyRecord], provider: str, states: KeyStateStore,
                 counters: RoundRobinCounters) -> KeyRecord:
    return live[counters.next(provider, len(live))]


def _fastest(live: list[KeyRecord], provider: str, states: KeyStateStore) -> KeyRecord:
    def sort_key(r: KeyRecord):
        s = states.get(provider, r.id)
        # No measurement yet sorts as the best possible latency, so a new
        # key gets tried at least once instead of being permanently passed
        # over by keys with an established fast track record.
        return s.ema_latency_ms if s.ema_latency_ms is not None else -1.0
    return min(live, key=sort_key)


def _weighted(live: list[KeyRecord], provider: str, states: KeyStateStore) -> KeyRecord:
    weights = [max(r.weight, 0) for r in live]
    if sum(weights) == 0:
        return random.choice(live)
    return random.choices(live, weights=weights, k=1)[0]


def pick_key(
    candidates: list[KeyRecord],
    provider: str,
    model_id: str,
    strategy: str,
    states: KeyStateStore,
    cap: int,
    now: Optional[float] = None,
    counters: Optional[RoundRobinCounters] = None,
) -> Optional[KeyRecord]:
    live = [
        r for r in candidates
        if allows(r, model_id)
        and states.is_available(provider, r.id, now=now)
        and states.get(provider, r.id).active_requests < cap
    ]
    if not live:
        return None
    if strategy == "round_robin":
        if counters is None:
            raise ValueError("round_robin strategy requires a RoundRobinCounters instance")
        return _round_robin(live, provider, states, counters)
    if strategy == "fastest":
        return _fastest(live, provider, states)
    if strategy == "weighted":
        return _weighted(live, provider, states)
    return _most_headroom(live, provider, states)  # default, and unknown-strategy fallback
