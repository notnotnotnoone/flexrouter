# Issue 01: nesting two TestClients leaks a LocalRouter and its config watchdog

Status: ready-for-agent

## What

`create_app()` in `flexrouter/app.py` sets `state.router = None` without closing
whatever router was already there. `tests/test_dashboard_pages.py` builds a
second app inside the `client` fixture's live app (the HTML-injection tests need
a second config), so the outer lifespan later finds `None` and closes nothing.
One orphaned `LocalRouter` plus its config-file watchdog survives per run.

## Why it is not urgent

Pre-existing `app.py` lifecycle behaviour, merely exercised for the first time
by Stage 8's tests. Nothing leaks in real use — `flexrouter serve` builds one
app and one router. The cost is a stray object and a watcher thread inside the
test process.

## Done when

- `create_app` either closes an existing router before replacing it, or the
  module-level `state` singleton is replaced by something that cannot be
  silently overwritten.
- A test proves a second `create_app` does not strand the first router.

## Constraint

This touches the lifecycle `/v1/*` depends on. It was deliberately kept out of
a dashboard sub-plan for that reason. Whoever picks it up should treat it as an
app-lifecycle change, not a test fix.
