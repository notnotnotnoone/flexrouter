# Plan file lifecycle

Root-level plan files (e.g. `PLAN.md`, `PLAN-V2.md`) hold the working plan for
whatever rebuild or major change is currently in progress.

## When a plan is finished

Once every stage in a root-level plan file is done, move the file into
`docs/archive/` instead of deleting it or leaving it in the root. Don't edit
its content on the move — it's a historical record, not living documentation.

If a superseding plan exists (e.g. `PLAN-V2.md` replacing `PLAN.md`), archive
the superseded one immediately rather than waiting for the new plan to finish.
