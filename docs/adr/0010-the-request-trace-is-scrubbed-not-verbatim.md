# 0010. The request trace is scrubbed, not verbatim

Date: 2026-09-19
Status: Accepted

## Context

Spec section 3 asks for `provider_message` to be "the provider's verbatim
text" in a per-request trace written to disk. That line was written before
`flexrouter/redact.py` existed. Several providers echo the rejected
credential back inside their own error text, and Stage 2's whole-branch
review already found exactly this shape of leak reaching a persisted,
unauthenticated-readable file once (the penalty reasons behind
`quarantine_reason()`). A trace is a saved record, read back later, of
provider text taken from possibly-hostile or possibly-leaky upstream
responses — the single highest-risk piece of this stage.

`engine.py` is reused unchanged by spec decree. It already computes exactly
why a model was passed over, through `_skip_reason()`, and
`explain_unavailable()` already re-walks a tier calling it for every model —
that method existed before this stage and was not written for it, but it is
exactly the shape Stage 3 needs.

Per-key identity (`state/key_state.json`) and the decision layer (`Decider`,
the error brain) are later stages, not built yet.

## Decision

**`provider_message` and `skipped[].detail` are scrubbed on the way into
`state/traces.jsonl`, by `TraceWriter.write()`, unconditionally.**
`flexrouter/redact.py`'s `scrub()` is applied to every value under those two
keys before the JSON line is written. This is not literally what the spec
asked for.

*Cost:* a trace read back later shows `…1234` instead of the provider's
whole sentence in the rare case that sentence contained something scrubbed —
a web address, a long model name, or an actual leaked credential all look
the same to the scrubber, exactly as ADR 0009 already accepted for the
`/v1/chat/completions` error envelope. No new cost beyond what ADR 0009
already priced in; this is the same rule reapplied at a new write site.

**`skipped` is populated by calling `RoutingEngine.explain_unavailable()`
from `LocalRouter`, once per request, before the retry loop begins.**
`engine.py` gains no new method and no edit. `_router.py` filters the
returned list to `available: False` entries and drops the `score` and
`available` keys to match the trace shape.

*Cost:* `skipped` reflects the tier's state at the moment the request
arrived, not at the moment each retry attempt happened. A model that becomes
unavailable partway through this request's own retries (for instance,
penalized by an earlier attempt within the same request) will not appear in
`skipped` for this trace, only in a later one. This was chosen over
recomputing `explain_unavailable()` on every retry because the spec's shape
has one `skipped` list per trace, not one per attempt, and the field answers
"what could the router see when this request arrived", which is the
debugging question the spec motivates the field with.

**`key_id` is `null` everywhere in this stage.** `RouteResult.api_key` is a
bare secret chosen by blind rotation (`counter % len(provider_cfg.api_keys)`)
with no identity attached — the per-key state stage has not landed. Matching
the secret against `keys.json` to reconstruct an identity was considered and
rejected: it would be inferring a fact the routing path does not yet track,
rather than recording one it does.

**`verdict` is absent from every attempt.** The decision layer that would
compute it (`Decider`, `NullDecider`, the error brain) does not exist yet.

## Consequences

- A trace read back by a future dashboard, or by the error brain in a later
  stage, is provably free of anything key-shaped, at the cost of occasionally
  reading slightly worse.
- `skipped` and the four failure-handling branches in `_router.py`
  (`_handle_auth_failure`, `_handle_provider_error`, the rate-limit branch,
  the empty-completion branches) needed no change to their own scrubbing —
  they already scrubbed at their own write sites per ADR 0009. `TraceWriter`
  scrubbing again is deliberate belt-and-suspenders, not redundant: a value
  reaching the trace by a path that does not go through those branches (for
  instance the raw `str(exc)` attempt-message this stage records) is still
  caught.
- `key_id` and `verdict` being placeholders rather than real values is
  visible in every trace written by this stage, which is the point: a future
  reader should be able to tell "not tracked yet" apart from "tracked and
  happened to be this value" without reading this ADR first.
