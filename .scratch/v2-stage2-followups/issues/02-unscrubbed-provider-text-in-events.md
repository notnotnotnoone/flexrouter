# Issue 02: provider text still reaches the machine's own event record unscrubbed

Status: ready-for-agent

## What

Stage 2 added `flexrouter/redact.py` and now scrubs provider text at the points where a
provider or a route is sidelined, so nothing unscrubbed is persisted there or served
over HTTP. Two paths were left out:

1. `flexrouter/_router.py` lines around 253, 395, 407 and 442 still record
   `detail=str(exc)` unscrubbed for a rate-limit failure. A 429 body that echoes the
   credential would put it in `state/events.csv`.
2. `flexrouter/events.py:34` opens `events.csv` with no explicit encoding, so it is
   written in whatever the platform's default codec is. The scrubber's `…` character
   now appears in every sidelined detail, so the file is reliably no longer valid
   UTF-8 on Windows. It does not raise here, because the Windows default codec can
   represent that character — but a legacy codec that cannot (cp437, for instance)
   would raise during a request.

## Why it is not urgent

Nothing serves that column: `EventLogger.recent()` has no non-test caller, and the
uptime view reads only the timestamp, provider, model and event type. So this is a
file on the owner's own machine, readable by his own account, not a response leak.

## Done when

- Rate-limit details are scrubbed the same way the sideline details are.
- `events.csv` is opened with an explicit `encoding="utf-8"` on both the write and the
  read path, so what is written can be read back.
- A test writes an event from an exception carrying a key-shaped string and asserts the
  key is not in the file on disk, and that the file round-trips as UTF-8.

## Relationship to the design

Spec section 3 (the request trace) rewrites this recording path. If Stage 3 lands
first, do it there and make the trace scrub by construction rather than at each site.
