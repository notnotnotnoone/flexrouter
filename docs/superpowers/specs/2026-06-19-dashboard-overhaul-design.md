# Dashboard Overhaul: Robust Stats, Uptime, and Observability

**Date:** 2026-06-19
**Status:** Approved design, ready for implementation planning

## Summary

Overhaul the flexrouter dashboard from a consumer-style app into an operational
dashboard. Add two new pages (**Stats**, **Uptime**) backed by a rewritten,
durable persistence layer that records health over time at **zero free-tier
quota cost**. Apply a cross-cutting **observability layer** so the UI never
hides an error and never *looks* frozen. Port modelrelay's animated KPI cards.
Make configuration errors loud and precise.

## Motivation

The current dashboard has three structural problems:

1. **No durable history.** Everything real — rpm/tpm (`window.py`), penalties
   (`recovery.py`) — lives in memory and dies on restart. `health.json` is a
   thin session snapshot that does not even contain a `models` key, so the
   existing "Live Telemetry" model table reads `status.models` which is
   effectively always empty.
2. **Errors are hidden.** `AccountStatus.tsx` swallows fetch errors with
   `.catch(() => {})`. `LiveTelemetry` leaves a permanent null state on failure,
   indistinguishable from a frozen app.
3. **Config failures are opaque.** Wrong-modality models (speech-to-text,
   text-to-speech, image, moderation classifiers) load fine but fail at request
   time with confusing errors. Validation does not catch them.

## Non-Goals

- **No "money saved" / cost-savings tracking.** Explicitly cut as
  overcomplicating. Cost columns in `audit.csv` remain but are not surfaced as a
  savings narrative.
- **No active background pinger by default.** flexrouter's purpose is conserving
  free-tier quota; spending quota on synthetic pings is self-defeating. Health
  sampling is **passive** (reads in-memory engine state only). An active pinger
  may be added later behind an explicit opt-in flag; it is out of scope here.

## Architecture Overview

```
Engine process (user's app)                 Dashboard server process
---------------------------                 ------------------------
RoutingEngine ──► AuditLogger ──► state_dir/ ◄── reads ── HTTP API
  - SlidingWindow (rpm/tpm)        audit.csv              /api/status
  - PenaltyBox (persisted)         health_history.jsonl   /api/stats
  - passive sampler thread         events.csv             /api/uptime
                                   health.json            /api/config/validate
                                                                │
                                                          React frontend
                                                          (usePolling hook)
```

The engine process owns all writes; the dashboard server is read-only over the
files in `state_dir` (plus existing config POST). They may be different
processes, so the **passive sampler must live in the engine/audit layer**, not
the dashboard server.

## Backend: Durable Persistence Layer

All files live in `state_dir` and are written atomically (temp + `os.replace`,
matching the existing `health.json` pattern).

### 1. `audit.csv` (unchanged)
Per-request append-only ledger. Source of truth for the Stats page. Columns:
`timestamp, tier, provider, model, prompt_tokens, completion_tokens, cost_usd,
latency_ms, status`.

### 2. `health_history.jsonl` (new)
Append-only health time-series, one JSON object per sample:

```json
{
  "timestamp": "2026-06-19T14:03:00Z",
  "models": {
    "groq/llama-3.3-70b-versatile": {
      "status": "up", "rpm": 4, "tpm": 1820,
      "penalized": false, "penalty_until": null, "latency_ewma_ms": 412
    }
  },
  "providers": { "groq": { "models_up": 6, "models_total": 8 } }
}
```

Written on **every request** and by an optional **passive sampler thread**
(config `sample_interval_seconds`, default 60). The sampler reads in-memory
engine state only — **no network calls, no quota cost** — so idle models still
emit "healthy/idle" samples and the time-series has no gaps.

**Retention:** rotate/compact entries older than a configurable horizon
(`health_history_days`, default 30) to bound file size. Compaction downsamples
samples older than 24h to one per 5 minutes.

### 3. `events.csv` (new)
Append-only incident ledger emitted by the engine when penalties/errors occur
during real traffic. Columns:
`timestamp, provider, model, event_type, detail, penalty_seconds`.
`event_type` ∈ `{penalized, recovered, rate_limited, timeout, server_error}`.
Powers penalty swimlanes, the reliability incident timeline, and uptime
incidents.

### 4. `PenaltyBox` persistence (modified `recovery.py`)
Switch the persisted expiry from `time.monotonic()` (meaningless across
restarts) to wall-clock `time.time()` for display/history, keeping monotonic for
live in-process checks. Persist penalty state to `state_dir` so penalties and
their history survive restart. Emit an `events.csv` row on `penalize()` and on
`clear()`/recovery.

### New Endpoints (`dashboard/server.py` + `dashboard/api.py`)
- **`/api/stats`** — full aggregation computed server-side from `audit.csv`
  (hourly/daily buckets, per-model rollups, P50/P95 percentiles). Avoids
  shipping tens of thousands of CSV rows to the browser. New module
  `dashboard/stats.py`.
- **`/api/uptime`** — per-model uptime %, status-timeline segments, and incidents
  derived from `health_history.jsonl` + `events.csv`. New module
  `dashboard/uptime.py`.
- **`/api/config/validate`** — config validation result (see Config Clarity).

## Stats Page — Five Categories

1. **Request Volume & Throughput** — requests/hour bar chart (24h), 7-day
   sparkline, KPI cards (total requests, peak RPM, requests this hour).
2. **Latency & Performance** — P50/P95 per-model horizontal bars, latency
   histogram (distribution buckets), time-of-day heatmap (hours × days).
3. **Provider & Model Distribution** — provider request-share donut, top-models
   ranked bars, routing-diversity score (concentration risk).
4. **Error Rate & Reliability (expanded)** — rolling 1h error-rate line,
   per-model error table (requests/errors/error %/top error type), error-type
   stacked bars per provider, **penalty swimlane** (Gantt of outages), mean time
   to recovery, cascade detection (3+ models penalized simultaneously),
   most/least-reliable model cards.
5. **Efficiency & Optimization (no dollars)** — latency-vs-quality scatter
   (fast + high-score = good), tier-utilization & fallback-depth breakdown (how
   often tier-0 succeeded vs fell back), "wasted attempts" metric (failed tries
   before a success).

## Uptime Page (status-page style)

- Per-model uptime % cards for 24h / 7d / 30d windows.
- Green/red/grey **segmented status-timeline bars** per model (from
  `health_history.jsonl`); grey = "no data" for idle periods, honestly distinct
  from "down."
- Incident list from `events.csv` with durations and MTTR.
- System-level + per-provider uptime headline.

## Frontend: Observability Layer

### `usePolling` hook (shared by ALL data sources)
Every async source — existing tabs retrofitted **and** new pages — flows through
one hook. No silent `catch` anywhere in the codebase.

State: `loading | ok | stale | error`, plus `lastData`, `lastUpdated`,
`inFlight`, full `error`.

- **On error:** keep showing last-good data (visually dimmed) + a loud inline
  banner naming endpoint, HTTP status, and raw message. The widget never blanks.
- **On in-flight:** a visible "refreshing…" pulse.
- **Stale:** if last success > 2× poll interval ago, show amber
  "STALE — updated Ns ago".
- **Timeouts:** all fetches use `AbortController`, so a hung request surfaces as
  a timeout error instead of an infinite spinner.

### Global chrome
- **Fixed top status bar:** backend connection dot (green/amber/red),
  "last sync Ns ago", poll cadence, version, and a **1-second ticking clock** so
  the UI is visibly alive even when data is stale.
- **Diagnostics console** (collapsible drawer): logs every error with timestamp,
  endpoint, status, message. Nothing swallowed.
- **Watchdog:** N consecutive failed polls escalates the status bar to red
  **"BACKEND UNREACHABLE"** with the raw error and a manual retry button.

### KPI cards (ported from modelrelay)
Three animated SVG cards sit **permanently above the tab bar** (in `App.tsx`):
- **Current Model** — spinning hexagon outline + pulsing core.
- **Models Online** — constellation of dots with connecting lines (bright =
  online).
- **Providers Online** — hub-and-spoke network (active links flow to a central
  hub).

Constant motion doubles as a liveness signal, reinforcing the anti-frozen goal.

### Aesthetic: dashboard, not consumer app
Denser grid, `tabular-nums` + monospace for IDs/numbers, operational
green/amber/red semantics, tighter radii, status-bar chrome — information over
decoration.

### Charting
Add **Recharts** for standard charts (line/area/bar/donut/scatter). Hand-roll
**two** SVG components Recharts does not do well: the time-of-day **heatmap** and
the penalty **swimlane**.

## Config Error Clarity

### Backend (`config.py` validation rewrite)
`load_config` collects **all** problems and reports them together, each naming
the exact tier + model index + field:
- Tier model references a provider not defined in `providers:` (caught at load,
  not at request time).
- Missing/invalid required fields; non-positive `rpm` / `tpm` / `score`;
  malformed `base_url`.
- Empty `api_keys` on a non-local provider → **warning**, not fatal (ollama is
  legitimately keyless).
- **Modality heuristic warning:** flags suspiciously low `context_window`
  (e.g. < 1000) or known non-chat name patterns (`whisper`, `tts`, `image`,
  `lyria`, `guard`) — the exact class of bug present in the original config.

Validation returns a structured result (errors + warnings) consumed by the new
`/api/config/validate` endpoint.

### Frontend
A loud, persistent **Config Health banner** at the top of the dashboard — red
for errors, amber for warnings — listing every issue with field location.

## Config Cleanup (already applied)

The live `flexrouter.yaml` was cleaned as part of this work (file stays
untracked — it contains live API keys):
- Removed 17 wrong-modality models (whisper STT ×2, orpheus TTS ×2, prompt-guard
  classifiers ×2, gemini TTS ×2, gemini image ×3, gemini native-audio ×3,
  gemini live ×1, lyria music ×2).
- Fixed `openrouter/owl-alpha` `context_window` typo `1048756` → `1048576`.
- Corrected Gemini Flash `context_window` `131072` → `1048576` (true 1M window;
  prevents wrongly skipping large prompts).
- Result: 55 models, all referencing valid providers, verified to load.

## Testing Strategy

- **Backend unit tests:** `stats.py` aggregation (buckets, percentiles, rollups)
  against fixture `audit.csv`; `uptime.py` segment/incident derivation against
  fixture `health_history.jsonl` + `events.csv`; `config.py` validation
  (multi-error collection, modality heuristics, provider-reference checks);
  `recovery.py` persistence round-trip across simulated restart.
- **Frontend:** `usePolling` state-machine tests (loading→ok→stale→error,
  timeout via aborted fetch, error never blanks last data); Config Health banner
  renders all issues; status bar watchdog escalation.
- **Manual:** kill the backend mid-session and confirm the UI shows
  "BACKEND UNREACHABLE" with raw error and never a blank/frozen screen.

## Open Risks

- **Engine ↔ dashboard process boundary:** the passive sampler must run in the
  engine process. If a deployment runs the dashboard standalone with no engine,
  `health_history.jsonl` will only contain request-driven samples — acceptable
  and honestly reflected by grey "no data" timeline segments.
- **File growth:** `health_history.jsonl` and `audit.csv` grow unbounded without
  the compaction/retention described; retention is part of scope.
