# Issue 01: a saturated-but-live provider always waits a floored 30s, not a real estimate

Status: ready-for-agent

## What

`_router.py`'s `_pick_key` handles `pick_key()` returning `None` two ways: if every
key is benched/disabled it quarantines the provider, otherwise it calls
`self._key_states.min_seconds_until_available(...)` and penalizes for
`int(max(wait, 30.0))` seconds.

`min_seconds_until_available` (`flexrouter/key_state.py`) is built from
`seconds_until_available`, which is built from `is_available`. `is_available` only
looks at a key's `status` (`live` / `cooling` / `benched` / `disabled`) — it has no
idea about `active_requests` or the concurrency cap. So when every key is `live` but
saturated (`active_requests >= cap`, the case `scheduler.pick_key`'s filter
comprehension excludes them for), `is_available` reports every key as available,
`seconds_until_available` returns `0.0` for each, and `min_seconds_until_available`
returns `0.0`. The `wait != float("inf")` branch then always penalizes for exactly
`max(0.0, 30.0) = 30` seconds — a fixed floor, not a number that reflects how soon a
request now in flight is likely to free up a slot.

## Why it is not urgent

The default concurrency cap is low and usage is personal-scale, so a flat 30s
backoff on saturation is a reasonable, harmless approximation in practice. This path
also has no dedicated test coverage today — it was not exercised or verified during
stage 4.

## Done when

- A test drives the saturation case directly: every candidate key `live` and at
  `active_requests >= cap`, and asserts what `_pick_key` actually does (today: a
  30s `penalize_short`).
- If a real time-to-free-slot signal is worth adding (e.g. derived from
  `ema_latency_ms` or `last_used_at`), it goes through `key_state.py`/`scheduler.py`
  and `min_seconds_until_available`'s callers get a meaningful number instead of a
  cap-blind one. If not, at minimum document the 30s floor as the intended fallback.

## Constraint

`flexrouter/engine.py` is reused-unchanged by spec decree; this is all
`flexrouter/key_state.py` / `flexrouter/scheduler.py` / `flexrouter/_router.py`
territory (see ADR 0011).
