# Issue 01: no way to accept a pending catalogue change into (or out of) the router's actual configuration

Status: ready-for-agent

## What

`state/catalog_pending.json` (written automatically on every service start
as of Stage 7, and manually via `flexrouter refresh`/the dashboard's
refresh button since Stages 1-2) records every model a provider's
catalogue shows that `config.yaml` doesn't know about yet ("appeared"),
and every model `config.yaml` lists that the provider's catalogue no
longer shows ("vanished"). Nothing acts on either list. A newly-appeared
model is never added to a bucket; a vanished model is never disabled or
flagged. The spec (§6) calls for a per-provider auto-apply setting plus a
manual, ask-me-first default — neither exists.

## Why it is not urgent

Nothing is broken by its absence — `catalog_pending.json` is exactly as
informative as it already was pre-Stage-7, just populated automatically
now instead of only on demand. The router keeps serving whatever
`config.yaml` plus `overrides.json` already say, unaffected either way.

## Done when

- `overrides.json` gains a way to represent a model that doesn't exist in
  `config.yaml` at all (today `flexrouter/overrides.py`'s
  `apply_overrides`/`ALLOWED_FIELDS` can only patch fields on a model
  `config.yaml` already declares — `provider`/`model` identity fields are
  deliberately excluded, on purpose, to stop an override turning one
  model into another; whatever this issue builds must not weaken that).
- A decision is made and recorded on which bucket a newly-appeared model
  lands in when accepted, and on what "accepting" a vanished model's
  removal actually does (disable it via the existing `enabled: false`
  override mechanism, most likely, since that already works for a model
  `config.yaml` still declares).
- A new `ProviderConfig.catalogue_auto_apply: bool = False` setting (or
  similar) lets a provider opt into applying its own pending changes
  automatically on the next startup refresh, matching spec's stated
  default (manual/ask-me-first) and opt-in (auto-apply).
- Some interface exists to accept/dismiss a pending item manually — a CLI
  command is the smallest option if this lands before Stage 8's
  dashboard; the dashboard's eventual "pending tray" UI can call the same
  underlying function either way.
- Real tests prove: an accepted appeared model actually becomes selectable
  (shows up in `/v1/models`, can be routed to); an accepted vanished
  model's removal actually stops it being selected; `config.yaml` is never
  written by any of this.

## Constraint

`flexrouter/engine.py` is reused-unchanged by spec decree — whatever
mechanism lands here must make an accepted model selectable through
`config.yaml`/`overrides.json`'s existing load path into `FlexConfig.tiers`,
not by teaching `engine.py` anything new (see ADR 0009/0010/0011/0013 for
the established pattern).
