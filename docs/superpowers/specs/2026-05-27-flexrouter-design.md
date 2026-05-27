# flexrouter — Design Spec

**Date:** 2026-05-27  
**Status:** Approved

---

## Overview

`flexrouter` is a Python library (`pip install flexrouter`) that routes LLM requests across multiple providers and models using a named-tier system. It replaces the individual routing code in funaithings, OpenVL, Colosseum, Atlas, and any future project with a single, well-tested package.

**Core promise:** `router.generate(messages, tier="low")` — one call, best available model in that tier, full rate-limit awareness, cost tracking, and a live dashboard.

---

## Architecture

```
flexrouter/
├── __init__.py          # Public API: FlexRouter class
├── config.py            # YAML parser, hot-reload watcher
├── engine.py            # Tier selection, model scoring, session stickiness
├── window.py            # Sliding window rate limiter (RPM + TPM)
├── client.py            # HTTP dispatch via httpx (OpenAI-compatible)
├── audit.py             # CSV audit log + health JSON writer
├── recovery.py          # Penalty box, exponential backoff
├── budget.py            # Per-provider daily spend tracking
└── dashboard/
    ├── server.py         # Lightweight Python HTTP server (/api/* + static)
    └── static/           # Pre-built React + Vite + Tailwind frontend
        └── index.html    # Ships with pip package, no npm needed by end users
```

**Data flow:**

```
router.generate(messages, tier="low", wait=True)
    │
    ├─ config: load flexrouter.yaml (hot-reloaded on mtime change)
    ├─ engine: filter models for tier "low"
    │   ├─ skip penalized models (recovery.py)
    │   ├─ skip models over daily provider budget (budget.py)
    │   ├─ skip models exceeding RPM/TPM windows (window.py)
    │   ├─ within-tier context window check → ContextWindowWarning if some skip
    │   └─ score remaining 1-100, pick best; random among top 20% to avoid thundering herd
    │
    ├─ if none available:
    │   wait=True  → sleep until next window slot, retry
    │   wait=False → raise RouterBusy
    │
    ├─ client: POST to provider base_url (OpenAI chat completions format)
    ├─ client: parse response headers → update RPM/TPM windows
    │
    ├─ on 429       → penalty box (30s → 60s → 120s → ... → 1800s), retry next model
    ├─ on 5xx       → penalty box, retry next model
    ├─ on empty/refusal response → ContextWindowWarning, short penalty (30s)
    │
    ├─ audit: append to .flexrouter/audit.csv
    ├─ audit: update .flexrouter/health.json
    └─ return: OpenAI-compatible response dict
```

**Key design decisions:**
- `FlexRouter` is stateful — one instance per app, holds all windows, session map, and audit state
- Sync `generate()` runs the async engine via a managed internal event loop; callers don't need asyncio unless they want it
- No global state — two `FlexRouter` instances are fully independent
- All state files written to configurable state dir (default: `.flexrouter/` next to config)

---

## Config File

Auto-discovered: `./flexrouter.yaml` → `~/.flexrouter.yaml`.  
Explicit path: `FlexRouter("path/to/flexrouter.yaml")`.  
Hot-reloaded: mtime watched, reloaded without restart (preserves rate-limit windows and penalty state).

```yaml
tiers:
  low:
    - provider: groq
      model: llama-3.1-8b-instant
      score: 85
      rpm: 60
      tpm: 60000
      context_window: 131072
    - provider: openrouter
      model: mistralai/mistral-small
      score: 70
      rpm: 40
      tpm: 80000
      context_window: 32768

  medium:
    - provider: groq
      model: llama-3.3-70b-versatile
      score: 90
      rpm: 30
      tpm: 60000
      context_window: 131072
    - provider: openai
      model: gpt-4o-mini
      score: 80
      rpm: 500
      tpm: 200000
      context_window: 128000

  high:
    - provider: openai
      model: gpt-4o
      score: 95
      rpm: 60
      tpm: 150000
      context_window: 128000
      vision: true
    - provider: anthropic_compat
      model: claude-sonnet-4-5
      score: 93
      rpm: 50
      tpm: 100000
      context_window: 200000
      vision: true

providers:
  groq:
    base_url: https://api.groq.com/openai/v1
    api_keys:
      - env: GROQ_API_KEY_1
      - env: GROQ_API_KEY_2       # round-robin across keys

  openai:
    base_url: https://api.openai.com/v1
    api_keys:
      - env: OPENAI_API_KEY

  openrouter:
    base_url: https://openrouter.ai/api/v1
    api_keys:
      - env: OPENROUTER_API_KEY

  anthropic_compat:
    base_url: https://api.anthropic.com/v1   # OpenAI-compatible endpoint
    api_keys:
      - env: ANTHROPIC_API_KEY

settings:
  state_dir: .flexrouter/               # audit.csv + health.json written here
  window_seconds: 60                    # sliding window duration
  penalty_base_seconds: 30
  penalty_max_seconds: 1800
  session_ttl_minutes: 30              # sticky session expiry
  dashboard_port: 7352

  retry_policy: balanced               # conservative | balanced | aggressive
  # Or manual override:
  # retries: 3
  # backoff_seconds: 2

  provider_budget:                     # max USD per provider per day
    openai: 5.00
    anthropic_compat: 3.00

  hooks:                               # built-in pre-routing hooks
    - detect_vision                    # auto-set vision=True if messages contain images
    - estimate_tokens                  # pre-check context window fit before dispatch
```

**Tier names are user-defined.** Call them `cheap`/`smart`/`nuclear` or anything else.  
**Score** (1-100) ranks models within a tier — higher = preferred when available.  
**`api_keys`** is a list for round-robin; single key also accepted as scalar string.  
**`api_key_env`** never stores the key itself — always an env var name.

---

## Public API

```python
from flexrouter import FlexRouter, RouterBusy, RouterError, ContextWindowWarning
import warnings

# Init
router = FlexRouter()                          # auto-discover config
router = FlexRouter("path/to/flexrouter.yaml") # explicit

# Sync
response = router.generate(
    messages=[{"role": "user", "content": "classify this..."}],
    tier="low",
    wait=True,               # block until slot available (default: True)
    vision=False,            # filter to vision-capable models only
    session_id="conv-abc",   # sticky routing — same model for this session
    max_tokens=512,          # passed through to provider
    temperature=0.2,         # passed through to provider
)

# Async
response = await router.agenerate(
    messages=[...],
    tier="medium",
    session_id="conv-abc",
)

# Response — standard OpenAI-compatible dict
text = response["choices"][0]["message"]["content"]
usage = response["usage"]  # prompt_tokens, completion_tokens, total_tokens

# Manual reload (hot-reload is automatic, but can force)
router.reload()

# Exceptions
try:
    response = router.generate(messages=[...], tier="low", wait=False)
except RouterBusy:
    # All low-tier models rate-limited right now
    ...
except RouterError as e:
    # Provider returned unrecoverable error (auth failure, repeated 5xx)
    ...

# Context window warning (not an exception — logged + within-tier fallback attempted)
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    response = router.generate(messages=long_messages, tier="low")
    if any(issubclass(x.category, ContextWindowWarning) for x in w):
        print("Some low-tier models skipped due to context length")
```

---

## Retry Policy

| Preset | Retries | Backoff |
|---|---|---|
| `conservative` | 2 | 5s |
| `balanced` | 3 | 2s |
| `aggressive` | 5 | 1s |

Manual override in config: `retries: N` + `backoff_seconds: N` (overrides preset).

---

## Error Handling & Recovery

| Condition | Action |
|---|---|
| 429 Too Many Requests | Penalty box: 30s → 60s → 120s → 1800s cap. Retry next model in tier. |
| 5xx Server Error | Same penalty box. Retry next model. |
| Empty / refusal response | `ContextWindowWarning` logged. 30s short penalty. Within-tier fallback. |
| Context window exceeded | Within-tier fallback to model with larger `context_window`. `ContextWindowWarning` raised. |
| Provider budget hit | Skip provider for rest of day. Log warning. Continue routing in tier. |
| All tier models unavailable | `wait=True`: sleep until slot opens. `wait=False`: raise `RouterBusy`. |
| Auth failure | Raise `RouterError` immediately. No retry. |

---

## Session Stickiness

`session_id` pins a conversation to a specific model for consistency across turns.

- TTL: configurable (`session_ttl_minutes`, default 30). Idle session expires and re-routes freely.
- If pinned model becomes unavailable (rate-limited, penalized): transparently re-routes to next best in tier for that call only. Does not reset pin.
- Session map held in-memory. Not persisted across process restarts.

---

## Multi-Key Round-Robin

Multiple API keys per provider rotate round-robin:

```yaml
providers:
  groq:
    api_keys:
      - env: GROQ_KEY_1
      - env: GROQ_KEY_2
      - env: GROQ_KEY_3
```

- Counter per provider, incremented on each request.
- If a key hits a 429: skip to next key immediately (don't wait for round-robin).
- Per-key penalty box identical to per-model penalty box.

---

## Dashboard

**Start:** `flexrouter dashboard` — launches Python HTTP server + opens browser to `http://localhost:7352`.

**Init wizard:** `flexrouter init` — opens browser to setup tab, walks through API key entry, generates `flexrouter.yaml`.

**Tech stack:** React + Vite + Tailwind CSS frontend, pre-built static files committed to repo. End users never run npm. Python `http.server` backend serves `/api/*` endpoints + static files.

**Tabs:**

1. **Live Telemetry** — models table grouped by tier. Columns: model, score, RPM bar, TPM bar, status dot, penalty countdown. Search + filter by tier/status/vision. Hover-pause. Click row → right-side drawer with full stats. Dark/light theme toggle.
2. **Chat** — test any tier live. Model dropdown shows which model was selected.
3. **Request Logs** — last 50 audit entries. Toggle: card view / table view. Pause live updates.
4. **Account Status** — per-provider key usage, daily budget remaining, round-robin index.
5. **Settings** — view/edit config, export/import config token, retry policy, budget limits.
6. **Setup** — `flexrouter init` target tab. Walks through provider setup.

**Backend API (polled every 2s):**

```
GET  /api/status    → tier health, model windows, penalties, budget
GET  /api/logs      → last 50 audit entries
GET  /api/config    → current parsed config
POST /api/config    → update config (triggers hot-reload)
```

---

## Audit & Persistence

**`.flexrouter/audit.csv`** — appended on every request:
```
timestamp,tier,provider,model,prompt_tokens,completion_tokens,cost_usd,latency_ms,status
2026-05-27T12:01:00,low,groq,llama-3.1-8b,1204,320,0.00008,280,ok
2026-05-27T12:01:05,low,groq,llama-3.1-8b,0,0,0,0,rate_limited
```

**`.flexrouter/health.json`** — updated on every request:
```json
{
  "total_cost_usd": 0.042,
  "session_start": "2026-05-27T11:00:00",
  "providers": {
    "groq": { "daily_cost_usd": 0.012, "budget_usd": null },
    "openai": { "daily_cost_usd": 0.030, "budget_usd": 5.00 }
  },
  "models": {
    "groq/llama-3.1-8b": { "rpm_current": 48, "tpm_current": 44000, "penalty_until": null }
  }
}
```

Add `.flexrouter/` to `.gitignore`.

---

## CLI

```bash
flexrouter init              # open browser setup wizard → generate flexrouter.yaml
flexrouter dashboard         # start dashboard at localhost:7352
flexrouter status            # print tier health to terminal (no browser)
flexrouter config export     # print portable config token (base64 encoded)
flexrouter config import <token>
```

---

## Built-in Hooks

Declared in `settings.hooks`. Run before routing decision.

| Hook | What it does |
|---|---|
| `detect_vision` | Scans messages for image content; sets `vision=True` automatically |
| `estimate_tokens` | Counts tokens in messages; skips models whose `context_window` would be exceeded |

No user-defined hooks in v1. Add later if needed.

---

## Packaging

- **Python minimum:** 3.11
- **PyPI:** `pip install flexrouter` — published on git tag via GitHub Actions
- **Dependencies:** `httpx`, `pyyaml`, `watchdog` (hot-reload), `tiktoken` (token estimation), `click` (CLI)
- **Dev dependencies:** `pytest`, `pytest-asyncio`, `respx` (mock httpx), `vite` + React + Tailwind (frontend, build only)
- **Pre-built frontend:** `flexrouter/dashboard/static/` committed to repo, included in package via `MANIFEST.in`

---

## What's Out of Scope (v1)

- Redis / distributed state (file-based only)
- Virtual keys / multi-tenant
- MCP / A2A integration
- Cross-tier fallback (strict isolation)
- User-defined pre-routing hooks
- WebSocket / SSE for dashboard (HTTP polling sufficient)
- Response caching
