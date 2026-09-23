# Configuration Guide

**Goal:** Understand every setting in your flexrouter settings file, and configure flexrouter for your multi-provider, multi-tier needs.

> **Three ways to work with your configuration:** the **dashboard**
> (`flexrouter dashboard`) lets you view and edit settings from your browser
> and shows the effect live; the **TUI** (`flexrouter tui`) manages keys and
> shows status from your terminal; and the settings file itself is there when
> you want to edit by hand. This page describes the file — the dashboard and
> TUI read and write the same configuration.

---

## Where Your Settings Live

flexrouter keeps one settings file for your whole computer — not one per project. Run this any time to see exactly where it is:

```bash
flexrouter doctor
```

If you want that shared place to live somewhere else, set `FLEXROUTER_HOME` to the folder you want before running flexrouter.

You can pass an explicit path instead, to point at a different file on purpose: `FlexRouter("path/to/config.yaml")`. Note that the importable `FlexRouter` is a client of the flexrouter service (see the [Getting Started guide](1-Getting-Started.md)), so all it takes from that file is the port and the optional local key — the providers, models and keys are read by the service, from the service's own settings.

---

## Top-Level Structure

Every settings file has these sections:

```yaml
tiers:
  ...

providers:
  ...

settings:
  ...
```

---

## Section 1: Tiers

Tiers are named groups of models. You define which models belong to which tier, and their relative priority.

### Example: Three Tiers

```yaml
tiers:
  cheap:
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
```

### Tier Fields Explained

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider` | string | Yes | Key matching a provider in the `providers` section |
| `model` | string | Yes | Model identifier (e.g., `gpt-4o`, `claude-sonnet-4-5`) |
| `score` | int (1–100) | Yes | Priority within tier. Higher = preferred. Random selection among top 20% to avoid thundering herd. |
| `rpm` | int | Yes | Requests per minute limit *you enforce locally* |
| `tpm` | int | Yes | Tokens per minute limit *you enforce locally* |
| `context_window` | int | Yes | Max tokens this model can handle. Used for overflow detection. |
| `vision` | bool | No (default: false) | Set `true` if model supports images |

### Naming Tiers

Tier names are arbitrary. Common patterns:

- **Speed/Cost:** `cheap`, `medium`, `expensive`
- **Quality:** `fast`, `balanced`, `accurate`
- **Use case:** `draft`, `review`, `production`
- **Custom:** `vision-only`, `long-context`, `code-generation`

You call them by name:

```python
router.generate(messages=[...], tier="cheap")
```

---

## Section 2: Providers

Define how to reach each LLM provider (API endpoint and authentication).

### Example: Multiple Providers

```yaml
providers:
  groq:
    base_url: https://api.groq.com/openai/v1
    api_keys:
      - env: GROQ_API_KEY_1
      - env: GROQ_API_KEY_2

  openai:
    base_url: https://api.openai.com/v1
    api_keys:
      - env: OPENAI_API_KEY

  openrouter:
    base_url: https://openrouter.ai/api/v1
    api_keys:
      - env: OPENROUTER_API_KEY

  anthropic_compat:
    base_url: https://api.anthropic.com/v1
    api_keys:
      - env: ANTHROPIC_API_KEY
```

### Provider Fields Explained

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `base_url` | string | Yes | OpenAI-compatible endpoint (usually `https://api.provider.com/v1`) |
| `api_keys` | list or string | Yes | Environment variable names holding API keys. List for round-robin. |

### API Keys: Single vs. Multiple

**Single key:**
```yaml
providers:
  openai:
    base_url: https://api.openai.com/v1
    api_keys: OPENAI_API_KEY
```

**Multiple keys (round-robin):**
```yaml
providers:
  groq:
    base_url: https://api.groq.com/openai/v1
    api_keys:
      - env: GROQ_KEY_1
      - env: GROQ_KEY_2
      - env: GROQ_KEY_3
```

Keys rotate in order with each request. If one hits a 429, it's skipped immediately.

> Saved keys (`flexrouter keys add <provider>`) can also be managed without the
> command line: the **TUI's Keys tab** adds, removes, and disables keys, and
> the dashboard shows key status on its **Account Status** tab.

### OpenAI-Compatible Requirement

All providers must expose an OpenAI-compatible chat completions endpoint:

```
POST {base_url}/chat/completions
```

This works with:
- ✅ OpenAI
- ✅ Groq
- ✅ OpenRouter
- ✅ Anthropic (via `/v1` endpoint)
- ✅ Together.ai
- ✅ Most self-hosted LLM servers (Ollama, vLLM, LocalAI, etc.)

---

## Section 3: Settings

Global configuration for routing behavior, dashboards, and persistence.

### Full Example

```yaml
settings:
  # Persistence
  state_dir: .flexrouter/

  # Rate limiting windows
  window_seconds: 60

  # Penalty box (exponential backoff for failures)
  penalty_base_seconds: 30
  penalty_max_seconds: 1800

  # Session behavior
  session_ttl_minutes: 30

  # Port for the service (API + dashboard, one port)
  port: 4891

  # Retry policy (preset or manual)
  retry_policy: balanced
  # OR manual override:
  # retries: 3
  # backoff_seconds: 2

  # Cost tracking
  provider_budget:
    openai: 5.00
    groq: 2.00
    openrouter: 10.00

  # Built-in pre-routing hooks
  hooks:
    - detect_vision
    - estimate_tokens
```

### Setting Fields Explained

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `state_dir` | string | `.flexrouter/` | Where audit.csv and health.json are written |
| `window_seconds` | int | 60 | Duration of sliding window for RPM/TPM tracking |
| `penalty_base_seconds` | int | 30 | Initial penalty duration (doubles on repeated failures) |
| `penalty_max_seconds` | int | 1800 | Maximum penalty (30 min) |
| `session_ttl_minutes` | int | 30 | Time before sticky session expires |
| `port` | int | 4891 | Port for the service (API and dashboard together). `dashboard_port` still works as an older name for the same setting. |
| `retry_policy` | string | `balanced` | One of: `conservative` (2 retries, 5s backoff), `balanced` (3 retries, 2s backoff), `aggressive` (5 retries, 1s backoff) |
| `retries` | int | — | Manual override (ignores preset) |
| `backoff_seconds` | int | — | Manual override (ignores preset) |
| `provider_budget` | dict | null | Max USD per provider per day |
| `hooks` | list | `[]` | Built-in pre-routing hooks to enable |

### Retry Policies

| Policy | Retries | Backoff | Best For |
|--------|---------|---------|----------|
| `conservative` | 2 | 5s | Production apps where stability > speed |
| `balanced` | 3 | 2s | Most applications (default) |
| `aggressive` | 5 | 1s | Real-time apps that need to exhaust all options |

To override:

```yaml
settings:
  retries: 4
  backoff_seconds: 1.5
```

### Cost Budgets

Limit spending per provider per calendar day (UTC):

```yaml
settings:
  provider_budget:
    openai: 5.00
    anthropic_compat: 3.00
```

Once a provider hits its daily budget, it's skipped. Warning logged. Routing continues with other providers in the tier.

Check remaining budget in the dashboard **Account Status** tab.

### Hooks

Pre-routing hooks run before the routing decision. Currently two built-in hooks:

#### `detect_vision`

Scans message content for image URLs. If found, sets `vision=True` automatically (filters to vision-capable models).

```yaml
settings:
  hooks:
    - detect_vision
```

#### `estimate_tokens`

Counts tokens in the input. If they exceed a model's `context_window`, that model is skipped and a `ContextWindowWarning` is logged.

```yaml
settings:
  hooks:
    - estimate_tokens
```

Both hooks are safe to enable; they degrade gracefully if features aren't available.

---

## Hot-Reload

flexrouter picks up a change the next time your code asks it for an answer, without you restarting anything. It watches all three files a change can come from:

- your settings file, if you edit it by hand
- `overrides.json`, where your dashboard changes are saved
- `keys.json`, so a key you add with `flexrouter keys add` works straight away

Watching only the settings file would mean watching the one file nothing is allowed to write — so a model you disabled in the dashboard would have kept being used until you restarted.

- ✅ Adds and removes buckets and models
- ✅ Updates provider addresses
- ✅ Picks up a newly added or removed key
- ✅ Changes settings (retry policy, budgets, and so on)
- ❌ Does NOT reset what it has learned so far — rate-limit counts, rest periods and session pins all carry over

**If the change can't be read**, flexrouter keeps running on the last settings it read successfully rather than failing the request you were in the middle of. Correct the file and the next request picks up the fix. Nothing announces this yet, so if a change appears to have done nothing, check the file for a mistake.

To force a reload manually:

```python
router.reload()
```

---

## Validation

Check your settings for common mistakes:

```bash
flexrouter doctor
```

This reads your settings the same way flexrouter itself does and tells you what it found, including anything it could not make sense of. The dashboard shows the same check on its **Settings** tab, and the TUI's **Doctor** tab shows it in your terminal.

Or check programmatically. `FlexRouter` is a client of the service, so this
only proves your settings file can be read and that the service answered — the
providers and models in it are validated by the service, which must already be
running (`flexrouter serve`):

```python
from flexrouter import FlexRouter

try:
    router = FlexRouter()
    router.generate([{"role": "user", "content": "ping"}], tier="cheap")
    print("Config valid!")
except Exception as e:
    print(f"Config error: {e}")
```

---

## Common Patterns

### Development (Fast + Cheap)

```yaml
tiers:
  dev:
    - provider: groq
      model: llama-3.1-8b-instant
      score: 100
      rpm: 100
      tpm: 100000
      context_window: 131072

providers:
  groq:
    base_url: https://api.groq.com/openai/v1
    api_keys:
      - env: GROQ_API_KEY

settings:
  state_dir: .flexrouter/
  retry_policy: aggressive
```

### Production (Quality + Fallback)

```yaml
tiers:
  production:
    - provider: openai
      model: gpt-4o
      score: 100
      rpm: 60
      tpm: 150000
      context_window: 128000

    - provider: anthropic_compat
      model: claude-sonnet-4-5
      score: 90
      rpm: 50
      tpm: 100000
      context_window: 200000

providers:
  openai:
    base_url: https://api.openai.com/v1
    api_keys:
      - env: OPENAI_API_KEY

  anthropic_compat:
    base_url: https://api.anthropic.com/v1
    api_keys:
      - env: ANTHROPIC_API_KEY

settings:
  state_dir: .flexrouter/
  provider_budget:
    openai: 50.00
  retry_policy: balanced
```

### Vision Tasks

```yaml
tiers:
  vision:
    - provider: openai
      model: gpt-4o
      score: 100
      rpm: 60
      tpm: 150000
      context_window: 128000
      vision: true

    - provider: anthropic_compat
      model: claude-3-5-sonnet-vision
      score: 90
      rpm: 50
      tpm: 100000
      context_window: 200000
      vision: true

providers:
  openai:
    base_url: https://api.openai.com/v1
    api_keys:
      - env: OPENAI_API_KEY

  anthropic_compat:
    base_url: https://api.anthropic.com/v1
    api_keys:
      - env: ANTHROPIC_API_KEY

settings:
  hooks:
    - detect_vision
    - estimate_tokens
```

---

## Next Steps

- **Want to understand routing logic?** → [Concepts](3-Concepts.md)
- **Ready to use what you've configured?** → [Getting Started](1-Getting-Started.md)
- **Need advanced setups (sessions, budgets, dashboards)?** → [Advanced Usage](4-Advanced-Usage.md)
