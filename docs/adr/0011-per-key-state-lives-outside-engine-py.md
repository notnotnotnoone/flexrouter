# 0011. Per-key state lives outside engine.py

Date: 2026-09-21
Status: Accepted

## Context

Spec section 5 asks for blind key rotation (`counter % len(provider_cfg.api_keys)`)
to be replaced with real per-key state: a rejected key benches only itself,
a rate-limited key cools down instead of re-entering rotation immediately,
and a configurable strategy picks between a provider's live keys. The code
that currently does the blind rotation lives inside `engine.py::_make_result`,
which is on the "reused unchanged" list — three independent reviews judged
its design sound, and Stage 3 already established the pattern of routing
around it rather than editing it (`explain_unavailable()` for `skipped`,
`_build_pin_engine` for pinning).

`ProviderConfig` already carries `keys: list[KeyRecord]` — real key
identity, resolved from `keys.json`/environment/inline secrets by
`config.py::resolve_keys` — alongside the flattened `api_keys: list[str]`
that `engine.py` actually rotates through. This meant the real fix did not
need any new plumbing from settings down to the router; it only needed a
place to *use* the identity that was already there.

## Decision

**`engine.py` is not touched.** `RoutingEngine.select()` still returns a
`RouteResult` with *some* `api_key` value, chosen by its own unmodified
blind rotation. `LocalRouter` immediately overrides it:
`dataclasses.replace(route, api_key=chosen.secret)` — the same technique
`_build_pin_engine` already uses on `FlexConfig`, applied here to the
`api_key` field `RouteResult` already has. The chosen key's id is never
attached to `RouteResult` (that would need editing the dataclass in
`engine.py`); it travels as a plain local variable through the retry loop,
the same way Stage 3's `attempts`/`skipped` do.

*Cost:* `engine.py`'s own key rotation still runs on every call, computing a
value that is immediately discarded. Accepted because the alternative is
editing a frozen file for a computation whose result is thrown away either
way — the waste is in CPU cycles nobody will ever measure, not in behaviour.

**When every key for a provider is currently unavailable, the key layer
signals it to `engine.py` through primitives `engine.py` already reads.** A
cooling-dominated exhaustion calls `PenaltyBox.penalize_short(provider,
model, remaining_seconds)`; an all-benched exhaustion calls
`PenaltyBox.quarantine_provider(provider, reason)`. Both already exist,
already unmodified, and `engine.py`'s `_skip_reason` and
`seconds_until_available` already consult them. This is the third time this
codebase has used "teach `engine.py` something by feeding it through an
interface it already reads" instead of editing it — Stage 2 did it for
pinning, Stage 3 did it for `skipped`.

**A rejected key (`bad_key`) benches only that key, not the provider** —
this is the fix the whole stage exists for. The provider itself is
quarantined only once every configured key for it is `benched` or
`disabled`, matching the spec's own wording exactly.

**`most_headroom`, `fastest`, `requests_today`/`tokens_today`/`failures_24h`,
and the concurrency cap all needed a concrete definition the spec's worked
example doesn't give** — `most_headroom` is fewest `active_requests` then
lowest `tokens_today` (no per-key rate limit exists anywhere in the schema
to compute a literal headroom fraction against); `fastest` is an EMA of
completed-attempt latency with unmeasured keys sorting best (so a new key
gets tried at least once); the three daily counters reset on UTC
calendar-day rollover, the same simplification `budget.py`'s `DailyBudget`
already makes elsewhere, just persisted; the concurrency cap is a new
setting, `key_concurrency_cap`, default `4`.

*Cost of the daily-rollover simplification:* `failures_24h` is really
"failures today," which can under- or over-count a true trailing 24 hours
depending on what time of day a key's streak happens to fall. Accepted
because every field this covers is informational bookkeeping for a future
dashboard, not something this stage's own selection or backoff logic reads.

**`active_requests` is bracketed around "time until the first response,"
not the whole stream.** For non-streaming this is the whole call; for
streaming it is `begin_request`/`end_request` around
`stream.__anext__()`'s first pull only — a post-commit failure does not
re-decrement or otherwise touch it, because it was already released the
moment the first chunk (or a pre-commit failure) resolved. Extending this
through the whole stream would mean threading key-state calls through
Stage 3's carefully-finished post-commit code for a form of sustained
concurrency-capping the spec never asked for.

**Rate-limit cooldown duration reuses the existing model-level penalty
duration** (`PenaltyBox.penalty_seconds`) as a stand-in for "the provider's
stated reset time," because the decision layer that would supply a real one
(spec §4a, Stage 5) does not exist yet. This is a known, temporary
approximation — Stage 5 should replace it.

## Consequences

- A dashboard (Stage 8) reading `state/key_state.json` can show, per key:
  is it live/cooling/benched, when it'll recover, how much it's been used
  today, its recent latency — everything the spec's worked example asked
  for, all without engine.py knowing any of it exists.
- `key_id` in `state/traces.jsonl` (reserved as always-`null` by Stage 3,
  ADR 0010 ruling 4) is now populated with a real value on every trace
  where a key was actually used, closing the one deferred field that stage
  explicitly left for later.
- The next time a "route around engine.py, don't edit it" case comes up
  (Stage 5's error brain will likely need one, since verdicts feed back
  into routing decisions engine.py currently makes alone), this ADR and its
  two predecessors are the established playbook: find the interface
  `engine.py` already reads, and feed it through that.
