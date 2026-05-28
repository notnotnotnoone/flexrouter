# flexrouter

Universal LLM router for Python. One call routes to the best available model across providers, with rate-limit awareness, cost tracking, and a live dashboard.

```python
from flexrouter import FlexRouter

router = FlexRouter()
response = router.generate(
    messages=[{"role": "user", "content": "classify this text..."}],
    tier="low",
)
print(response["choices"][0]["message"]["content"])
```

## Install

```bash
pip install flexrouter
```

Requires Python 3.11+.

## Quickstart

**1. Create `flexrouter.yaml` in your project:**

```yaml
tiers:
  low:
    - provider: groq
      model: llama-3.1-8b-instant
      score: 85
      rpm: 60
      tpm: 60000
      context_window: 131072

  high:
    - provider: openai
      model: gpt-4o
      score: 95
      rpm: 60
      tpm: 150000
      context_window: 128000
      vision: true

providers:
  groq:
    base_url: https://api.groq.com/openai/v1
    api_keys:
      - env: GROQ_API_KEY

  openai:
    base_url: https://api.openai.com/v1
    api_keys:
      - env: OPENAI_API_KEY

settings:
  state_dir: .flexrouter/
  retry_policy: balanced
```

**2. Set your API keys:**

```bash
export GROQ_API_KEY=gsk_...
export OPENAI_API_KEY=sk-...
```

**3. Route:**

```python
from flexrouter import FlexRouter

router = FlexRouter()

# Sync
response = router.generate(messages=[...], tier="low")

# Async
response = await router.agenerate(messages=[...], tier="high")
```

## How routing works

Each tier is a ranked list of models (score 1–100). On each call:

1. Models penalized for 429/5xx are skipped
2. Models over their daily provider budget are skipped
3. Models exceeding RPM/TPM windows are skipped
4. Models too small for the estimated token count are skipped (emits `ContextWindowWarning`)
5. The highest-scoring remaining model wins; picks randomly among models within 20% of the top score to avoid thundering herd

If no model is available: `wait=True` (default) sleeps until a slot opens; `wait=False` raises `RouterBusy`.

Tiers are strictly isolated — a busy `low` tier never falls back to `high`.

## Config reference

### Tiers

```yaml
tiers:
  <name>:
    - provider: <provider-name>   # must match a key in providers:
      model: <model-id>           # passed to the API
      score: 85                   # 1-100, higher = preferred
      rpm: 60                     # requests per minute limit
      tpm: 60000                  # tokens per minute limit
      context_window: 131072      # max tokens this model accepts
      vision: false               # set true for image-capable models
```

Tier names are arbitrary — use `cheap`/`smart`/`nuclear` or whatever makes sense.

### Providers

```yaml
providers:
  <name>:
    base_url: https://api.example.com/v1   # OpenAI-compatible endpoint
    api_keys:
      - env: MY_API_KEY_1   # env var name (never the key itself)
      - env: MY_API_KEY_2   # multiple keys rotate round-robin
```

### Settings

```yaml
settings:
  state_dir: .flexrouter/          # audit.csv + health.json location
  window_seconds: 60               # sliding window duration
  penalty_base_seconds: 30         # first 429 penalty
  penalty_max_seconds: 1800        # cap (30s → 60s → 120s → ... → 1800s)
  session_ttl_minutes: 30          # sticky session expiry
  dashboard_port: 7352

  retry_policy: balanced           # conservative | balanced | aggressive
  # Or manual:
  # retries: 3
  # backoff_seconds: 2

  provider_budget:                 # optional daily USD cap per provider
    openai: 5.00
    groq: 2.00

  hooks:                           # run before routing
    - detect_vision                # auto-sets vision=True if messages contain images
    - estimate_tokens              # pre-checks context window fit
```

| Preset | Retries | Backoff |
|---|---|---|
| `conservative` | 2 | 5s |
| `balanced` | 3 | 2s |
| `aggressive` | 5 | 1s |

## API

### `FlexRouter(config_path=None)`

Auto-discovers `./flexrouter.yaml` → `~/.flexrouter.yaml`. Pass an explicit path to override.

```python
router = FlexRouter()                          # auto-discover
router = FlexRouter("path/to/flexrouter.yaml") # explicit
```

### `generate(messages, tier, *, wait=True, vision=False, session_id=None, **kwargs)`

Synchronous. Blocks until a model responds (or raises if `wait=False` and none available).

```python
response = router.generate(
    messages=[{"role": "user", "content": "..."}],
    tier="low",
    wait=True,            # block until slot available (default)
    vision=False,         # only route to vision-capable models
    session_id="conv-1",  # sticky session — same model for this ID
    max_tokens=512,       # passed through to provider
    temperature=0.2,
)
text = response["choices"][0]["message"]["content"]
tokens = response["usage"]["total_tokens"]
```

### `agenerate(messages, tier, *, wait=True, vision=False, session_id=None, **kwargs)`

Async version. Same signature.

```python
response = await router.agenerate(messages=[...], tier="medium")
```

### `reload()`

Force config reload. Hot-reload is automatic on file mtime change, but you can call this manually.

### Exceptions

```python
from flexrouter import RouterBusy, RouterError, ContextWindowWarning
import warnings

try:
    response = router.generate(messages=[...], tier="low", wait=False)
except RouterBusy:
    # All low-tier models are rate-limited right now
    ...
except RouterError:
    # Auth failure or repeated 5xx — unrecoverable
    ...

# Context window warning (not an exception — some models skipped, routing continued)
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    response = router.generate(messages=long_messages, tier="low")
    if any(issubclass(x.category, ContextWindowWarning) for x in w):
        print("Some models skipped: message too long for their context window")
```

### Context manager

```python
with FlexRouter() as router:
    response = router.generate(messages=[...], tier="low")
# event loop closed on exit
```

## Session stickiness

Pass a `session_id` to pin a conversation to one model for consistency:

```python
for turn in conversation:
    response = router.generate(
        messages=turn["messages"],
        tier="medium",
        session_id=turn["conversation_id"],
    )
```

The pin expires after `session_ttl_minutes` of inactivity (default 30). If the pinned model becomes unavailable mid-session, that call re-routes to the next best model without resetting the pin.

## Multi-key round-robin

List multiple API keys per provider to rotate automatically:

```yaml
providers:
  groq:
    api_keys:
      - env: GROQ_KEY_1
      - env: GROQ_KEY_2
      - env: GROQ_KEY_3
```

Keys rotate round-robin. A key that returns 429 is skipped immediately.

## CLI

```bash
flexrouter dashboard        # start live dashboard at http://localhost:7352
flexrouter status           # print tier health to terminal
flexrouter init             # open browser setup wizard
flexrouter config export    # print portable base64 config token
flexrouter config import <token>
```

## Dashboard

```bash
flexrouter dashboard
# → http://localhost:7352
```

Six tabs: **Live Telemetry** (RPM/TPM bars, penalty countdowns, search/filter), **Chat** (test any tier live), **Request Logs** (last 50 audit entries), **Account Status** (per-provider cost), **Settings** (view/edit config), **Setup** (getting-started guide). Dark/light theme toggle.

## Audit log

Every request appends to `.flexrouter/audit.csv`:

```
timestamp,tier,provider,model,prompt_tokens,completion_tokens,cost_usd,latency_ms,status
2026-05-27T12:01:00,low,groq,llama-3.1-8b-instant,1204,320,0.00008,280,ok
```

`.flexrouter/health.json` is updated after every request with running totals.

Add `.flexrouter/` to `.gitignore`.

## Hot reload

Edit `flexrouter.yaml` while your app is running — changes are picked up automatically on the next `generate()` call (mtime-watched). Rate-limit windows and penalty state are preserved across reloads.

## PyPI publish

Tag a release to publish:

```bash
git tag v0.1.0
git push origin main --tags
```

Requires a [PyPI trusted publisher](https://docs.pypi.org/trusted-publishers/) configured for this repo with environment `pypi`.
