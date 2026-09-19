# 0004. The existing settings file was not migrated

Date: 2026-09-18
Status: Accepted

## Context

Moving to one shared home (ADR-0001) means every existing per-project `flexrouter.yaml` needs to end up somewhere. An automatic migration could merge every project's buckets, providers, and models into the new shared `config.yaml` and try to reconcile whatever conflicts came out of that.

## Decision

The owner chose not to do that. Nothing about buckets or models is carried over automatically. The new home starts from `STARTER_CONFIG` — empty providers, empty buckets — and the owner rebuilds them through the dashboard/CLI by hand. The one thing that is carried over is credentials: `flexrouter keys import <old_file>` (`flexrouter/cli.py::keys_import`, backed by `_scan_old_settings`) reads an old settings file, lifts any plain secrets it finds into `keys.json`, and leaves the old file untouched. Keys that were already environment-variable references are left alone, since they already resolve without any action.

## Consequences

- No risk of an automatic merge silently producing a bucket or provider list nobody asked for, and no migration-conflict logic to write, test, or maintain.
- The owner has to manually re-enter every provider, model, score, and rate limit that used to live in each project's `flexrouter.yaml`, once, by hand, before routing works again.
- `keys import` only understands the old inline-secret shapes (`api_key:`, `api_keys:` as strings or `{key: ...}` maps); anything stranger in an old file is silently skipped rather than imported or flagged.
