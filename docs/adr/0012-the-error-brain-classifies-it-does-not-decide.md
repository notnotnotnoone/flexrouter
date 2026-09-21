# 0012. The error brain classifies; it does not decide

Date: 2026-09-21
Status: Accepted

## Context

Spec section 4a asks for provider error text to be classified into a small,
typed vocabulary (`too_fast`, `bad_key`, `needs_payment`, `model_gone`,
`their_end_temporary`, `message_too_long`, `bad_request`, `unknown`), with
built-in rules for the unambiguous cases and a small structured-output
model (behind a `Decider` protocol, defaulting to a `NullDecider`) for
everything else. The spec's own table also maps each verdict to a router
action — but by the time this stage started, every one of those actions
for the cases a bare status code already disambiguates was already built,
across Stages 2 through 4, already reviewed: a 401/403 benches the key
that got it (Stage 4), a 402 quarantines the provider
(`ProviderError.is_provider_wide`, `client.py`, frozen), a 404/410
quarantines the route (`ProviderError.is_permanent`, frozen), a 429 cools
the key that hit it (Stage 4). Reimplementing those same decisions behind
a new verdict layer would mean two sources of truth for one fact, or
worse, a subtle divergence between what the classifier says and what the
router actually does.

## Decision

**This stage is observability-only.** `ErrorBrain.classify()` is called
from every failure branch in both `agenerate` and `agenerate_stream`, and
its result is written to `attempts[].verdict` in the request trace — the
field Stage 3 reserved and left `null` (ADR 0010, ruling 5). Nothing else
changes. No call site's existing `quarantine`/`quarantine_provider`/
`penalize`/`mark_benched`/`mark_cooling` decision is touched, conditioned
on, or duplicated by a verdict.

*Cost:* the spec's "Router action" column is only half-implemented — the
classification exists, the action on it (mostly) doesn't, for cases the
router doesn't already handle by status code alone. This is accepted
because acting on a verdict is exactly the kind of routing-behaviour
change that deserves the same task-by-task, reviewed care Stages 2-4 got,
not a rider on a stage whose main job is building the classifier in the
first place.

**The classifier's real value is scoped to exactly the gap in the existing
status-code logic**: a `ProviderError` whose status is not already one of
401/402/403/404/410/429. For every branch where the router already knows
the verdict unambiguously from which `except` clause fired, `_router.py`
passes a fixed, correct status rather than re-deriving one from text — the
`RateLimitError` branch always classifies with `status=429`, the
`RouterError` (auth) branch always with `status=401` (this codebase's
`client.py` raises `RouterError` only for 401 or 403, and both verdicts
are identical — `bad_key` — so 401 is a safe stand-in for either).

**`bad_request` has no built-in rule yet.** The spec says a `bad_request`
verdict should feed `state/model_facts.json` as capability evidence — that
store doesn't exist until Stage 6. A verdict with no consumer isn't worth
manufacturing; an otherwise-unclassified 400-class error falls through to
`unknown` under `NullDecider` instead.

**Fingerprinting happens on already-scrubbed text**, unconditionally,
inside `ErrorBrain.classify()` itself — not trusting any caller to have
scrubbed first, the same defense-in-depth `TraceWriter` already
established (ADR 0010). `state/error_brain.json`'s `sample` field can
never contain anything key-shaped.

**Manual entries are protected even though nothing writes one yet.**
`ErrorBrain._record()` checks `source == "manual"` before ever overwriting
a stored verdict, so when a later stage adds a way to set one (a CLI
command, a dashboard row), the protection is already correct rather than
retrofitted.

**`ModelFacts` stays a plain `dict` on the `Decider` protocol.** Its real
shape (spec §4b) is Stage 6's to define; `describe_model` exists on the
protocol to match the spec's interface exactly, but nothing calls it this
stage.

## Consequences

- A future dashboard (Stage 8) reading `state/error_brain.json` and
  `attempts[].verdict` across traces gets real, typed reasons for
  failures — "why didn't it use the good one" now has an actual taxonomy,
  not just a raw exception string.
- The next stage that needs to *act* on a verdict (most plausibly Stage 6,
  once `state/model_facts.json` exists and `bad_request`/capability
  evidence has somewhere to go) inherits a classifier that already works
  and is already trustworthy — it only has to wire the action, not build
  the classification from scratch.
- `NullDecider` shipping as the only implementation resolves the
  roadmap's Open Q4: nothing in this codebase depends on
  `typesafe/jev-1.13` pricing or access being confirmed.
