# 0013. Model facts are learned, separately from declared config

Date: 2026-09-21
Status: Accepted

## Context

Spec section 4b asks for a persistent record of what each model can
actually do — vision, tools, reasoning — built from real traffic through a
deliberately lenient state machine: one contradicting result only ever
casts doubt, never certainty; three contradicting results (five for a
value the provider itself published) are needed before something is
marked `no`; any success is stronger evidence than any failure and
restores `yes` outright.

`engine.py` already has a related, but different, mechanism:
`ModelConfig.vision`, a flag the owner declares in settings, which
`_skip_reason` already uses today to reject a vision request against a
model that doesn't claim to support it. That flag is static and
config-driven. What this stage builds is dynamic and traffic-driven.
Conflating the two — letting a learned `no` actually block a request the
way the declared flag does — would be exactly the kind of routing-decision
change Stage 5 already established belongs in its own, separately
reviewed task, not folded into the stage that builds the knowledge base
in the first place.

Stage 5 (ADR 0012, ruling 5) declined to add a `bad_request` classification
rule because nothing existed yet to send that evidence to. This stage is
that destination.

## Decision

**`state/model_facts.json` and `ModelConfig.vision` remain two separate,
unconnected mechanisms.** Neither reads nor writes the other — `engine.py`
has no reference to `model_facts.py` or `ModelFactsStore` anywhere in it.
This stage teaches the model-facts store from real traffic; it does not
wire that knowledge back into selection. That wiring — and the judgment
call of whether a learned `no`/`doubted` should ever be allowed to
override an owner's explicit config, or only supplement missing config —
is deferred to a stage that can give it the same task-by-task care
Stages 2-5 gave their own routing changes.

**Stage 5's deferred `bad_request` rule is added now.** `classify_by_rule`
maps a bare 400 status to `bad_request`, `source="rule"`, `confidence=1.0`
— `flexrouter/error_brain.py` is this project's own code, not a frozen
file, so this is a normal, anticipated extension, not an exception to
anything.

**Only `vision` gets real evidence wiring this stage.** The state machine
itself (`record_success`, `record_contradicting_failure`,
`check_staleness`) is capability-agnostic and works identically for
`tools` and `reasoning` — but the router has no per-request signal today
for whether a call actually exercised tool-calling or extended reasoning,
the way `vision: bool` already exists for image input. Inventing that
signal is a small feature of its own, filed as a follow-up, not smuggled
into a stage whose job is the state machine and the store.

**A failure counts as evidence under three conditions, all required**:
the request asked for `vision`, the classified verdict is `bad_request` or
`model_gone`, and the verdict's confidence is at or above
`ErrorBrain.confidence_threshold` — a new, small public property added to
`ErrorBrain` so this comparison doesn't duplicate the same number
`ErrorBrain` already enforces internally for its own `flagged_for_review`
marking. `LocalRouter.agenerate` and `agenerate_stream` each check all
three at every site where they classify an attempt's failure (rate
limit, auth failure, provider error, and — `agenerate_stream` only, since
a stream can fail after it has already started answering — an empty or
interrupted stream) before calling `record_contradicting_failure`, and
both call `record_success` only when `vision` was set and the attempt
actually succeeded.

**A `manual` fact absorbs a contradicting result silently rather than
raising.** The spec calls for "a notice," but nothing in this codebase has
a notice-delivery mechanism yet (that's a dashboard concern, Stage 8) —
a no-op is the correct behaviour until there is somewhere for a notice to
go; a request that happens to generate disputed evidence about a
manually-pinned fact should not crash because of it.

**Staleness (an `observed` `no` reverting to `doubted` after 30 days) is
checked on read**, inside `ModelFactsStore.get`, not by a background job —
this project has no scheduler infrastructure, and a fact's staleness only
matters at the moment something is about to consult it.

**`vision` is not yet reachable from the HTTP surface.** `vision` is a
parameter of `LocalRouter.agenerate`/`agenerate_stream` and of the
`FlexRouter` client library, but the OpenAI-shaped `/v1/chat/completions`
wire format has no field for it — `flexrouter/app.py`'s `chat_completions`
handler forwards only `_PASSTHROUGH` body keys (`temperature`, `tools`,
etc.), and `vision` is not among them. Every failure- and success-path
test proving this stage's evidence recording therefore goes through
`LocalRouter` directly, the same surface Tasks 2 and 3 tested against.
Wiring a request-level vision signal into the wire protocol — so an HTTP
client, not just library callers, can generate this evidence — is its own
small feature, not something this stage's scope covers.

## Consequences

- A future dashboard (Stage 8) or a future selection-preference stage can
  read `state/model_facts.json` and get real, evidence-backed capability
  beliefs, with full provenance and a strike history, without having
  built any of the collection machinery themselves.
- `tools` and `reasoning` capability facts can exist in the schema (a
  dashboard could show them as "not yet observed") but will never actually
  gain evidence until the follow-up request-level detection feature lands.
- The next stage that wants routing to actually consult this store
  inherits a state machine that is already correct and already tested —
  it only has to decide how to weigh "learned" against "declared," not
  build the learning itself.
- Evidence only ever accumulates through the library surface
  (`LocalRouter`/`FlexRouter`) until a future stage adds `vision` to the
  wire protocol; a deployment that only ever talks to flexrouter over
  HTTP will see an empty `model_facts.json` regardless of how many vision
  requests it actually sends.
