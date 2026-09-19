# Issue 03: Nothing tells the owner the router is running on stale settings

Status: ready-for-agent

## What

The running router reloads when the settings, overrides or credential file changes. If
that reload fails — a settings file edited into an invalid state, for instance — the
router deliberately keeps serving with the previously-loaded settings rather than
failing in-flight requests. That is the right behaviour.

But the failure is recorded only in the log and on the router object as
`_reload_error`. Nothing surfaces it in `flexrouter status`, `flexrouter doctor`, or
the dashboard. The owner can be running on settings that are hours out of date with no
way to notice from any interface the tool offers.

## Done when

- `flexrouter doctor` reports, when it applies, that the running service failed to
  reload and is serving older settings, with the reason and when it happened.
- The same appears in the dashboard's "Needs you" strip once that exists (spec §7).
- A test covers a failed reload followed by a report.
