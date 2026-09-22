# Issue 02: `build/` holds a stale copy of code that no longer exists

Status: ready-for-agent

## What

`build/lib/flexrouter/` contains an old copy of the package, including
`dashboard/server.py` carrying the "dashboard not built; run `npm run build`"
message that Stage 8 removed from the real source. Stage 8's regression guard
only scans `flexrouter/`, so it cannot see this copy.

It is an untracked build artifact, not source, which is why Stage 8 left it
alone rather than deleting it mid-branch.

## Done when

- `build/` is deleted.
- `build/` is in `.gitignore`. Note: an uncommitted `.gitignore` edit adding
  exactly this line was found in the working tree during Stage 8, written by a
  dispatch that was interrupted. It was deliberately not merged. Check whether
  it is still there before writing a duplicate.

## Why it matters at all

Only because a future grep-based guard or a reader could mistake it for live
code. Nothing imports it.
