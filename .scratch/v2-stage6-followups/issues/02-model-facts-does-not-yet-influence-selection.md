# Issue 02: `state/model_facts.json` is learned but never consulted by routing

Status: ready-for-agent

## What

Stage 6 built a full capability state machine and taught it from real traffic (`ModelFactsStore`,
`flexrouter/model_facts.py`), but deliberately did not wire it back into any routing decision.
`engine.py`'s `_skip_reason`/`_score_candidates`/`_pick` still only ever consult the static,
config-declared `ModelConfig.vision` flag — a learned `doubted`/`no` capability fact for a model
currently has zero effect on whether that model gets selected for a vision request.

The spec's own "Selection preference" bullet (§4b) describes the eventual behavior: prefer
`published`/`observed` facts, then `guessed`, then `doubted`, never `no`. None of that exists yet.

## Why it is not urgent

Wiring this correctly means either (a) touching `engine.py`'s selection logic — forbidden by spec
decree without a separately-reviewed, narrowly-scoped exception the way Stage 3's engine.py bugfix
was, or (b) building a new override/filtering layer analogous to Stage 4's key-scheduler pattern,
sitting between `engine.select()` and the caller. Either is real, separate design work deserving
its own task-by-task care, not something to fold into the stage that only just finished building
the knowledge base this would consume. There is also an open judgment call this issue doesn't
answer: should a learned `no` ever be allowed to override an owner's explicit `ModelConfig.vision
= true`, or only ever supplement missing config? That decision belongs to whoever picks this up.

## Done when

- A ruling is made (and recorded) on whether learned facts can ever override declared config, or
  only fill gaps where declared config says nothing.
- Selection for a vision-flagged request actually consults `state/model_facts.json` and applies
  the spec's stated preference order, without touching `engine.py`'s own selection algorithm
  directly (matching the established "feed it through an interface it already reads, or wrap its
  output" pattern from ADR 0009/0010/0011).
- Real tests prove a model whose learned vision fact is `no` is actually deprioritized or skipped
  for a vision request, and that a `doubted` fact is still selectable, just ranked last, per spec.

## Constraint

`flexrouter/engine.py` is reused-unchanged by spec decree. Any exception to that needs its own
isolated, separately-reviewed commit the way Stage 3's `seconds_until_available` clock-unit fix
was handled (ADR: the fix commit for that bug), not a blanket rewrite of selection.
