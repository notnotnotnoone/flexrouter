# Advanced Usage

**Goal:** Master session stickiness, multi-key round-robin, retry strategies, cost tracking, and the dashboard.

---

## Session Stickiness: Pinning to One Model

Use `session_id` to stick a conversation to one model across multiple turns.

### Basic Example

```python
from flexrouter import FlexRouter

router = FlexRouter()
session_id = "user-123-chat"

# Turn 1
response1 = router.generate(
    messages=[
        {"role": "user", "content": "What's your name?"}
    ],
    tier="low",
    session_id=session_id,
)
model_used_1 = response1.get("model")  # e.g., "llama-3.1-8b"
print(f"Turn 1 used: {model_used_1}")

# Turn 2 - same model is used (if available)
response2 = router.generate(
    messages=[
        {"role": "user", "content": "What's your name?"},
        {"role": "assistant", "content": response1["choices"][0]["message"]["content"]},
        {"role": "user", "content": "Tell me more about yourself."}
    ],
    tier="low",
    session_id=session_id,
)
model_used_2 = response2.get("model")
print(f"Turn 2 used: {model_used_2}")
assert model_used_1 == model_used_2  # Same model
```

### Session TTL

Sessions expire after `session_ttl_minutes`:

```yaml
settings:
  session_ttl_minutes: 30
```

Once expired, the next call is free to pick a new model. Reset a session manually:

```python
# Expire the session and pick a fresh model
router.session_map.pop(session_id, None)

response = router.generate(
    messages=[...],
    tier="low",
    session_id=session_id,
)
```

### Fallback on Unavailability

If the pinned model becomes unavailable (rate-limited, penalized, over budget), flexrouter falls back to the next-best model for that call only:

```python
# Pinned to model A
response1 = router.generate(..., session_id=session_id, tier="low")
# Model A is now rate-limited

# This call uses model B (fallback)
response2 = router.generate(..., session_id=session_id, tier="low")

# But the session is still pinned to A
# If A recovers, the next call uses A again
response3 = router.generate(..., session_id=session_id, tier="low")
```

The pin is **not reset** by fallback. This ensures temporary outages don't accidentally abandon your session.

### Generating Session IDs

Common patterns:

```python
import uuid

# Per-conversation
session_id = f"conversation-{uuid.uuid4()}"

# Per-user per-feature
session_id = f"user-{user_id}-draft-writing"

# Per-user per-timestamp
from datetime import datetime
session_id = f"user-{user_id}-{datetime.now().isoformat()}"
```

---

## Multi-Key Round-Robin

If you have multiple API keys for one provider, they rotate round-robin:

```yaml
providers:
  groq:
    base_url: https://api.groq.com/openai/v1
    api_keys:
      - env: GROQ_KEY_1
      - env: GROQ_KEY_2
      - env: GROQ_KEY_3
```

Each request uses the next key in the list. If a key hits a 429 (rate limit), that key is penalized and the next key is used immediately.

### Why Multiple Keys?

- **Distribute load** across keys to avoid hitting limits
- **Burst handling** if one key is rate-limited
- **Credential rotation** for security (periodically add/remove keys)

### Key Penalty Box

Each key has its own penalty box, separate from model penalties:

```
Key 1: hits 429 → penalized 30s, skipped
Key 2: next in rotation, gets the request
Key 3: ...
```

After the penalty expires, Key 1 is available again.

---

## Retry Policies

Configure how many times flexrouter retries after a failure.

### Presets

```yaml
settings:
  retry_policy: balanced  # conservative | balanced | aggressive
```

| Preset | Retries | Backoff | Best For |
|--------|---------|---------|----------|
| `conservative` | 2 | 5s | Stability > speed |
| `balanced` | 3 | 2s | Most apps |
| `aggressive` | 5 | 1s | Real-time, exhaustive |

### Manual Override

```yaml
settings:
  retries: 4
  backoff_seconds: 1.5
```

This overrides the preset. Retry logic:

1. Try model A
2. If it fails, wait `backoff_seconds`, try model B
3. If it fails, wait `backoff_seconds`, try model C
4. ... up to `retries` attempts
5. If all fail, raise `RouterError`

### Example: Aggressive Retry

```python
router = FlexRouter()  # Uses aggressive retry policy from config

# This will retry up to 5 times with 1s backoff between retries
response = router.generate(
    messages=[{"role": "user", "content": "test"}],
    tier="low",
)
```

---

## Cost Tracking and Budgets

Set daily spending limits per provider:

```yaml
settings:
  provider_budget:
    openai: 5.00
    anthropic_compat: 3.00
    groq: 1.00
```

Once a provider hits its budget, it's skipped for the rest of the day (UTC).

### How Costs Are Calculated

flexrouter infers cost from token usage:

```
cost = completion_tokens * provider_cost_per_token
```

Cost per token varies by model and provider. flexrouter uses standard pricing.

### View Budget Status

Check the dashboard **Account Status** tab, or read the health file:

```python
import json

with open(".flexrouter/health.json") as f:
    health = json.load(f)
    
for provider, stats in health["providers"].items():
    daily = stats["daily_cost_usd"]
    budget = stats["budget_usd"]
    if budget:
        remaining = budget - daily
        print(f"{provider}: ${remaining:.2f} remaining (${budget:.2f} budget)")
    else:
        print(f"{provider}: ${daily:.2f} spent (no budget limit)")
```

### No Budget? Skip the Setting

If you don't set a budget for a provider, it has no limit (use unlimited):

```yaml
settings:
  provider_budget:
    openai: 5.00
    # anthropic_compat has no limit (omitted)
```

### Budget Reset

Budgets reset at **00:00 UTC** daily. If you have a multi-timezone app, use UTC and handle local time zone conversions yourself.

---

## Vision Tasks (Images)

If your messages contain images, enable vision filtering.

### Auto-Detection Hook

Enable the `detect_vision` hook:

```yaml
settings:
  hooks:
    - detect_vision
```

This scans message content for image URLs. If found, `vision=True` is set automatically (filters to vision-capable models).

### Manual Vision Setting

Or set it explicitly:

```python
response = router.generate(
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What's in this image?"},
                {"type": "image_url", "image_url": {"url": "https://example.com/cat.jpg"}},
            ],
        }
    ],
    tier="high",
    vision=True,  # Filter to vision-capable models
)
```

### Vision-Capable Models

Mark models as vision-capable in config:

```yaml
tiers:
  high:
    - provider: openai
      model: gpt-4o
      vision: true

    - provider: anthropic_compat
      model: claude-3-5-sonnet
      vision: true
```

When `vision=True` is requested, only models with `vision: true` are considered.

---

## Dashboard: Live Monitoring

Start the dashboard:

```bash
flexrouter dashboard
```

Opens `http://localhost:4891`.

### Tab 1: Live Telemetry

Real-time model status:

- **Model name, score, tier**
- **RPM bar** — requests used / limit
- **TPM bar** — tokens used / limit
- **Status dot** — green (available), yellow (penalized), red (rate-limited)
- **Penalty countdown** — time until model recovers

**Interactions:**
- Search by model name or provider
- Filter by tier or status
- Click a row to see full details in the drawer
- Hover-pause live updates

### Tab 2: Chat

Test the router interactively:

1. Pick a tier from the dropdown
2. Type your message
3. flexrouter picks a model and sends the request
4. Response appears with model name and token count

### Tab 3: Request Logs

Last 50 audit entries:

- Timestamp, tier, provider, model
- Token count and cost
- Latency
- Status (ok, rate_limited, error, etc.)

**Interactions:**
- Toggle card view ↔ table view
- Pause live updates while scrolling
- Export logs

### Tab 4: Account Status

Per-provider cost tracking:

- Daily cost so far
- Budget limit (if set)
- Remaining budget

- API key usage (round-robin index)
- Budget reset time

### Tab 5: Settings

View and edit config:

- Full parsed YAML
- Retry policy and manual overrides
- Provider budgets
- Hooks and session TTL

Changes apply immediately (hot-reload).

### Tab 6: Setup

Initial setup wizard for onboarding. Opens automatically on first run, or click **Setup**.

---

## Async Usage

flexrouter supports async/await:

```python
import asyncio
from flexrouter import FlexRouter

router = FlexRouter()

async def chat(messages, tier):
    response = await router.agenerate(
        messages=messages,
        tier=tier,
        session_id="user-123",
    )
    return response["choices"][0]["message"]["content"]

# Use in an async context
async def main():
    response = await chat(
        [{"role": "user", "content": "Hello"}],
        tier="low",
    )
    print(response)

asyncio.run(main())
```

### Async with FastAPI

```python
from fastapi import FastAPI
from flexrouter import FlexRouter

app = FastAPI()
router = FlexRouter()

@app.post("/generate")
async def generate(messages: list, tier: str):
    response = await router.agenerate(
        messages=messages,
        tier=tier,
    )
    return response
```

Both sync and async use the same underlying engine. Use async if you're already in an async context.

---

## Audit Logs: Deep Dive

Every request appends to `.flexrouter/audit.csv`:

```
timestamp,tier,provider,model,prompt_tokens,completion_tokens,cost_usd,latency_ms,status
2026-05-27T12:01:00,low,groq,llama-3.1-8b,1204,320,0.00008,280,ok
2026-05-27T12:01:05,low,groq,llama-3.1-8b,0,0,0,0,rate_limited
2026-05-27T12:01:10,medium,openai,gpt-4o-mini,500,150,0.0012,1200,ok
```

### Analyze Logs

```python
import pandas as pd

df = pd.read_csv(".flexrouter/audit.csv")

# Total cost by provider
print(df.groupby("provider")["cost_usd"].sum())

# Average latency by tier
print(df.groupby("tier")["latency_ms"].mean())

# Success rate by model
print(df.groupby("model")["status"].value_counts())

# Top 10 most-used models
print(df["model"].value_counts().head(10))
```

### Status Values

| Status | Meaning |
|--------|---------|
| `ok` | Request succeeded |
| `rate_limited` | 429, model penalized, retried |
| `server_error` | 5xx, model penalized, retried |
| `context_window` | Context too large, model skipped |
| `auth_error` | Auth failed, no retry (hard error) |
| `timeout` | Request timeout |

---

## Error Recovery Patterns

### Graceful Degradation

Fall back to a higher-tier model on `RouterBusy`:

```python
from flexrouter import RouterBusy

try:
    response = router.generate(messages=[...], tier="low", wait=False)
except RouterBusy:
    print("Low tier busy, trying medium...")
    response = router.generate(messages=[...], tier="medium", wait=False)
```

### Retry with Wait

Let flexrouter wait until a slot opens:

```python
response = router.generate(
    messages=[...],
    tier="low",
    wait=True,  # Default. Block until a model is available.
)
```

### Queue for Later

Catch the exception and queue the request:

```python
import queue

request_queue = queue.Queue()

try:
    response = router.generate(messages=[...], tier="low", wait=False)
except RouterBusy:
    request_queue.put({"messages": messages, "tier": "low"})
    print("Request queued.")
```

Then process the queue in a background worker.

---

## Common Recipes

### Multi-Turn Conversation (Sticky Session)

```python
session_id = f"user-{user_id}-conversation"

for user_message in messages:
    response = router.generate(
        messages=[...],
        tier="low",
        session_id=session_id,
    )
```

### Testing (Use Cheap Provider Only)

```python
# flexrouter.yaml
tiers:
  test:
    - provider: groq
      model: llama-3.1-8b
      rpm: 1000
      tpm: 100000
      score: 100

# In test
router = FlexRouter("flexrouter.test.yaml")
response = router.generate(messages=[...], tier="test")
```

### Production (Quality with Fallback)

```yaml
tiers:
  production:
    - provider: openai
      model: gpt-4o
      score: 100

    - provider: anthropic_compat
      model: claude-sonnet-4-5
      score: 90

    - provider: groq
      model: llama-3.3-70b
      score: 70
```

Use the single `production` tier; flexrouter handles fallback automatically.

### Cost-Aware Batching

```python
import json

# Check remaining budget before processing batch
with open(".flexrouter/health.json") as f:
    health = json.load(f)
    openai_remaining = health["providers"]["openai"]["budget_usd"] - health["providers"]["openai"]["daily_cost_usd"]

if openai_remaining < 1.00:
    print("OpenAI budget low, using cheaper models.")
    tier = "low"
else:
    tier = "medium"

for item in batch:
    response = router.generate(messages=item, tier=tier)
```

---

## Monitoring and Alerts

### Custom Monitoring

```python
import json
import time

def check_health():
    with open(".flexrouter/health.json") as f:
        health = json.load(f)
    
    for provider, stats in health["providers"].items():
        daily_cost = stats["daily_cost_usd"]
        budget = stats.get("budget_usd")
        
        if budget and daily_cost > budget * 0.8:
            print(f"⚠️ {provider} approaching budget limit!")

check_health()
```

### Alert on High Latency

```python
import pandas as pd

df = pd.read_csv(".flexrouter/audit.csv")
recent = df.tail(10)

avg_latency = recent["latency_ms"].mean()
if avg_latency > 2000:
    print(f"⚠️ High latency: {avg_latency:.0f}ms")
```

---

## Next Steps

- **Debugging a problem?** → [Concepts](3-Concepts.md)
- **Need to reconfigure?** → [Configuration Guide](2-Configuration-Guide.md)
- **Look up a specific method?** → [API Reference](5-API-Reference.md)
