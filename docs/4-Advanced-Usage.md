# Advanced Usage

**Goal:** Master session stickiness, multi-key round-robin, retry strategies, cost tracking, and monitoring from the dashboard or the TUI.

> **Everything on this page needs the service running.** `FlexRouter` is a
> client: it sends your request to the one flexrouter service on this machine,
> which does the routing, the retrying and the key round-robin. Start it in its
> own terminal with `flexrouter serve` before running any example here. The
> code and the method signatures are exactly as they always were.

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

If the pinned model becomes unavailable (Busy, Struggling, Needs you, over budget), flexrouter falls back to the next-best model for that call only:

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

Each request uses the next key in the list. If a key hits a 429 (rate limit), that key turns Busy and the next key is used immediately (see [When a Key Is Busy](#when-a-key-is-busy) below).

### Why Multiple Keys?

- **Distribute load** across keys to avoid hitting limits
- **Burst handling** if one key is rate-limited
- **Credential rotation** for security (periodically add/remove keys)

### When a Key Is Busy

Each key has its own status, separate from the model's. A key that hits a
429 is **Busy** until the provider says it can be used again (its
`Retry-After` or rate-limit headers, else 60 seconds), and the next key in
rotation takes the request. A key the provider rejects (401) is **Needs
you**, on that key only; the other keys keep working.

---

## Failover and Statuses

When a model fails, flexrouter moves to the next model in the bucket
straight away. It doesn't sleep between tries or count retries:

| What came back | What happens |
|---|---|
| 429 or 503 (busy, overloaded) | next model now; this one is **Busy** until its retry time |
| 404 (no such model) | next model now; this one **Needs you**, with "Did you mean X?" when the provider's real list has a close match |
| 402 / 403 / a 429 whose quota is 0 | next model now; this one **Needs you**: "Not on your plan" / "Balance empty" |
| message too long | the next model with a bigger context window |
| an empty reply | next model now; this one is **Struggling** for about an hour |
| a genuine bad request (your request's fault) | returned to you at once |

It tries every model in the bucket and gives up after 30 seconds
(`failover_budget_seconds`, "Give up after" in Settings). If every model
fails, the error lists each one with the provider's own words.

**Pinned requests** name one exact model (`groq/llama-3.1-8b-instant`, with
a `/`). There's no fallback: if that model is Busy you get "429: busy, retry
in 40s" at once.

**The five statuses.** Every model and key is always exactly one of:

| Status | Means | Button on the Status page |
|---|---|---|
| ● Ready | will be used | none |
| ◐ Busy | rate limit or overload; clears on its own, with a countdown | none |
| ◆ Struggling | failing in odd ways; clears after about an hour | Try now |
| ▲ Needs you | wrong ID, not on plan, no balance, key rejected; never expires on a timer | the one fix: Use X / Retry / Remove / Replace key |
| ○ Off | you turned it off | Turn on |

**The error brain decides** what an unfamiliar error means. With a
classifier configured (Settings → Error brain), a bare 400 or text the
built-in rules don't know is classified once per kind of error, and the
verdict sets the status. An error it isn't sure about fails over and shows
under **Not sure** on the Status page, where you say what it means once.

**Google counts failed attempts.** Google's free tier counts a request that
failed (a 503 "high demand", say) against your daily allowance, the same as
one that worked. flexrouter's Allowance page counts them too, so its numbers
match AI Studio's. It's also why failing over at once, instead of retrying
the same busy model, saves real daily quota.

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

Check the dashboard's **Allowance** page (or the TUI's **Overview** tab), or read the health file:

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

The dashboard is the main interface to a running flexrouter. Start it:

```bash
flexrouter dashboard
```

Opens `http://localhost:4891`. The pages, in menu order:

- **Overview**: the Get started card (until you hide it), one line saying
  whether things are fine, the same status counts as the Status page, who
  answered hour by hour, buckets, providers, recent requests and latency.
- **Providers & keys**: add a provider, paste and **Test** a key, and edit
  or remove keys. Removing a key offers **Undo** for a few seconds instead of
  asking first.
- **Models**: every model with its status, score, how often it answered and
  what it can do. From here: **Add models with AI**, **Rank models with AI**
  and **Find rate limits with AI**, plus the folded "Not chat models yet" list.
- **Buckets**: each bucket's models in the order they'd be picked. Drag a
  model onto a bucket (or tap it, then tap the bucket; or press Enter twice)
  to add it there. **Try it** sends one small request through the bucket.
- **Requests**: every request, newest first. Press one for its sheet: the
  model that answered, the Thinking, what you sent and the reply (see
  [Saved conversations](#saved-conversations)), and every model tried first,
  with the provider's words and the time spent waiting.
- **Playground**: chat through any bucket or model, with the reasoning in a
  folded Thinking section and replies rendered as markdown.
- **Status**: every model's one status. Needs you on top, one sentence and
  one button each; Struggling and Busy with a countdown; Not sure; Ready and
  Off folded away. Press a row for what the provider actually said.
  **Explain errors with AI** copies a prompt describing every problem, and
  **Test all** says hi to every model.
- **What's broken**: a summary of problems the error brain isn't sure about
  (unclear errors). Press **Resolve** to vote on what each one means without
  leaving the page; it saves your answer and removes the uncertain entry.
- **Allowance**: free-tier headroom per provider, when each provider's day
  starts over (in your own time), and the provider's own figures when it
  sends them.
- **Settings**: every setting in plain words, with on/off switches that save
  themselves, backup and restore, and the Danger zone.

Every button shows it's working, then Done or the reason it failed. The
layout works from a phone (the menu folds behind **Menu**) to a wide screen,
and follows your system's light or dark theme.

## Logging and the Logs page

The dashboard already shows every *request* (the Requests page). `--log` adds
the server's own side of the story — startup, routing warnings, provider
errors, and the HTTP lines uvicorn writes:

```bash
flexrouter dashboard --log
```

With the flag on, the dashboard grows a **Logs** page under *System* in the
menu. It tails the log live, newest first, with each level color-coded —
warnings washed amber, errors red — so a bad provider stands out without
reading a thing. It is also in the Ctrl+K command bar, and the raw file is
just a file, so `Get-Content`/`tail -f` on it works too.

**Where it writes:** `flexrouter.log` in the state directory (`flexrouter doctor`
shows where that is). The file rotates at 5 MB and keeps three backups, so it
can never grow past roughly 20 MB.

**Without the flag**, nothing writes a log file and the menu shows no Logs
link. Opening `/logs` anyway says "Logging is off" and how to turn it on.

---

## Saved conversations

flexrouter keeps each request's prompt, reply and reasoning for 7 days, so
the request sheet can show what was asked and answered. They live in
`state/conversations/`, one file per day, with long messages cut at about
20 KB. Files older than `save_conversations_days` are deleted.

**The privacy trade-off:** anything an app puts in a prompt, including
anything sensitive, sits in that folder on this machine for up to 7 days.
Keys flexrouter holds are masked, but nothing else in the text is. The
request trace is scrubbed and safe to share; the conversations folder is
not. Turn it off with **Save conversations** in Settings (or
`save_conversations: false`).

---

## Terminal UI: Monitoring Without a Browser

```bash
flexrouter tui
```

The same router, in your terminal, split into four tabs — and it works whether
or not the service is running, because it reads your flexrouter home directly
rather than talking to a live service:

| Tab | Shows |
|---|---|
| **Overview** | Totals and today's spend per provider, straight from what the service has recorded |
| **Keys** | Every provider, with the keys you have added (masked, never in full). Add a key, remove one, or switch one off without leaving the screen |
| **Requests** | Your recent requests, newest first |
| **Doctor** | Where your settings, keys, and records live, and which key each provider will use |

Press `a` to add a key, `d` to remove the selected one, `r` to refresh, `q` to
quit. The tabs refresh on their own every couple of seconds.

For anything interactive — chatting with a bucket, editing settings, deep
request logs — use the dashboard; the TUI covers status and key management at
a glance. One-shot terminal output without any UI: `flexrouter status`.

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

Close the client with `await router.aclose()` on shutdown — inside a running
event loop, the synchronous `close()` cannot be used.

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
| `rate_limited` | 429, model turns Busy, next one tried |
| `server_error` | 5xx, model turns Busy, next one tried |
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

Give your test setup its own flexrouter home, separate from the one you use day to day, by pointing the `FLEXROUTER_HOME` environment variable at a folder just for tests. flexrouter reads settings, keys, and everything else from whatever home that variable names, so tests never touch your real settings or your real keys:

```python
import os

os.environ["FLEXROUTER_HOME"] = "/tmp/flexrouter-test-home"

# Add a "test" tier to the settings file in that folder first, e.g.:
# tiers:
#   test:
#     - provider: groq
#       model: llama-3.1-8b
#       rpm: 1000
#       tpm: 100000
#       score: 100
# ...then start the service against that home: `flexrouter serve`

router = FlexRouter()
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
