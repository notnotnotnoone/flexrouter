# 0005. Four superseded files were deleted

Date: 2026-09-18
Status: Accepted

## Context

v1 ran two separate hand-rolled `http.server` processes — a dashboard on port 7352 and an OpenAI-compatible API on port 7353 — both driving a single `FlexRouter` instance across threads while the router itself owned one non-thread-safe asyncio event loop. Overlapping requests could raise "this event loop is already running" or deadlock outright; that isn't fixable with locking, only by giving one process ownership of the loop. Stage 1 replaces both with a single FastAPI app (`flexrouter/app.py`) that owns the loop and serves `/v1/*`, `/api/*`, and the dashboard SPA on one port.

## Decision

Delete the modules the new app makes redundant: `flexrouter/server.py`, `tests/test_server.py`, `flexrouter/dashboard/server.py`, `tests/test_dashboard_server.py`. `tests/test_public_surface.py` now asserts both `flexrouter.server` and `flexrouter.dashboard.server` are not importable, and that `start_server` (the old entry point) is gone from `flexrouter`'s public API.

## Consequences

- One server process, one port (4891 by default), no cross-thread event-loop hazard.
- `flexrouter.start_server()` no longer exists — any code or docs that called it directly needs to switch to `flexrouter serve` / `flexrouter dashboard` on the CLI, or to `flexrouter.app.create_app()` for programmatic use.
- The dual-port layout (7352 dashboard / 7353 API) is gone; anything hardcoding those ports needs updating to the single port.
