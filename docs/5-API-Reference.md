# API Reference

**Goal:** Complete reference for flexrouter's public API.

---

## Module: `flexrouter`

### Classes and Exceptions

---

## `FlexRouter`

Main class for routing LLM requests.

### Initialization

```python
FlexRouter(config_path: str | None = None)
```

**Parameters:**
- `config_path` (str, optional): Path to a settings file. If not given, flexrouter reads the one shared settings file for this machine — run `flexrouter doctor` to see where that is. Pass a path only to point at a different file on purpose.

**Raises:**
- `ConfigError`: If the settings file can't be found or read.
- `ConfigFieldError`: If the settings file is missing something it needs.

**Example:**

```python
from flexrouter import FlexRouter

# Use the shared settings file (the normal case)
router = FlexRouter()

# Point at a different settings file
router = FlexRouter("/etc/flexrouter/config.yaml")
```

---

### `generate()` — Sync Request

```python
generate(
    messages: list[dict],
    tier: str,
    wait: bool = True,
    vision: bool = False,
    session_id: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    **kwargs
) -> dict
```

Make a synchronous routing request. Blocks until a model is available or raises an exception.

**Parameters:**
- `messages` (list[dict], required): OpenAI-format messages. Each message is a dict with `role` and `content`.
- `tier` (str, required): Tier name (e.g., `"low"`, `"medium"`). Must exist in config.
- `wait` (bool, default=True): If True, block until a model slot opens. If False, raise `RouterBusy` immediately if all models are rate-limited.
- `vision` (bool, default=False): Filter to vision-capable models only.
- `session_id` (str, optional): Sticky session ID. Pins conversation to one model.
- `max_tokens` (int, optional): Passed to provider. Maximum response length.
- `temperature` (float, optional): Passed to provider. Sampling temperature (0–2).
- `top_p` (float, optional): Passed to provider. Nucleus sampling.
- `**kwargs`: Additional parameters passed to provider (e.g., `frequency_penalty`, `presence_penalty`).

**Returns:**
- dict: OpenAI-compatible response object:
  ```python
  {
      "id": "chatcmpl-...",
      "object": "chat.completion",
      "created": 1234567890,
      "model": "llama-3.1-8b",  # Actual model used
      "choices": [
          {
              "index": 0,
              "message": {
                  "role": "assistant",
                  "content": "Response text..."
              },
              "finish_reason": "stop"
          }
      ],
      "usage": {
          "prompt_tokens": 100,
          "completion_tokens": 50,
          "total_tokens": 150
      }
  }
  ```

**Raises:**
- `RouterBusy`: All models in tier are rate-limited and `wait=False`.
- `RouterError`: Provider returned unrecoverable error (auth failure, repeated 5xx).
- `ContextWindowWarning`: All models in tier too small for context (if `estimate_tokens` hook enabled).

**Example:**

```python
response = router.generate(
    messages=[
        {"role": "user", "content": "Explain quantum computing in one sentence."}
    ],
    tier="low",
    max_tokens=512,
    temperature=0.7,
)

print(response["choices"][0]["message"]["content"])
print(f"Used {response['usage']['total_tokens']} tokens")
```

---

### `agenerate()` — Async Request

```python
async agenerate(
    messages: list[dict],
    tier: str,
    wait: bool = True,
    vision: bool = False,
    session_id: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    **kwargs
) -> dict
```

Async version of `generate()`. Same parameters and return value.

**Example:**

```python
import asyncio

async def main():
    response = await router.agenerate(
        messages=[{"role": "user", "content": "Hello"}],
        tier="low",
    )
    print(response["choices"][0]["message"]["content"])

asyncio.run(main())
```

---

### `reload()`

```python
reload() -> None
```

Manually reload configuration from disk. Usually unnecessary (hot-reload is automatic), but useful for forcing a reload after external changes.

**Example:**

```python
router.reload()
print("Config reloaded.")
```

---

### `remaining_capacity()`

```python
remaining_capacity(tier: str) -> dict[str, dict]
```

Returns how much of each model's per-minute rpm/tpm allowance is still free right now, for
every model in `tier` that's currently in the running for selection (the same top-20%-by-score-
among-available pool `generate()`/`agenerate()` would pick from). Read-only — does not affect
routing/selection or consume any allowance.

**Parameters:**
- `tier` (str, required): Tier name. Must exist in config.

**Returns:**
- dict: `{"provider/model": {"rpm_remaining": int, "tpm_remaining": int}}`, one entry per
  currently-eligible model. Empty dict if no model in the tier is currently available.

**Raises:**
- `KeyError`: If `tier` doesn't exist in config.

**Example:**

```python
capacity = router.remaining_capacity("default")
for model, room in capacity.items():
    print(f"{model}: {room['tpm_remaining']} tokens/min free")
```

---

### Attributes

#### `session_map`

```python
session_map: dict[str, tuple[str, float]]
```

In-memory session map. Keys are `session_id`, values are (model_name, expiry_timestamp).

**Use cases:**
- Inspect active sessions
- Manually expire a session

**Example:**

```python
# List all active sessions
for session_id, (model, expiry) in router.session_map.items():
    print(f"Session {session_id}: {model} (expires {expiry})")

# Expire a session
router.session_map.pop("user-123-conversation", None)
```

---

## Exceptions

### `RouterBusy`

```python
class RouterBusy(Exception):
    """All models in tier are rate-limited. Raised when wait=False."""
```

Raised when `wait=False` and all models in the requested tier are rate-limited (hitting RPM/TPM limits).

**When to catch:**
- You want to fail fast instead of waiting
- You want to queue the request for later

**Example:**

```python
from flexrouter import RouterBusy

try:
    response = router.generate(
        messages=[...],
        tier="low",
        wait=False,
    )
except RouterBusy:
    print("All low-tier models are at capacity. Retrying later...")
```

---

### `RouterError`

```python
class RouterError(Exception):
    """Provider returned an unrecoverable error."""
```

Raised when a provider returns an unrecoverable error (auth failure, repeated 5xx after retries, config error).

**When to catch:**
- Auth/credential issues
- Provider outages
- Config errors

**Example:**

```python
from flexrouter import RouterError

try:
    response = router.generate(messages=[...], tier="low")
except RouterError as e:
    print(f"Routing failed: {e}")
    # Handle auth, config, or provider issues
```

---

### `ContextWindowWarning`

```python
class ContextWindowWarning(UserWarning):
    """Context window exceeded. Logged when estimate_tokens hook enabled."""
```

Warning (not exception by default) logged when request exceeds a model's context window. Can be caught as a warning.

**When to catch:**
- You want to handle oversized requests gracefully
- You want to downgrade to a larger-context model

**Example:**

```python
import warnings
from flexrouter import ContextWindowWarning

with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    response = router.generate(messages=long_messages, tier="low")
    
    if any(issubclass(x.category, ContextWindowWarning) for x in w):
        print("Some models were too small. Retrying with larger tier...")
        response = router.generate(messages=long_messages, tier="medium")
```

---

## CLI

### `flexrouter doctor`

```bash
flexrouter doctor
```

Shows where your settings and keys live, and checks that everything is readable.

**What it does:**
- Prints the shared folder flexrouter is using (see [`FLEXROUTER_HOME`](#environment-variables) below)
- Lists which key each provider will actually use, and flags any provider with no usable key
- Points out a key that's still typed directly into the settings file, so you can move it
- Shows any changes made from the dashboard that are layered on top of your settings

---

### `flexrouter keys`

Manage saved provider keys. Keys never belong in the settings file — this is how you add them instead.

```bash
flexrouter keys add <provider>      # save a key (prompts for it, never shows it on screen)
flexrouter keys list                # show saved keys, masked
flexrouter keys rm <provider> <id>  # remove a saved key
flexrouter keys import <old_file>   # copy keys out of an old settings file into the shared one
```

`keys import` only copies keys — it never modifies or deletes the old file.

---

### `flexrouter serve`

```bash
flexrouter serve [--port PORT]
```

Starts flexrouter as a service: an OpenAI-compatible API and the dashboard, together, on one port.

**What it does:**
- Listens at `http://localhost:4891` by default
- Accepts standard `POST /v1/chat/completions` requests
- Supports both streaming (`stream: true`) and non-streaming modes
- Exposes `GET /v1/models` to list available tiers
- Also serves the dashboard at the same address

**Options:**
- `--port`: Override the port (default: 4891, or whatever `port` is set to in your settings file)

**Example:**
```bash
flexrouter serve --port 8080
```

---

## OpenAI-Compatible HTTP API

When running `flexrouter serve`, the following endpoints are available:

### `POST /v1/chat/completions`

Drop-in compatible with OpenAI's chat completions API. Route requests through flexrouter by changing only the base URL.

**Request:**
```json
{
  "model": "auto-default",
  "messages": [{"role": "user", "content": "Hello!"}],
  "temperature": 0.7,
  "stream": false
}
```

**Response:** Standard OpenAI chat completion format:
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "llama-3.1-8b-instant",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Hi there!"},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
}
```

**Streaming:**

Set `"stream": true` to receive [server-sent events (SSE)](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events):

```bash
curl -N http://localhost:4891/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto-default",
    "messages": [{"role": "user", "content": "Tell me a joke"}],
    "stream": true
  }'
```

Response format follows the [OpenAI streaming specification](https://platform.openai.com/docs/api-reference/streaming):
```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":...,"choices":[{"index":0,"delta":{"content":"Why"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":...,"choices":[{"index":0,"delta":{"content":" did"},"finish_reason":null}]}

...

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":...,"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{...}}

data: [DONE]
```

### `GET /v1/models`

Lists all available routing tiers as models:

```json
{
  "object": "list",
  "data": [
    {"id": "auto", "object": "model", "created": 0, "owned_by": "flexrouter"},
    {"id": "auto-default", "object": "model", "created": 0, "owned_by": "groq, openrouter"},
    {"id": "auto-premium", "object": "model", "created": 0, "owned_by": "openai, google"}
  ]
}
```

### Model Name Mapping

| `model` value | Behaviour |
|---|---|
| `auto` | Pick the tier with the **highest-scoring model** (most intelligent available) |
| `auto-{tier}` | Route through a specific tier (e.g. `auto-default`, `auto-premium`) |
| Any other string | Falls back to the `default` tier |

### Using with OpenAI SDKs

Any OpenAI SDK or tool that supports custom `base_url` works with flexrouter:

**Python (openai package):**
```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:4891/v1")

# Non-streaming
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Hello!"}],
)

# Streaming
stream = client.chat.completions.create(
    model="auto-premium",
    messages=[{"role": "user", "content": "Write a poem"}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")
```

**Node.js (openai package):**
```js
import OpenAI from "openai";
const client = new OpenAI({ baseURL: "http://localhost:4891/v1" });
```

**curl:**
```bash
curl http://localhost:4891/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "Hi"}]}'
```

---

### `flexrouter dashboard`

```bash
flexrouter dashboard
```

Same as `flexrouter serve`, but also opens the dashboard in your browser for you.

**What it does:**
- Starts the service at `http://localhost:4891` (or whatever `port` is set to in your settings file)
- Opens your browser to live telemetry
- Provides `/api/*` endpoints for the dashboard's own use

**Tabs:**
- Live Telemetry
- Chat
- Request Logs
- Account Status
- Settings
- Setup

---

### `flexrouter status`

```bash
flexrouter status
```

Print tier health to terminal (no browser needed).

**Output:**

```
flexrouter Status (as of 2026-05-27T12:34:56Z)

Tier: low
  ✓ groq / llama-3.1-8b (score 85)
    RPM: 45/60  TPM: 28000/60000  Status: available
  
  ✓ openrouter / mistral-small (score 70)
    RPM: 15/40  TPM: 12000/80000  Status: available

Tier: medium
  ✗ groq / llama-3.3-70b (score 90)
    RPM: 30/30  TPM: 45000/60000  Status: rate-limited (20s penalty)
  
  ✓ openai / gpt-4o-mini (score 80)
    RPM: 120/500  TPM: 80000/200000  Status: available
```

---

### `flexrouter config export`

```bash
flexrouter config export
```

Print your settings file as a portable, base64-encoded token, for sharing.

Note: this includes anything typed directly into the settings file. It does not include keys saved with `flexrouter keys add`, since those never live in the settings file.

**Output:**

```
aGlzdG9yeSBjb25maWcgdG9rZW4gKGJhc2U2NCBlbmNvZGVkIFlBTUwpCgo=
```

Share this with teammates; they can import it:

```bash
flexrouter config import aGlzdG9yeSBjb25maWcgdG9rZW4gKGJhc2U2NCBlbmNvZGVkIFlBTUwpCgo=
```

---

### `flexrouter config import <token>`

```bash
flexrouter config import aGlzdG9yeSBjb25maWcgdG9rZW4gKGJhc2U2NCBlbmNvZGVkIFlBTUwpCgo=
```

Decodes the token and prints it to your terminal, along with the path to your settings file. It does **not** touch your settings file — flexrouter never overwrites it. Copy in whatever parts you want by hand.

---

## Config Schema (YAML)

Full structure of the settings file (run `flexrouter doctor` to see where yours lives):

```yaml
# Tier definitions (required)
tiers:
  <tier_name>:
    - provider: <provider_key>
      model: <model_name>
      score: <1-100>
      rpm: <int>
      tpm: <int>
      context_window: <int>
      vision: <bool>  # optional, default false

# Provider definitions (required)
providers:
  <provider_key>:
    base_url: <openai_compatible_endpoint>
    # Keys belong in `flexrouter keys add <provider_key>`, not here.
    # An `env:` reference to an environment variable still works too:
    api_keys:
      - env: <ENV_VAR_NAME>
      # ... more keys for round-robin

# Global settings (optional, all have defaults)
settings:
  state_dir: <path>                    # default: .flexrouter/
  window_seconds: <int>                # default: 60
  penalty_base_seconds: <int>          # default: 30
  penalty_max_seconds: <int>           # default: 1800
  session_ttl_minutes: <int>           # default: 30
  port: <int>                          # default: 4891 (dashboard_port also works, as an older alias)
  
  # Retry policy
  retry_policy: conservative|balanced|aggressive  # default: balanced
  # OR manual override:
  # retries: <int>
  # backoff_seconds: <float>
  
  # Cost budgets
  provider_budget:
    <provider_key>: <float>            # max USD per day

  # Pre-routing hooks
  hooks:
    - detect_vision
    - estimate_tokens
```

---

## State Files

### `.flexrouter/audit.csv`

CSV log of every request:

```
timestamp,tier,provider,model,prompt_tokens,completion_tokens,cost_usd,latency_ms,status
2026-05-27T12:01:00,low,groq,llama-3.1-8b,1204,320,0.00008,280,ok
```

**Columns:**
- `timestamp`: ISO 8601 timestamp
- `tier`: Tier used
- `provider`: Provider key
- `model`: Model name
- `prompt_tokens`: Input token count
- `completion_tokens`: Output token count
- `cost_usd`: Cost of this request
- `latency_ms`: Roundtrip time
- `status`: `ok`, `rate_limited`, `server_error`, `context_window`, `auth_error`, `timeout`

---

### `.flexrouter/health.json`

JSON summary of current state:

```json
{
  "total_cost_usd": 2.45,
  "session_start": "2026-05-27T11:00:00Z",
  "providers": {
    "groq": {
      "daily_cost_usd": 0.45,
      "budget_usd": 2.00
    },
    "openai": {
      "daily_cost_usd": 2.00,
      "budget_usd": 5.00
    }
  },
  "models": {
    "groq/llama-3.1-8b": {
      "rpm_current": 45,
      "tpm_current": 28000,
      "penalty_until": null
    },
    "openai/gpt-4o": {
      "rpm_current": 120,
      "tpm_current": 80000,
      "penalty_until": "2026-05-27T12:02:15Z"
    }
  }
}
```

---

## Environment Variables

### Required

None. Save keys with `flexrouter keys add <provider>`, or reference an environment variable from the settings file with `api_keys: - env: <NAME>`.

### Optional

- `DEBUG=1`: Enable debug logging
- `FLEXROUTER_HOME`: Moves the shared folder where flexrouter keeps your settings, keys, and everything else. Without it, flexrouter uses a standard per-user app-data folder for your OS. Run `flexrouter doctor` any time to see exactly where it landed.

---

## Response Format

All `generate()` and `agenerate()` responses follow OpenAI's chat completions format:

```python
{
    "id": str,                          # Unique message ID
    "object": "chat.completion",
    "created": int,                     # Unix timestamp
    "model": str,                       # Actual model used
    "choices": [
        {
            "index": int,               # Choice index
            "message": {
                "role": "assistant",
                "content": str          # Response text
            },
            "finish_reason": str        # "stop", "length", "error", etc.
        }
    ],
    "usage": {
        "prompt_tokens": int,           # Input tokens
        "completion_tokens": int,       # Output tokens
        "total_tokens": int             # Sum
    }
}
```

---

## Python Version

- **Minimum:** Python 3.11
- **Tested:** Python 3.11, 3.12, 3.13

---

## Dependencies

- `httpx>=0.28` — HTTP client
- `pyyaml>=6.0` — YAML parsing
- `watchdog>=3.0` — Hot-reload file watching
- `tiktoken>=0.5` — Token estimation
- `click>=8.0` — CLI framework

---

## Quick Reference

| Task | Code |
|------|------|
| Create router | `router = FlexRouter()` |
| Make request | `router.generate(messages=[...], tier="low")` |
| Sticky session | `router.generate(..., session_id="user-123")` |
| Fast fail | `router.generate(..., wait=False)` |
| Vision request | `router.generate(..., vision=True)` |
| Catch busy | `except RouterBusy: ...` |
| Catch error | `except RouterError as e: ...` |
| Catch warning | `except ContextWindowWarning: ...` |
| Reload config | `router.reload()` |
| Dashboard | `flexrouter dashboard` |
| API server | `flexrouter serve` |
| View status | `flexrouter status` |
| Where are my settings? | `flexrouter doctor` |
| Save a key | `flexrouter keys add <provider>` |
| List saved keys | `flexrouter keys list` |

---

## FAQ

**Q: Can I use two FlexRouter instances simultaneously?**

A: Yes. Each instance is independent with its own state, sessions, and audit logs.

**Q: What if a provider is down?**

A: The model goes into penalty box. flexrouter retries other models in the tier. If all fail, raises `RouterError`.

**Q: Can I cache responses?**

A: Not built-in. Implement caching at the application level (e.g., Redis keyed by message hash).

**Q: Does flexrouter work offline?**

A: No. It requires network access to providers. But it does work with self-hosted/local providers (Ollama, vLLM) if you configure them.

**Q: How do I update my API key?**

A: Run `flexrouter keys add <provider>` with the new key, then call `router.reload()` or restart your app.

**Q: Can I have different state dirs per tier?**

A: Not supported. One state dir per FlexRouter instance. Create separate instances if needed.

---

## Next Steps

- **Want to understand the design?** → [Concepts](3-Concepts.md)
- **Need examples?** → [Advanced Usage](4-Advanced-Usage.md)
- **Getting started?** → [Getting Started](1-Getting-Started.md)
