# Issue 05: the guides say "tier" while everything else says "bucket"

Status: ready-for-human

## What

The owner's word for a named group of models is **bucket**, settings files write
`buckets:`, and the wire now advertises bucket names plainly. But the internal field is
still `FlexConfig.tiers` and the public parameter is still `tier`, because renaming
either would touch `flexrouter/engine.py`, which is reused-unchanged by spec decree.

`CONTEXT.md` explains the split. The guides under `docs/` do not — a reader walking
through them meets both words in the same page with nothing pointing across.

## Done when

- `docs/3-Concepts.md` carries one short sentence saying the two words mean the same
  thing and why both exist.
- Nothing is renamed. See ADR 0009.

## Also in this area

`tests/test_stage2_e2e.py` binds a fixed port (4899). If something else is holding it,
the test fails with a bare assertion that does not say the port was busy, and two
overlapping runs of the suite collide. Worth either picking a free port at runtime or
failing with a message that names the port.
