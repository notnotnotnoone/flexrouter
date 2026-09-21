# 0014. Catalogue refresh runs on startup; applying a pending change is deferred

Date: 2026-09-21
Status: Accepted

## Context

Spec section 6 asks for two things: an automatic catalogue check (originally
"once a day," changed by the owner on 2026-09-20 to "once per service
start," since this service is started and stopped by hand rather than left
running unattended), and a way to apply what that check finds — accepting
a newly-appeared model into a bucket, or accepting a vanished model's
removal, with a per-provider setting to do either automatically.

`flexrouter/refresh.py`'s `refresh_config()` already existed, already
correct and already tested, from Stages 1-2: it makes the actual
per-provider calls, diffs against `config.yaml`, and writes
`state/catalog_pending.json` — but nothing had ever called it except a
human running `flexrouter refresh` or clicking the dashboard's refresh
button. The automatic trigger was the one genuinely missing piece for the
"check" half of this section.

The "apply" half needs something `overrides.json` cannot do today:
`flexrouter/overrides.py`'s `apply_overrides` only patches fields on a
model `config.yaml` already declares (`provider`/`model` are deliberately
excluded from the allowed-fields list, specifically so an override can
never turn one model's identity into a different one) — it has no way to
inject a brand-new model that doesn't exist in `config.yaml` at all. Adding
that capability, and deciding how an auto-apply setting should choose
which bucket a newly-appeared model lands in, is a change that can alter
which models the router actually serves — the same category of risk Stage
6 priced when it deferred letting `model_facts.json` influence selection.

## Decision

**This stage builds the check half only.** `LocalRouter.__init__` calls
the unmodified `refresh_config()` once, synchronously, wrapped so nothing
about the call — a slow provider, an unreachable one, an unexpected
exception — can prevent the service from starting.

*Cost:* service startup takes a little longer, once, for the round trip to
every configured provider. This is the accepted cost of "check on
startup" replacing a background timer, already priced in by the owner's
2026-09-20 decision.

**Newly-discovered models get exactly one fact recorded automatically**:
their published context window, via `ModelFactsStore.record_discovered()`,
read back from the `catalog_pending.json` entry `refresh_config()` already
wrote. Nothing else about a new model is inferred — there is no real
`Decider` configured (still `NullDecider`, which answers nothing), and
nothing about the model has been *observed* yet (Stage 6's evidence
wiring only fires from real traffic, which a never-yet-routed model has
had none of).

**The apply mechanism is deferred, not built.** Turning a pending appeared
model into something selectable, or a pending vanished model into an
accepted removal, needs: a new way for `overrides.json` to represent a
model identity that doesn't exist in `config.yaml`; a decision about which
bucket an auto-applied model lands in; and — for a manual "ask me first"
acceptance — some interface to say yes, which doesn't exist without either
a CLI command this stage didn't build or the dashboard this project
doesn't have yet (Stage 8). Filed as a follow-up
(`.scratch/v2-stage7-followups/issues/01-...md`) rather than rushed.

*Cost:* `state/catalog_pending.json` accumulates real, correct findings
that nothing acts on yet — exactly the state this codebase was already in
before this stage, just refreshed automatically now instead of only on a
manual command. No regression, a real but partial step forward.

## The nested-event-loop fix

The design above shipped once, then broke on the one path that actually
matters: `flexrouter serve`. `refresh_config()` calls `asyncio.run()`
internally to make its per-provider HTTP calls. `asyncio.run()` raises
`RuntimeError` unconditionally when called from a thread that already has
its own event loop running — and under `flexrouter serve`, `app.py`'s
`lifespan()` is exactly that: an `async def` that calls the sync
`get_router()` (and so `LocalRouter.__init__`) directly from its own body,
on uvicorn's event-loop thread. That is different from an ordinary `def`
route handler (e.g. every `/api/*` handler in `app.py`), which FastAPI
already offloads to a worker thread automatically, and so already got a
thread with no event loop of its own "for free" — those handlers could
have called `refresh_config()` directly and never hit this. `lifespan()`
gets no such offload from the framework; it runs on the loop's own thread
by design, so the exception fired every single time under uvicorn. The
`except Exception` wrapper this stage already had around the refresh call
caught it cleanly and logged it, so nothing crashed — but that also meant
the refresh silently never ran on the one path this whole stage was built
for. A first round of tests that constructed `LocalRouter` directly (no
event loop on the calling thread) could not see this, because that
constructor path never had one to collide with.

The fix: `_run_startup_catalogue_refresh` now submits `refresh_config()` to
a one-worker `concurrent.futures.ThreadPoolExecutor` and blocks on
`.result()`, instead of calling it inline. A freshly spawned worker thread
has no event loop of its own regardless of what the thread that spawned it
is doing, so `asyncio.run()` inside it succeeds unconditionally — whether
the caller is `lifespan()` on uvicorn's loop thread, a direct
`LocalRouter()` construction with no loop at all, or (as this task's own
e2e test exercises) a lazy `get_router()` call from inside an `async def`
route handler running on a `TestClient` request portal's own loop. The
startup cost is unchanged (still one synchronous round trip before the
service is ready to serve), and the failure-tolerance guarantee is
unchanged (any exception from the worker, including the old
`RuntimeError`, is still caught and logged rather than propagated) — only
which thread actually executes `refresh_config()`'s internals changed.
Verified by `tests/test_startup_refresh.py`'s
`test_startup_refresh_runs_cleanly_from_inside_the_fastapi_lifespan`, which
drives the real FastAPI `lifespan` via `with TestClient(app):` and asserts
`refresh_config` was actually called with no `RuntimeWarning` about an
unawaited coroutine or colliding loop, and independently by this task's
`tests/test_startup_refresh_e2e.py`, which proves the same fix holds on
the lazy-construction path (first request after `state.router = None`)
rather than the eager `lifespan` path.

## Consequences

- The pending tray this stage's own roadmap entry names has real data in
  it, automatically, from the first service start onward — Stage 8's
  dashboard (or a future CLI command) has something true to show and act
  on the moment it exists, without needing to also build the discovery
  side.
- `state/model_facts.json` entries for brand-new models carry a real,
  `published`-sourced context window from the moment they're discovered,
  even before any traffic has ever been routed to them.
- The next stage or follow-up that builds the apply mechanism inherits a
  known, load-bearing constraint it must solve rather than discover:
  `overrides.py`'s `ALLOWED_FIELDS` deliberately excludes model identity,
  and that exclusion is there on purpose (it stops an override from
  quietly turning one model into another) — whatever adds "inject a new
  model" needs its own, separate mechanism, not a weakening of that
  existing guarantee.
- Startup refresh now always executes on a dedicated worker thread rather
  than inline on whatever thread constructed `LocalRouter` — a detail
  future readers touching `_run_startup_catalogue_refresh` need to keep
  in mind, since removing the `ThreadPoolExecutor` wrapper would silently
  reintroduce the exact regression this section documents, with no test
  short of driving the real `lifespan` (or an equivalent already-running
  event loop) able to catch it.
