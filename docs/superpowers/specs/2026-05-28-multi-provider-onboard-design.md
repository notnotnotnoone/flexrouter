# Multi-Provider Onboarding Wizard — Design Spec
Date: 2026-05-28

## Goal

Replace the single-provider (OpenRouter-only) `flexrouter init` wizard with a multi-provider wizard that:
- Discovers all available free models per provider via their `/v1/models` endpoints
- Scores discovered models using the Artificial Analysis Intelligence Index API
- Learns rate limits from live API response headers (no test calls during onboarding)
- Writes a complete, ready-to-use `flexrouter.yaml`

## Providers

### Free (shown by default)

| Provider | Base URL | Free model detection |
|---|---|---|
| Cerebras | `https://api.cerebras.ai/v1` | All returned models |
| Groq | `https://api.groq.com/openai/v1` | All returned models |
| OpenRouter | `https://openrouter.ai/api/v1` | `pricing.prompt == "0"` |
| Ollama | `http://localhost:11434/v1` | All local models (auto-detect) |
| Google AI | `https://generativelanguage.googleapis.com/v1beta/openai/` | Zero-price models |

### Paid-optional (skipped by default, user opts in)

| Provider | Base URL |
|---|---|
| DeepSeek | `https://api.deepseek.com/v1` |
| SiliconFlow | `https://api.siliconflow.cn/v1` |
| Sambanova | `https://api.sambanova.ai/v1` |

## Wizard Flow

```
flexrouter init

1. For each free provider:
   - Show: provider name, signup URL, current masked key
   - Prompt: Enter key (Enter=keep, -=clear, value=update)
   - If key set: GET /v1/models → filter free models → collect

2. Ollama (special):
   - Auto-detect http://localhost:11434 (silent HTTP check)
   - If reachable: collect all models, no key needed
   - If not reachable: skip silently

3. "Include paid providers? (y/N)"
   - If yes: repeat step 1 for DeepSeek, SiliconFlow, Sambanova (no free filter)

4. If AA_API_KEY env var set:
   - GET https://artificialanalysis.ai/data/llms/models
   - Match discovered models by normalized name → assign Intelligence Index score
   - Unmatched models → score = 50

5. Write flexrouter.yaml:
   - All free models → tiers.default (sorted by score desc)
   - Paid models (if any) → tiers.paid (sorted by score desc)

Error handling: bad key / timeout / 401 on any provider → skip + print warning, continue.
```

## Provider Registry

Each provider defined as a `ProviderDef` dataclass in `onboard.py`:

```python
@dataclass
class ProviderDef:
    name: str
    base_url: str
    signup_url: str
    free: bool                     # False = paid-optional
    free_filter: callable          # fn(model_dict) -> bool
    rate_limit_headers: dict       # {"rpm": header_name, "tpm": header_name}
    ollama: bool = False           # special: no key, auto-detect via HTTP
```

Standard rate limit header names (used by all providers):
- `x-ratelimit-limit-requests` → rpm
- `x-ratelimit-limit-tokens` → tpm

## Rate Limit Learning (Engine Side)

`client.py` already receives every API response. After each successful completion:

1. Parse `x-ratelimit-limit-requests` and `x-ratelimit-limit-tokens` from response headers
2. Write to `.flexrouter/rate_limits.json`: `{"provider/model": {"rpm": N, "tpm": N, "updated_at": epoch}}`
3. Engine loads `rate_limits.json` at startup → overrides yaml rpm/tpm values for known models
4. On each response, if headers differ from stored values → update immediately

This means:
- Onboarding writes yaml with sensible defaults (from AA or score=50 fallback)
- Real rate limits populate automatically from first live call
- Plan upgrades/downgrades detected and updated without re-running init

## AA Scoring

```
GET https://artificialanalysis.ai/data/llms/models
Headers: x-api-key: $AA_API_KEY

Response: [{model_id, name, intelligence_index, ...}, ...]

Matching: normalize both provider model ID and AA name
  → lowercase, strip punctuation/spaces
  → substring match
  → first match wins
  → no match → score = 50
```

`AA_API_KEY` sourced from environment only. Not stored in yaml.

## YAML Output

```yaml
providers:
  cerebras:
    base_url: https://api.cerebras.ai/v1
    api_keys:
      - key: csk-xxx
  groq:
    base_url: https://api.groq.com/openai/v1
    api_keys:
      - key: gsk-xxx
  openrouter:
    base_url: https://openrouter.ai/api/v1
    api_keys:
      - key: sk-or-xxx
  googleai:
    base_url: https://generativelanguage.googleapis.com/v1beta/openai/
    api_keys:
      - key: AIza-xxx
  ollama:
    base_url: http://localhost:11434/v1
    api_keys: []

tiers:
  default:
    - provider: openrouter
      model: qwen/qwen3-coder:free
      score: 87
      rpm: 20
      tpm: 100000
      context_window: 262144
    # all free models, sorted by score desc

  paid:                          # only written if user opted in
    - provider: deepseek
      model: deepseek-chat
      score: 74
      rpm: 60
      tpm: 200000
      context_window: 65536

settings:
  state_dir: .flexrouter
  dashboard_port: 7352
```

## Files Changed

| File | Change |
|---|---|
| `flexrouter/onboard.py` | Full rewrite — provider registry, wizard loop, AA scoring |
| `flexrouter/config.py` | Already supports inline `key:` format. Add `ollama` provider with empty api_keys. |
| `flexrouter/client.py` | Add rate limit header parsing + persist to state dir |
| `flexrouter/engine.py` | Load `rate_limits.json` at startup, override ModelConfig rpm/tpm. Skip api_key selection for providers with empty api_keys (Ollama). |

## Out of Scope

- Automatic re-discovery of new models (user re-runs `flexrouter init`)
- Multi-key rotation during onboarding
- Provider health checks beyond model list ping
