# Issue 01: `tools` and `reasoning` capability facts exist but never receive evidence

Status: ready-for-agent

## What

`flexrouter/model_facts.py`'s state machine (`record_success`, `record_contradicting_failure`,
`check_staleness`) is capability-agnostic — it works identically for `vision`, `tools`, and
`reasoning`. But `flexrouter/_router.py` only ever calls it with `capability="vision"`, at all
9 evidence-recording sites (3 in `agenerate`, 6 in `agenerate_stream`), gated on the existing
`vision: bool` request parameter.

There is no equivalent per-request signal for `tools`/`reasoning` anywhere in the codebase today.
`vision` works because `agenerate`/`agenerate_stream` already take it as an explicit parameter,
already reflected in the trace's `asked.needs`. Nothing currently detects "did this call actually
ask the model to use a tool" (e.g. a non-empty `tools=[...]` in `**kwargs`) or "did this call ask
for extended reasoning."

## Why it is not urgent

Stage 6's own scope (ADR 0013, ruling 4) explicitly named this as a follow-up rather than
in-scope work — inventing the detection signal is a small feature of its own, not something to
smuggle into the stage that builds the state machine and the store. Nothing is broken; `tools`
and `reasoning` facts simply stay unpopulated (`None`) forever until this lands.

## Done when

- A per-request signal exists for whether a call exercised tool-calling (most likely: a non-empty
  `tools` kwarg reaching `agenerate`/`agenerate_stream`) and, separately, for extended reasoning
  if this codebase gains a concept of that.
- The same three-part evidence guard already used for `vision` (`verdict.verdict in
  ("bad_request", "model_gone")` and `verdict.confidence >= self._error_brain.confidence_threshold`)
  is applied to `tools`/`reasoning` using that new signal, at the same 9 call sites (or however
  many remain relevant).
- Real tests drive both the positive (evidence recorded) and negative (guard correctly excludes)
  cases, matching the pattern already established in `tests/test_model_facts_agenerate.py` and
  `tests/test_model_facts_agenerate_stream.py`.

## Constraint

`flexrouter/engine.py` is reused-unchanged by spec decree. Any new capability-detection signal
belongs in `_router.py`'s request handling, not in `engine.py`'s selection logic (see ADR 0013).
