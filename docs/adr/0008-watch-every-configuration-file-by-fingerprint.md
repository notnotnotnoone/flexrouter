# 0008. Watch every configuration file, by fingerprint rather than newest timestamp

Date: 2026-09-19
Status: Accepted

## Context

A running `FlexRouter` picks up configuration changes without a restart. It did this by recording `config.yaml`'s modification time and comparing it on each request.

Stage 1 broke that in two ways at once.

First, `config.yaml` became the one file nothing may write. Every machine-made change now lands in `overrides.json`, and credentials live in `keys.json`. So the reload trigger was watching the only file that could no longer change: a model disabled in the dashboard, or a key added with `flexrouter keys add`, would never be noticed until the process restarted.

The obvious repair — take the newest modification time across all three files — was applied, and introduced two faults of its own:

- A filesystem clock is only as fine as its tick. On Windows two writes can land in the same tick, so "newest" reads identically either side of a real edit and the change is silently missed. This first appeared as an intermittently failing test, which is the cheap version of the same bug; in a running service it means a change the owner made quietly does nothing.
- `max()` hides a watched file being replaced by an *older* copy — restoring a backup, or a sync tool writing an earlier version. The newest timestamp stays the newest, so nothing reloads.

Separately, treating an unreadable file as a timestamp of zero turned a deleted or renamed settings file into "reload now", and the reload then failed in the middle of a request.

## Decision

`FlexRouter._watched_paths()` returns the settings file, the overrides file and the credential file. `_newest_mtime()` — the name is now a misnomer, kept only to avoid churn — returns a tuple carrying, per file, its `st_mtime_ns` and its size. A reload is triggered when that tuple differs from the last one seen.

A file that cannot be stat'ed keeps the last fingerprint that was seen for it, rather than contributing zero. A reload that raises is caught: the previously loaded settings stay in place and serving, and the failure is recorded on `_reload_error` and logged.

## Consequences

- A change to any of the three files is picked up on the next request, including a key added mid-run.
- Same-tick edits that change a file's length, and files moving backwards in time, are both caught. An edit that changes content while keeping the byte count identical *and* lands within the same 100ns tick is still theoretically missable; this is not reachable in practice.
- A broken edit no longer fails requests in flight. The cost is that the router can be serving stale settings with nothing in `status`, `doctor` or the dashboard saying so — tracked as a follow-up in `.scratch/v2-stage1-followups/issues/03-surface-stale-settings.md`.
- Comparing a tuple per request instead of one float is a stat call per watched file rather than one. At three files, on the request path of a network-bound operation, this does not signify.
