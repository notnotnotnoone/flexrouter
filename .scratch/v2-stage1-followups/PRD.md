# Stage 1 follow-ups: known issues left open at merge

Stage 1 of the v2 design (`docs/superpowers/specs/2026-09-18-flexrouter-v2-design.md`)
moved every setting and credential into one fixed machine-wide home. It finished with
a clean suite (479 passed, 1 skipped) and a final whole-branch review, but that review
and its scoped re-review surfaced items that were deliberately deferred rather than
fixed on the branch.

Each is real. None is a secret leak or a crash, and each needs its own small design.
They are numbered in rough priority order.

Nothing here blocks Stage 2 (the OpenAI-shaped service). Issue 02 belongs with Stage 5
and issue 05 with Stage 7 if they are not picked up sooner.
