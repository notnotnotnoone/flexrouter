# 0023. The quickstart is a checklist, not a wizard

Date: 2026-09-27
Status: Accepted. Amends ADR 0007's consequences.

## Context

ADR 0007 deleted the init wizard: flexrouter has no per-project settings
file and never puts keys in one, so the wizard had nothing honest to write.
Its consequence was "hand-editing config.yaml for buckets and providers".
Since then the dashboard grew a complete no-YAML path (add a key, Add
models with AI), but a first-time user had nothing pointing at it.

## Decision

A "Get started" card on top of Overview lists five steps: add a provider,
paste its key, Add models with AI, Test all, and point your app at
`localhost:4891/v1`. Each step ticks itself when the thing has really
happened. There are no Next buttons. The card shows on first start and
whenever no model works; [Hide] turns off "Show quickstart", and Settings
brings it back.

The card writes nothing itself. Every step links to the normal pages, which
write `overrides.json` and `keys.json` as they always did. The only state it
keeps is `state/quickstart.json`: that a key test passed, and what each
model said to Test all.

## Consequences

ADR 0007's reasons still hold. Hand-editing config.yaml becomes the
by-hand option, documented in the Configuration Guide, not the first step.
