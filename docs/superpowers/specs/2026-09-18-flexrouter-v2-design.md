# flexrouter v2: Service-First Router with a Learned Decision Layer

**Date:** 2026-09-18
**Status:** Approved design, ready for implementation planning
**Supersedes:** the remediation direction in `PLAN.md` (V1 patching)
**Plain-language companion:** `PLAN-V2.md` at the repo root

## In plain English

One background service. It pretends to be OpenAI, so any app points at it and
needs no flexrouter code. It holds every key and every setting in one place, so
no app carries its own copy. It records what actually happened on every request
instead of throwing the evidence away. A small classifier model learns what
providers' error messages mean and what unfamiliar models can do — once each —
and remembers, cautiously, with everything correctable by hand.

---

## Summary

Restructure flexrouter around a single long-lived local service that speaks the
OpenAI wire protocol, owns all credentials and state, and records a complete
decision trace for every request. Demote the Python library to a thin HTTP
client. Add a **decision layer**: a small structured-output model that
classifies unseen provider error text and infers attributes for newly
discovered models, writing typed, sourced, hand-correctable facts to disk.
Replace blind key rotation with per-key state. Keep V1's failure handling
verbatim.

## Motivation

Five concrete faults, each traceable to real observed behaviour:

1. **Config and credentials are per-app.** `config.discover_config()` walks a
   cwd search chain, so every consuming project ends up with its own
   `flexrouter.yaml` and its own copy of the keys. Changing a key means changing
   it everywhere. The owner's stated top complaint.
2. **Credentials live in the config file.** The working `flexrouter.yaml`
   contains live API keys in plaintext, and `dashboard/api.py::post_config()`
   will `yaml.dump()` the whole file — destroying comments and round-tripping
   secrets. Config export therefore hands out live keys.
3. **The router cannot say what is broken.** V1 discarded provider error bodies
   at the failure site. `errors.py` and `engine._skip_reason()` were added late
   and are not yet threaded into a durable per-request record; `audit.csv` has
   nine columns and no error text.
4. **The model catalogue rots silently.** 30 of 96 configured models no longer
   exist (Cerebras 2, Google 4, OpenRouter 18, Ollama 6). Nothing notices until
   live traffic fails.
5. **Key rotation is blind.** Multi-key selection is `counter % len(keys)`, so a
   key that has just been rate-limited re-enters rotation immediately, and a
   single rejected key quarantines its whole provider.

## Non-Goals

Each of these was considered and explicitly rejected by the owner. Do not
reintroduce them without a new decision.

- **Active health probing of individual models.** modelrelay pings every model
  on a stagger with a 1-token completion, costing ~15–20K requests/day against
  the same free-tier allowance this project exists to conserve. Rejected:
  *"modelrelay is stupid and this wastes tool calls."* This also restates the
  existing decision in `2026-06-19-dashboard-overhaul-design.md`. The
  replacement is one catalogue call per provider per day, plus learning from
  real traffic.
- **Publishing a self-updating signed model catalogue.** FreeLLMAPI does this
  well because it has contributors. Rejected: *"freellmapi has contributors.
  this project has me who updated this project every time i need to use it."*
  An unmaintained feed is worse than no feed.
- **Mid-stream provider failover.** Every project that claims it either cannot
  demonstrate it or corrupts tool-call deltas. On a post-first-delta failure,
  emit a well-formed error chunk and close.
- **Semantic / similarity caching.** Requires an embedding call per request and
  returns confidently wrong answers at any practical threshold.
- **Teams, RBAC, multi-tenancy, virtual keys.**
- **A nested declarative routing DSL** (Portkey-style). Flat buckets are a
  feature for a single owner.
- **Auto-starting the service.** Rejected twice. The library fails loudly with
  the exact start command instead.
- **Redis or any external datastore.** Single machine, single service.

## Architecture Overview

```
Any app (Python, Node, curl, an IDE, an editor plugin)
        │  OpenAI wire protocol, base_url = http://127.0.0.1:4891/v1
        ▼
┌──────────────────────────────────────────────────────────────┐
│ flexrouter service (FastAPI, single process, single writer)  │
│                                                              │
│  OpenAI surface ──► RoutingEngine ──► AsyncClient ──► provider
│   /v1/chat/…        (UNCHANGED)        (per-key)             │
│   /v1/models          │                    │                 │
│                       │                    ▼                 │
│                       │              ProviderError ──┐       │
│                       │                              ▼       │
│                       │                      Decision layer  │
│                       │                   ┌──────────────┐   │
│                       ▼                   │ error brain  │   │
│                 TraceWriter ◄─────────────│ model facts  │   │
│                       │                   └──────┬───────┘   │
│                       │                          │ miss only │
│                       │                          ▼           │
│                       │                   typesafe/jev-1.13  │
│                       ▼                                      │
│                 state dir (single writer, atomic replace)    │
│                       ▲                                      │
│                 read-only HTTP API ──► React dashboard       │
└──────────────────────────────────────────────────────────────┘
        ▲
        │ thin HTTP client; raises with the start command if absent
   flexrouter Python library
```

The service is the only writer. This removes the multi-process state-corruption
hazard that made Redis look necessary in `resilient-llm-gateway`: there is one
process, so file state with atomic replace is sufficient and correct.

---

## 1. Config, credentials, and one fixed home

### Locations

| Path | Contents | Written by |
|---|---|---|
| `%LOCALAPPDATA%\flexrouter\config.yaml` | Buckets, providers, owner's own comments | **Human only** |
| `%LOCALAPPDATA%\flexrouter\keys.json` | Credentials, mode `0600` / Windows ACL to the current user | Service |
| `%LOCALAPPDATA%\flexrouter\overrides.json` | Everything the dashboard changes | Service |
| `%LOCALAPPDATA%\flexrouter\state\` | Traces, brains, per-key state, catalogue | Service |

`FLEXROUTER_HOME` overrides the root. `config.discover_config()`'s cwd search
chain is **deleted** — it is the mechanism behind fault 1.

### Credential resolution order

1. Value stored via the dashboard (`keys.json`)
2. Environment variable named in config (`api_key_env`)
3. Inline `api_key` in config — **deprecated**; loading one emits a loud warning
   naming the file and line, and the dashboard offers one-click migration into
   `keys.json` followed by removal from the YAML.

### The config file is read-only to the machine

Nothing writes `config.yaml`. Dashboard edits land in `overrides.json` and are
merged over the parsed config at load. The merge is shallow per model/provider
key, and the dashboard shows which fields are overridden and offers a revert.
Comments in the hand-written YAML survive permanently, because nothing
round-trips it.

### `keys.json` shape

```json
{
  "openrouter": [
    {
      "id": "or-main",
      "label": "Main account",
      "secret": "sk-or-v1-…",
      "added_at": "2026-06-12T09:04:11Z",
      "weight": 2,
      "allow_models": ["*"],
      "enabled": true
    }
  ]
}
```

`allow_models` uses `fnmatch` globs (lifted from lmrelay); `["*:free"]` restricts
a key to free-tier variants. Secrets are never returned by any HTTP endpoint;
the API returns `masked` (`…e8d3`) only. `app.py::_mask()` already exists.

---

## 2. The OpenAI surface

`app.py` already implements a partial OpenAI-shaped surface
(`_parse_model_to_tier`, `_kwargs_from`, `_sse`). Complete it.

| Endpoint | Behaviour |
|---|---|
| `POST /v1/chat/completions` | Streaming and non-streaming |
| `GET /v1/models` | Buckets first, then individual models, OpenAI shape |
| `GET /v1/models/{id}` | Single entry |

- The request's `model` field names **a bucket** (`smart`, `fast`) or a specific
  `provider/model`. Buckets are the normal path — this is LiteLLM's "one name,
  many deployments", which is the single structural idea worth taking from it.
- **Forward stream deltas without re-parsing** where the provider's dialect
  already matches. LiteLLM's tool-call drops (BerriAI/litellm#17246) come from
  re-parsing; do not repeat it.
- On failure **before** the first delta: normal routing failover.
- On failure **after** the first delta: emit one well-formed error chunk, then
  `[DONE]`, then close. Never silently switch models mid-answer.
- Errors are returned in OpenAI's error envelope (`openai_error()` exists) with
  the provider's own message preserved in the body, not flattened to
  "server_error".
- Auth: optional local bearer token in `config.yaml`. Bound to `127.0.0.1` by
  default.

### The library becomes a client

`FlexRouter` keeps its current method signatures but performs HTTP against the
service. If the service is unreachable it raises exactly one error:

```
ServiceNotRunning: flexrouter isn't running. Start it with:

    flexrouter serve

(then this will work). Checked http://127.0.0.1:4891 — nothing listening.
```

No auto-start, no silent local fallback. A silent fallback would resurrect
fault 1 by letting each process keep its own state.

---

## 3. The request trace

One JSON object per request, appended to `state/traces.jsonl`. This is the
foundation for the dashboard, the error brain, and the attribute corrections.

```json
{
  "id": "req_01JBQ…",
  "at": "2026-09-18T14:02:11.482Z",
  "asked": {
    "bucket": "smart",
    "stream": true,
    "needs": ["tools"],
    "approx_input_tokens": 1840
  },
  "skipped": [
    {"provider":"groq","model":"llama-3.3-70b-versatile",
     "reason":"provider_quarantined","detail":"bad_key since 2026-09-14"},
    {"provider":"openrouter","model":"undi95/toppy-m-7b:free",
     "reason":"context_too_small","detail":"4k < 1840 tokens + headroom"}
  ],
  "attempts": [
    {"n":1,"provider":"openrouter","model":"qwen/qwen3-235b-a22b:free",
     "key_id":"or-spare","status":429,
     "provider_message":"Rate limit exceeded for requests per minute",
     "verdict":"too_fast","ms":210}
  ],
  "answered_by": {"provider":"openrouter","model":"deepseek/deepseek-chat-v3.1:free","key_id":"or-main"},
  "tokens": {"in":1840,"out":612},
  "cost_usd": 0.0,
  "ms_total": 3120,
  "ms_to_first_token": 640,
  "ok": true
}
```

Non-negotiable properties:

- **`skipped` is populated.** `engine._skip_reason()` already computes this and
  V1 discards it. Seven bare `continue` statements previously threw away the
  answer to "why didn't it use the good one?"
- **`provider_message` is the provider's verbatim text**, via
  `errors.extract_error_message()` (which already handles Google's
  array-wrapped errors).
- Rotated daily, retained 30 days, compacted like `health_history.py` does.
- `audit.csv` is retained unchanged for backwards compatibility.

---

## 4. The decision layer

One small structured-output model doing the classification jobs that would
otherwise be hand-maintained forever. Two consumers, one interface.

```python
class Decider(Protocol):
    def classify_error(self, text: str, status: int | None) -> ErrorVerdict: ...
    def describe_model(self, model_id: str, provider: str,
                       published: dict) -> ModelFacts: ...
```

Default implementation: `typesafe/jev-1.13` — a System One structured-decision
model returning a typed choice rather than prose, which is the correct shape
here. **Keep it behind the protocol.** A `NullDecider` that returns `unknown`
for everything must be a supported configuration, so the router works fully
without any classifier configured.

Both halves share the same discipline:

- Every stored decision carries `source`, `confidence`, `decided_at`.
- **Manual entries are never overwritten**, only flagged when reality disagrees.
- **Below `confidence_threshold` (default 0.80), flag for review; do not act.**
- The classifier is consulted on a **miss only**. Steady-state cost is ~zero.

### 4a. The error brain

`state/error_brain.json`, keyed by a normalised fingerprint of the error text:
lowercased, digits and hex ids replaced with `#`, whitespace collapsed, clipped
to 200 chars.

```json
{
  "rate limit exceeded for requests per #": {
    "verdict": "too_fast",
    "source": "rule",
    "confidence": 1.0,
    "seen": 1284,
    "first_at": "2026-06-12T…", "last_at": "2026-09-18T…",
    "sample": "Rate limit exceeded for requests per minute"
  }
}
```

**Built-in rules run first** and never reach the classifier: unambiguous status
codes (401/403 → `bad_key`, 402 → `needs_payment`, 404/410 → `model_gone`,
429 → `too_fast`, 5xx → `their_end_temporary`) and a small set of known
substrings. Only genuinely unrecognised text is classified.

| Verdict | Router action | Scope |
|---|---|---|
| `too_fast` | Back off to the provider's stated reset, minimum 30s | key |
| `bad_key` | Bench the key; do **not** retry | key |
| `needs_payment` | Quarantine the provider; surface in "Needs you" | provider |
| `model_gone` | Quarantine the model 24h, propose removal | model |
| `their_end_temporary` | Try the next candidate immediately | attempt |
| `message_too_long` | Retry on a larger-context model in the same bucket | attempt |
| `bad_request` | Do not retry; **feed to 4b as capability evidence** | attempt |
| `unknown` | Treat as `their_end_temporary`; flag for review | attempt |

This mapping replaces nothing in `recovery.py` — it *selects between* the
quarantine and backoff behaviours that already exist and already work.

### 4b. Model facts

`state/model_facts.json`, one entry per `provider/model`. Written when the daily
catalogue refresh finds a model not already known.

```json
{
  "openrouter/google/gemma-3-27b-it:free": {
    "vision":      {"value": true,  "source": "observed", "confidence": 1.0},
    "tools":       {"value": true,  "source": "doubted",  "strikes": 1,
                    "last_success_at": "2026-09-11T…",
                    "evidence": ["req_01JBQ…"]},
    "reasoning":   {"value": false, "source": "guessed",  "confidence": 0.74},
    "context":     {"value": 96000, "source": "published"},
    "size_class":  {"value": "medium", "source": "guessed", "confidence": 0.81},
    "provisional_score": {"value": 60, "source": "benchmark",
                          "note": "HumanEval 71.2", "at": "2026-09-12"}
  }
}
```

**Read before guessing.** Take any value the provider publishes in its own
`/v1/models` payload — context window always, capability flags where present.
The classifier only fills genuine gaps, which in practice means inferring from
the model id and family.

A `provisional_score` exists so a newly discovered model is usable immediately
rather than stuck unranked and never selected. It is superseded by §8.

#### Capability state machine (deliberately lenient)

A capability is `yes`, `doubted`, or `no`.

```
            confident contradicting failure        two more, separate requests
   yes  ─────────────────────────────────────►  doubted  ──────────────────────►  no
    ▲                                              │                              │
    └──────────────── any success ─────────────────┘                              │
    ▲                                                                             │
    └──────────────── ~30 days elapsed, revert to doubted ────────────────────────┘
```

Rules:

- One contradicting failure → `doubted`. Never straight to `no`.
- `doubted` remains **selectable**, ranked last for requests needing that
  capability.
- Three total contradicting failures, on **separate** requests, → `no`.
- **Any success resets `strikes` to 0 and restores `yes`.** Success is stronger
  evidence than failure: a thing that works cannot be a thing that doesn't.
- A failure counts as evidence only when the error brain returns a verdict of
  `bad_request` or `model_gone` **at or above the confidence threshold**, and the
  request actually exercised that capability. Ambiguous or unrelated failures
  are ignored for this purpose.
- Overriding a `published` value requires **five** strikes, not three, and
  raises a "the provider's own documentation is wrong" notice.
- A `manual` value is never overridden; a contradiction raises a notice and
  changes nothing.
- An observed `no` **goes stale after 30 days** and reverts to `doubted`.
  Providers upgrade models without announcing it.

#### Selection preference

When a request requires a capability, order candidates:
`published` / `observed` → `guessed` → `doubted`. Never `no`.

---

## 5. Per-key state

`state/key_state.json`, keyed by `provider:key_id`. Survives restart — the
`llmrouterx` review flagged in-memory-only circuit state as its main flaw.

```json
{
  "openrouter:or-spare": {
    "status": "cooling",
    "until": 1789412340.2,
    "reason": "too_fast",
    "consecutive_failures": 1,
    "failures_24h": 3,
    "requests_today": 194,
    "tokens_today": 610422,
    "last_used_at": 1789412298.1,
    "active_requests": 0
  }
}
```

- `status` ∈ `live` | `cooling` | `benched` | `disabled`.
- `bad_key` → `benched`. Other keys at the provider **keep working** — this is
  the granularity fix. Provider-wide quarantine is reserved for
  `needs_payment`, and for the case where *every* key at a provider is benched.
- `cooling` uses the provider's stated reset time when it gave one, floored at
  30s. A cooling key is skipped, not retried blindly.
- **Selection strategies** (`Scheduler` protocol, per provider, configurable):
  `most_headroom` (default), `round_robin`, `fastest` (latency EMA),
  `weighted`. Saturated keys (`active_requests` ≥ cap) are skipped.
- `allow_models` globs are applied before selection.

---

## 6. Catalogue refresh

- Once per day per provider (configurable; `manual` permitted), **one** call to
  the provider's model list. `probe.stale_models()` already does the diff.
- Result is written to `state/catalog_pending.json`, **not applied**:

```json
{
  "openrouter": {
    "checked_at": "2026-09-18T03:00:04Z",
    "appeared": ["moonshotai/kimi-k2:free", "z-ai/glm-4.5-air:free"],
    "vanished": ["nousresearch/hermes-3-llama-3.1-405b:free"]
  }
}
```

- Newly appeared models get a `model_facts` entry (§4b) so the pending card can
  show what they are, but are **disabled until accepted**.
- Vanished models are proposed for removal; acceptance writes to
  `overrides.json`, never to `config.yaml`.
- Default on both: *ask me first*. Per-provider settings allow auto-apply.
- Manual model entry writes the same structures.

## 7. Dashboard

Built to the approved mock-up (`claude.ai/artifact/DNVgao1ibNhXfbPsEP48xw`).
Existing stack: React 18 + Vite + Tailwind 4 + shadcn, built into
`flexrouter/dashboard/static/`.

- **Needs you** strip: only items the owner can fix and the service cannot, each
  quoting the provider verbatim and naming the concrete remedy.
- **Provider rows**: status, address, key count, models alive vs gone, allowance
  meter, last check, re-test.
- **Keys pane**: masked value with reveal, usage against cap, rotation state and
  the reason for it, weight, allowed models, per-key failure counts.
- **Models pane**: score with provenance, capabilities **rendered by source**
  (solid = published, green = observed, dashed = guessed, amber-? = doubted,
  ringed = manual), state, last-50-real-calls strip, typical latency, usage,
  last problem.
- **What it's learned**: the error brain and the model-facts tables, with
  below-threshold entries surfaced for confirmation and a manual override on
  every row.
- **Test buttons send one real request**, not a model-list lookup. The Cerebras
  case proves a list lookup is not evidence: the key is valid, the list loads,
  and completions still fail with 402.

## 8. Ranking models

- Builds a prompt from the current model list plus owner-supplied benchmark
  material (pasted text, or an instruction to go and look).
- **Two modes**: the service calls a chosen model itself, or the owner copies
  the prompt into their own chat and pastes the reply back.
- Output parsed as CSV: `model_id,score,reason,source`.
- Presented as current → proposed with per-row acceptance. Nothing applied until
  accepted. Large jumps are flagged.
- Applied scores record source and date. `manual`-pinned scores are untouched.
- Supersedes `provisional_score` from §4b.

---

## Reused unchanged

These are better than the commercial equivalents by the assessment of three
independent reviews. **Do not refactor them as part of this work.**

| Module | Why it stays |
|---|---|
| `recovery.py` | Permanent vs transient distinction, provider-wide vs model quarantine, reasons recorded. Strictly better than LiteLLM's uniform 30s cooldown. |
| `engine.py` selection | Score-based pick within a bucket, strict bucket isolation, `_skip_reason()`, `explain_unavailable()`. |
| `window.py`, `quota.py`, `rate_limits.py` | Sliding window, persistent rpd/rph, header-derived limits. |
| `errors.py` | Provider error extraction including Google's array-wrapped form. |
| `client.py` | `ProviderError.is_permanent` / `is_provider_wide`. |

## Migration

1. `flexrouter migrate` copies the existing `flexrouter.yaml` to the fixed home,
   lifts inline keys into `keys.json`, and **rewrites the YAML only to blank the
   secrets**, leaving all other bytes and comments untouched.
2. First run performs a catalogue refresh and files the ~30 known-dead models in
   the pending tray rather than deleting them.
3. `flexrouter doctor` prints the resolved home, which credential source won for
   each provider, and anything unreadable.

## Testing

- **TDD throughout**, matching how `probe.py` and `errors.py` were built: real
  captured provider payloads as fixtures.
- Error-brain fixtures must include the two confirmed live cases — Groq's
  `invalid_api_key` body and Cerebras' 402 — plus Google's array-wrapped error.
- The capability state machine gets exhaustive unit tests: single failure →
  doubted; success mid-sequence → reset; three failures → no; published needs
  five; manual never overridden; staleness reverts.
- `NullDecider` path must pass the full suite, proving no hard dependency on a
  classifier.
- Endpoint tests monkeypatch the decider and `probe_key`; **do not use respx**
  with FastAPI's `TestClient` — they collide (a known trap in this repo).
- Selection tests must use widely separated scores (99 vs 40); the engine picks
  randomly within 20% of the top, which has already caused one confusing
  failure.

## Open questions

1. Bucket names and membership for the new config.
2. Whether the 13.5KB `flexrouter.yaml` is migrated once or rebuilt in the UI.
3. Whether `flexrouter/server.py`, `tests/test_server.py`,
   `flexrouter/dashboard/server.py`, `tests/test_dashboard_server.py` — all
   superseded and currently unreferenced — are deleted. Asked three times,
   unanswered.
4. Confirm `typesafe/jev-1.13` pricing and access before it becomes the default
   decider; ship with `NullDecider` as the fallback until confirmed.
