# Concepts: How flexrouter Works

**Goal:** Understand the mental model behind flexrouter — how it routes requests, manages limits, and handles failures.

---

## The Core Idea

When you call `router.generate()`, you're asking: **"Give me the best available model in this tier, right now."**

flexrouter handles the complexity:
- Picking the best model based on availability and scoring
- Respecting rate limits (RPM, TPM)
- Handling provider failures and retries
- Tracking costs
- Remembering which model you used last (optional)

You just call `generate()` and get back an OpenAI-compatible response.

---

## Request Flow (Simplified)

```
router.generate(messages=[...], tier="low", wait=True)
  │
  ├─ Load config (hot-reloaded if changed)
  │
  ├─ Filter models for tier "low"
  │
  ├─ Apply filtering rules:
  │   ├─ Skip penalized models (in "penalty box")
  │   ├─ Skip models over daily provider budget
  │   ├─ Skip models exceeding RPM/TPM windows
  │   └─ Log ContextWindowWarning if context window too small
  │
  ├─ Score remaining models (1–100)
  │   └─ Pick best; random among top 20% to avoid thundering herd
  │
  ├─ If no models available:
  │   ├─ wait=True  → sleep until next slot, retry
  │   └─ wait=False → raise RouterBusy
  │
  ├─ Send request to provider via HTTP
  │
  ├─ Check response:
  │   ├─ 2xx success → return response
  │   ├─ 429 rate limit → penalty box, retry next model
  │   ├─ 5xx server error → penalty box, retry next model
  │   └─ empty/refusal → ContextWindowWarning, short penalty
  │
  ├─ Log to audit.csv
  ├─ Update health.json
  └─ Return OpenAI-compatible response dict
```

---

## Tier Selection

You pass a tier name:

```python
response = router.generate(messages=[...], tier="low")
```

flexrouter looks up all models in the `low` tier and picks one. Tiers are **isolated** — a request to `low` will never use a model from `medium` or `high`.

### Why Tiers?

Tiers let you trade off cost, speed, and quality:

- **low** → cheap, fast, acceptable quality (draft generation, brainstorming)
- **medium** → balanced (most requests)
- **high** → expensive, slow, best quality (complex reasoning, creative writing)

You decide per-call which tier fits your use case.

---

## Scoring and Model Selection

Within a tier, models have a **score** (1–100). Higher score = preferred.

When you request a tier:

1. Filter to available models (not penalized, not rate-limited, not over budget)
2. Score each: higher score wins
3. **Avoid thundering herd:** Instead of always picking the #1 model, randomly select from the top 20% of available models

This spreads load and prevents one model from being hammered.

### Example

Tier `low` has three models:

| Model | Score | Available? |
|-------|-------|-----------|
| llama-3.1-8b | 85 | ✅ |
| mistral-small | 70 | ✅ |
| gpt-4o-mini | 60 | ❌ (rate-limited) |

Top 20% of available = only llama-3.1-8b (score 85). Always picked.

**Another scenario:**

| Model | Score | Available? |
|-------|-------|-----------|
| llama-3.1-8b | 85 | ✅ |
| mistral-small | 82 | ✅ |
| gpt-4o-mini | 75 | ✅ |

Top 20% = scores 85–83 (llama-3.1-8b qualifies, mistral-small qualifies). Randomly pick one.

---

## Rate Limiting: RPM and TPM Windows

flexrouter enforces **local rate limits** using sliding windows.

### RPM (Requests Per Minute)

You set `rpm: 60` for a model. flexrouter tracks the last 60 seconds of requests and blocks if the limit is hit.

**Example:**

```yaml
tiers:
  low:
    - provider: groq
      model: llama-3.1-8b-instant
      rpm: 60
      tpm: 60000
```

You can make at most 60 requests in any 60-second window. If you hit 60, the 61st request waits until the oldest request ages out of the window.

### TPM (Tokens Per Minute)

Same idea, but tracking tokens instead of requests.

If you set `tpm: 60000`:
- A request with 1,000 input tokens + 500 output tokens = 1,500 tokens
- That counts against your TPM budget
- If you hit 60,000 tokens in 60 seconds, the next request waits

### Reading Remaining Capacity

Call `router.remaining_capacity(tier)` to see how much of each model's RPM/TPM allowance is
still free right now, without making a request:

```python
capacity = router.remaining_capacity("default")
# {"googleai/gemini-2.5-flash": {"rpm_remaining": 12, "tpm_remaining": 940000}, ...}
```

Only models currently in the running for selection (the same top-20%-by-score-among-available
pool `generate()` picks from) are included. Useful for sizing a request before sending it,
rather than finding out it doesn't fit after the fact.

### Window Duration

Configure in settings:

```yaml
settings:
  window_seconds: 60
```

Both RPM and TPM use this same window. (You can't set them separately.)

---

## Penalty Box: Exponential Backoff

When a model fails (429, 5xx, or empty response), it goes into the **penalty box** for a duration, then gradually recovers.

### How It Works

| Failure | Penalty | Next attempt |
|---------|---------|--------------|
| 1st | 30s | After 30s |
| 2nd | 60s | After 60s |
| 3rd | 120s | After 120s |
| ... | doubles each time | ... |
| max | 1800s (30 min) | After 30 min |

Once a model is penalized, flexrouter skips it and tries the next-best model in the tier.

### Recovery

After the penalty expires, the model is un-penalized and available again.

**Good for:**
- Temporary provider outages (model recovers when provider is back)
- Rate limit bursts (provider recovers when bucket refills)
- Cascade failure prevention (if model is struggling, give others a chance)

---

## Context Window Management

Each model has a `context_window` (max tokens it can handle).

### Built-in Hook: `estimate_tokens`

If you enable this hook:

```yaml
settings:
  hooks:
    - estimate_tokens
```

flexrouter counts tokens in your request. If tokens exceed a model's `context_window`:
- ⚠️ Log `ContextWindowWarning`
- Skip that model
- Try next-best model in tier

If all models in tier are too small:
- Log warning
- Raise `ContextWindowWarning` exception (unless caught)

### Manual Check

You can also check proactively:

The service must be running for any of this (`flexrouter serve`); `FlexRouter`
sends the request to it and does not route by itself.

```python
from flexrouter import FlexRouter

router = FlexRouter()

# Check context fit
try:
    response = router.generate(
        messages=long_messages,
        tier="low",
    )
except ContextWindowWarning:
    print("All low-tier models too small. Try 'medium' tier.")
    response = router.generate(
        messages=long_messages,
        tier="medium",
    )
```

---

## Budget Tracking

Set a daily spending limit per provider:

```yaml
settings:
  provider_budget:
    openai: 5.00
    anthropic_compat: 3.00
```

Each request's cost (inferred from `usage.completion_tokens`) is deducted. Once a provider hits its budget, it's skipped for the rest of the day (UTC midnight).

### Budget Reset

Budgets reset at **00:00 UTC** each day. If you have multiple time zones, use UTC.

### View Remaining Budget

Check the dashboard **Account Status** tab (or the TUI's **Overview** tab), or read `.flexrouter/health.json`:

```json
{
  "providers": {
    "openai": { "daily_cost_usd": 2.45, "budget_usd": 5.00 }
  }
}
```

---

## Session Stickiness

By default, each call picks a fresh model. But sometimes you want the same model for the entire conversation (for consistency).

Pass `session_id`:

```python
session_id = "user-123-conversation-456"

response1 = router.generate(
    messages=[{"role": "user", "content": "Hello"}],
    tier="low",
    session_id=session_id,
)

response2 = router.generate(
    messages=[
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": response1["choices"][0]["message"]["content"]},
        {"role": "user", "content": "Tell me more."},
    ],
    tier="low",
    session_id=session_id,
)
```

Both calls use the **same model**, even if it's not the highest-scoring available.

### TTL and Expiry

Sessions expire after `session_ttl_minutes` (default: 30). Once expired, the next call is free to pick any model.

```yaml
settings:
  session_ttl_minutes: 30
```

### Fallback on Unavailable

If the pinned model becomes unavailable (penalized, rate-limited, over budget):
- For that call only, flexrouter picks the next-best model
- **The pin is NOT reset** — future calls continue using the original pinned model (if it recovers)

This ensures you don't accidentally lose your session pin due to temporary hiccups.

---

## Error Handling

### RouterBusy

Raised when all models in a tier are rate-limited and `wait=False`:

```python
from flexrouter import RouterBusy

try:
    response = router.generate(
        messages=[...],
        tier="low",
        wait=False,
    )
except RouterBusy:
    print("All low-tier models are at capacity right now.")
    # Retry later, use a different tier, or queue the request
```

### RouterError

Raised when a provider returns an unrecoverable error (auth failure, repeated 5xx):

```python
from flexrouter import RouterError

try:
    response = router.generate(messages=[...], tier="low")
except RouterError as e:
    print(f"Routing failed: {e}")
    # Handle auth issues, provider outages, etc.
```

### ContextWindowWarning

Logged (not raised by default) when a model's context window is too small. You can catch it:

```python
import warnings
from flexrouter import ContextWindowWarning

with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    response = router.generate(messages=long_messages, tier="low")
    
    if any(issubclass(x.category, ContextWindowWarning) for x in w):
        print("Some low-tier models were too small.")
```

---

## Async vs. Sync

flexrouter offers both:

```python
# Sync (blocks)
response = router.generate(messages=[...], tier="low")

# Async (awaitable)
response = await router.agenerate(messages=[...], tier="low")
```

Internally, both use the same async engine. Sync just wraps it in a managed event loop. Use async if you're already in an async context (FastAPI, etc.). Use sync for scripts and traditional applications.

---

## Audit Logging

Every request is logged to `.flexrouter/audit.csv`:

```
timestamp,tier,provider,model,prompt_tokens,completion_tokens,cost_usd,latency_ms,status
2026-05-27T12:01:00,low,groq,llama-3.1-8b,1204,320,0.00008,280,ok
2026-05-27T12:01:05,low,groq,llama-3.1-8b,0,0,0,0,rate_limited
```

This gives you full visibility into:
- Which models you're using
- Token consumption
- Cost per request
- Success vs. failure rates
- Latency trends

You don't have to open the CSV to see any of this: the dashboard's **Request
Logs** and **Live Telemetry** tabs and the TUI's **Requests** and **Overview**
tabs present the same data live.

Use this for:
- Cost analysis and budgeting
- Performance debugging
- Audit trails (compliance)

---

## Hot-Reload

Changes are detected automatically, checked on the next request. flexrouter watches every file its configuration can come from — your settings file, the `overrides.json` your dashboard changes are saved in, and the `keys.json` your credentials live in — and reloads when any of them changes.

It compares each file's timestamp *and* its size rather than a single newest timestamp, because a filesystem clock is only so fine: two writes can land in the same tick and read as identical, and a single newest timestamp also hides a file being replaced by an older copy.

A reload that fails leaves the previously loaded settings in place and serving. A broken edit does not fail requests already in flight.

**Preserved on reload:**
- ✅ Rate-limit windows
- ✅ Penalty state
- ✅ Session pins
- ✅ Audit logs

**Updated on reload:**
- ✅ Tier definitions
- ✅ Provider URLs and keys
- ✅ Settings

No restart needed. Your app stays running.

---

## Key Takeaways

1. **Tiers** isolate models by cost/quality. You pick per-call.
2. **Scoring** and **thundering herd prevention** pick the best available model.
3. **Rate limits** (RPM/TPM) are enforced locally using sliding windows.
4. **Penalty box** with exponential backoff protects against cascading failures.
5. **Budget tracking** prevents runaway costs.
6. **Session stickiness** pins a conversation to one model for consistency.
7. **Error handling** gives you control: catch `RouterBusy`, `RouterError`, `ContextWindowWarning`.
8. **Audit logs** track every request for visibility and compliance.

---

## Next Steps

- **Ready to use these concepts?** → [Getting Started](1-Getting-Started.md)
- **Need to configure something?** → [Configuration Guide](2-Configuration-Guide.md)
- **Want advanced patterns (sessions, budgets, dashboards)?** → [Advanced Usage](4-Advanced-Usage.md)
- **Looking for specific methods?** → [API Reference](5-API-Reference.md)
