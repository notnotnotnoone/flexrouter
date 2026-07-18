# PRD: Streaming generate() with retry-progress events

Status: ready-for-agent

## Origin

Requested by Stash (`C:\projects\boxes`), a consumer of this library, which wants
two things its current blocking `generate()`/`agenerate()` can't provide:

1. **Retry visibility.** Stash's `default` tier has 55 configured models and
   `retries: 20` (21 attempts, 1s backoff) — a bad stretch can silently take
   20+ seconds today. Stash wants to show the user live progress ("trying
   model 4 of 21...") as the existing retry loop runs.
2. **Token-by-token output.** Once a model responds, Stash wants the answer
   text delivered incrementally (the classic typing effect), not as one
   blocking JSON blob.

Full design context (Stash's side of the contract, the SSE transport it
builds on top of this): `C:\projects\boxes\docs\superpowers\specs\2026-07-17-sse-streaming-retry-progress-design.md`.
This PRD only covers what FlexRouter itself needs to build — a new streaming
API. What Stash does with it (SSE framing, frontend rendering) is entirely
Stash's concern and not part of this repo's work.

## Requirement

Add a new streaming generate API **alongside** the existing `generate()`/
`agenerate()` — additive only. Those must keep working exactly as they do
today; other consumers of this library depend on the blocking contract and
must see zero behavior change.

The new API's caller (Stash, or anyone else) needs to observe, as they
happen:
- Each attempt in the existing retry loop starting (which model, which
  attempt number out of how many).
- Each attempt failing (and why — rate-limited vs. provider error), before
  the loop moves to the next model.
- Text deltas once a model's response has actually started streaming back.
- A clear terminal signal either way: success (with the same result shape
  `agenerate()` already returns today) or exhaustion (every attempt in the
  tier failed).

**No retry after a model commits.** Today's retry loop only rotates to
another model on `RateLimitError`/`ProviderError`, both raised before any
response body exists. That must stay true here: once a chosen model's stream
has yielded its first delta, a later failure (e.g. a dropped connection
mid-response) must NOT trigger a silent retry onto a different model — that
would produce an answer stitched from two different models' output, which is
worse than failing outright. A failure after the first delta ends the stream
as a terminal failure, full stop.

## Non-goals

- Changing the retry count, backoff, or model-selection logic in
  `engine.py`'s `select()`. This PRD is about exposing the existing behavior,
  not changing it.
- A CLI or dashboard UI for this — that's tracked separately if wanted at
  all; this PRD is the library API only.
- Guaranteeing every provider in `flexrouter.yaml` supports server-side
  streaming — see Issue 01 for how to handle providers that don't.

## Comments
