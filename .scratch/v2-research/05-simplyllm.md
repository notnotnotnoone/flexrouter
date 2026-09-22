# Research: simplyllm vs flexrouter

## Status: Target Package Not Located

**UNVERIFIED**: After extensive web searches, PyPI checks, and GitHub exploration, no Python package exactly named "simplyllm" (lowercase, one word) with the described features (`max_wait`, automatic fallover, rate limiting, OpenAI-SDK-compatible) could be found.

Possible interpretations:
1. **Package may not exist** as described or may be under a different name (e.g., SimplerLLM)
2. **Package may be very new** (post-Feb 2025 cutoff)
3. **Package may be private** or hosted on a non-standard registry
4. **The name may be a slight variation** (SimplerLLM, simple-llm, etc.)

This research documents what was investigated and what was found as potential candidates.

---

## Closest Match: SimplerLLM (hassancs91/SimplerLLM)

### What it is

[SimplerLLM](https://github.com/hassancs91/SimplerLLM) is an open-source Python library providing a unified interface across 11 LLM providers (OpenAI, Anthropic, Google Gemini, Cohere, OpenRouter, CometAPI, DeepSeek, Perplexity, Moonshot, Ollama, HuggingFace Local). It includes automatic failover via a `ReliableLLM` class, structured output support via Pydantic, and built-in tools for content loading, embeddings, and search.

**Note**: Not OpenAI-SDK-compatible in the sense of being a drop-in replacement; provides its own unified API instead.

### How it actually works

#### Installation
```bash
pip install simplerllm
pip install simplerllm[voice]  # with audio support
```

#### Basic API
```python
from SimplerLLM.language.llm import LLM, LLMProvider

llm = LLM.create(provider=LLMProvider.OPENAI, model_name="gpt-4o")
response = llm.generate_response(prompt="Your prompt")
```

#### Automatic Failover
```python
from SimplerLLM.language.llm.reliable import ReliableLLM

primary = LLM.create(provider=LLMProvider.OPENAI, model_name="gpt-4o")
secondary = LLM.create(provider=LLMProvider.ANTHROPIC, model_name="claude-sonnet-4-5-20250929")

reliable = ReliableLLM(primary, secondary)
response = reliable.generate_response(prompt="Your prompt")
```

**Mechanism**: If the primary provider fails, automatically switches to secondary. No documentation on failure criteria (is it any error? timeout? rate limit specifically?).

### OpenAI-SDK compatibility

**NOT compatible**. SimplerLLM provides its own unified API (`llm.generate_response()`) rather than mimicking the OpenAI SDK's interface. It does NOT:
- Subclass or wrap `openai.OpenAI` or `openai.AsyncOpenAI`
- Provide `client.chat.completions.create()` method signature
- Return OpenAI `ChatCompletion` objects directly

**Assessment**: Adoption would require code rewrite. This is a major compatibility gap versus a drop-in replacement.

### max_wait equivalent

**NOT FOUND**. SimplerLLM documentation mentions:
- `MAX_RETRIES` and `RETRY_DELAY` environment variables (retry configuration)
- "Built-in exponential backoff for failed requests" (generic recovery, no details)

**No `max_wait` parameter is documented**. There is no timeout parameter that blocks the caller until capacity returns. The failover simply switches providers without any wait-for-capacity mechanism.

### Rate limiting

**NOT EXPLICITLY DOCUMENTED**. The README does not specify:
- Rate limiting algorithm (token bucket? sliding window? fixed window?)
- Configuration options for RPM/TPM limits
- State storage (in-memory? persistent?)
- How limits are tracked per provider

Only mention: exponential backoff, but no rate-limit-specific mechanism is detailed.

---

## Alternative: SimpleLLM (Organization)

SimpleLLM is an EU-hosted LLM inference service providing OpenAI-compatible API endpoints. They publish SDKs for Node.js/TypeScript, Rust, Go, and C++. **No Python package called `simplyllm` appears in their public repositories.**

---

## Flexrouter's `wait=True` Mechanism

For comparison, here's what flexrouter actually does:

### Code Path (flexrouter/_router.py, lines 221-225)
```python
if not wait:
    raise RouterBusy(f"All models in tier {tier!r} are unavailable")
secs = self._engine.seconds_until_available(tier)
await asyncio.sleep(max(secs, 1.0))
continue
```

### How it works

1. **Tier Selection**: Tries to pick a model from a hand-written YAML tier (not automatic provider search)
2. **Rate Limit Calculation**: `seconds_until_available()` (engine.py:99-118) computes the minimum wait by checking:
   - **Penalties**: Exponential backoff (30s → 60s → 120s → ... → 1800s max) on 429/5xx
   - **Sliding Window**: Tracks RPM/TPM within a rolling window (default 60 seconds)
   - **Quotas**: Daily budget tracking per provider
3. **Blocking Sleep**: `await asyncio.sleep(max(secs, 1.0))` blocks until the first model becomes available
4. **Retry Loop**: Re-enters selection logic after sleep; repeats up to `retries` times

### Sliding Window Algorithm
- Maintains deque of request timestamps and (timestamp, token_count) tuples
- On each request, purges entries older than `window_seconds` (60 default)
- Availability = (current_rpm < limit AND current_tpm < limit)
- Wait time = oldest_request_timestamp + window_seconds - now (how long until oldest expires)

### State Persistence
- Stored in `.flexrouter/` (configurable `state_dir`)
- Audit trail: `audit.csv` (every request logged)
- Health snapshots: `health.json` (rate limit state, penalties, model status)

### Key Difference vs simplyllm's `max_wait`
- **flexrouter**: Calculates exact time until oldest rate-limit window expires, sleeps that duration
- **simplyllm (claimed)**: Blocks caller until capacity returns, with ~80-second default timeout

Flexrouter's approach is **precise and predictable** (sleeps exactly as long as needed), whereas simplyllm's would be **timeout-based** (risk of timeout expiration before capacity returns).

---

## What does better than flexrouter

### SimplerLLM
- **Unified API across providers**: One `LLM.create()` interface, no YAML tier configuration needed
- **Automatic provider selection**: Doesn't require hand-coded model tiers; can pick "best" model dynamically
- **Simpler onboarding**: Fewer moving parts (no penalty box config, window sizing, budget tracking)
- **Built-in content loaders**: Text extraction from PDF, DOCX, YouTube (flexrouter leaves this to caller)
- **Embeddings first-class**: Native embedding support; flexrouter doesn't mention it
- **Structured output** via Pydantic validation

**Adoption difficulty: MEDIUM** — simpler mental model, but code rewrite required (not SDK-compatible).

### simplyllm (if it exists and has max_wait)
- **Timeout-bounded wait**: Caller doesn't block forever (80s default); flexrouter's `wait=True` has no timeout
- **Drop-in OpenAI-SDK compatibility** (UNVERIFIED claim): If truly compatible, zero code changes
- **Automatic failover across providers** (UNVERIFIED): Assumes simplyllm auto-selects providers like SimplerLLM does

**Adoption difficulty: EASY** (if SDK-compatible) — drop-in replacement. **HARD** (if not).

---

## What flexrouter does better

### Tier Isolation
- **No cross-tier fallback**: A busy `low` tier never cascades to `high` (prevents cost explosion)
- SimplerLLM/simplyllm cross-provider failover doesn't respect cost boundaries
- Enforces strict SLA isolation

### Precise Rate Limit Tracking
- **Sliding window**: Exact RPM/TPM tracking; no loss of fidelity from fixed-window collisions
- **Per-provider state**: Knows which provider is rate-limited, routes around it
- **Visible state**: Audit trail + health snapshots let you debug exactly what happened

### Cost Visibility
- **Per-provider daily budgets**: `provider_budget: {openai: 5.00}` caps spend automatically
- **Audit logging**: Every request → CSV with cost, tokens, latency
- **Health history**: Time-series snapshots of rate limit state
- SimplerLLM has no documented cost tracking

### Penalty Differentiation
- **Temporary vs. permanent**: Distinguishes 404/410 (model deleted, quarantine 24h) from 429 (rate limit, backoff 30s)
- **Provider-wide failures**: 401/403/402 sideline the whole provider, not just one model
- SimplerLLM's failover logic is opaque

### Session Stickiness
- **Conversation pinning**: `session_id` keeps one conversation on the same model (avoiding context switching)
- SimplerLLM doesn't mention this feature

### Configuration Hot-Reload
- **Edit flexrouter.yaml while running**: Changes picked up on next request, state preserved
- SimplerLLM requires restart

### Multi-key Round-Robin
- **Multiple API keys per provider**: Rotate automatically, skip keys that return 429
- SimplerLLM doesn't document this

### Local-First State
- **No external service required**: All state in `.flexrouter/` (audit + health JSON)
- SimplerLLM state handling is undocumented

---

## Ideas worth stealing for v2

### From SimplerLLM (if applicable)

1. **Auto-provider selection** ⭐⭐⭐ HARD
   - Let flexrouter discover and score available models dynamically (not just hand-coded tiers)
   - Requires provider discovery API (OpenAI list_models endpoint equivalent for each)
   - Payoff: Eliminates stale config problem (PLAN.md mentions 96 models, many deleted)

2. **Unified embedding API** ⭐⭐ MEDIUM
   - Add `router.embed(text, tier=...)` alongside `router.generate()`
   - Reuses same rate-limit/penalty/routing infrastructure
   - Payoff: One place to manage embedding rate limits vs. completion limits

3. **Built-in content loaders** ⭐ LOW
   - `router.generate_from_pdf()` convenience wrapper
   - Payoff: Slightly nicer UX; not core to routing

### From simplyllm (UNVERIFIED, if it exists)

1. **Timeout-bounded wait()** ⭐⭐ MEDIUM
   - Add `wait_timeout_seconds=None` parameter to `generate()`
   - If hit, fall through to next tier instead of raising RouterBusy
   - Payoff: Prevents indefinite hangs; caller gets *something* in bounded time
   - Risk: Might return worse model if high tier is rate-limited but timeout fires

2. **Drop-in SDK compatibility** ⭐⭐⭐ VERY HARD
   - Subclass `openai.OpenAI` / `openai.AsyncOpenAI`
   - Mirror the full API surface (streaming, vision, tools, structured output)
   - Payoff: Near-zero adoption friction for existing OpenAI users
   - Risk: Chasing OpenAI API surface is a moving target; we'll always lag

---

## Traps / Things NOT to copy

### From SimplerLLM

1. **Opaque failover**: ReliableLLM switches providers without logging why or how long wait was
   - Flexrouter's approach (events + audit trail) is better for debugging
   - Don't sacrifice observability for simplicity

2. **One-size-fits-all retry**: No configuration of retry count, backoff, retry-on-what
   - Flexrouter's `retry_policy: (conservative|balanced|aggressive)` is better
   - Different callers have different risk tolerances (quick-fail vs. resilient)

3. **Undocumented rate limiting**: If SimplerLLM exists and has no documented rate-limit mechanism, don't copy that pattern
   - Rate limiting *must* be explicit and configurable
   - Users need to understand their own limits

4. **No session stickiness**: Switching providers mid-conversation breaks context
   - If copying failover, preserve session pinning

### General

1. **Hidden state**: Don't keep rate-limit/penalty state in-memory only
   - Make it visible (audit + snapshots like flexrouter)
   - Operators must be able to debug what happened

2. **Timeout as a substitute for capacity planning**: `max_wait` timeout is a band-aid
   - Better: **queue-based backpressure** or **connection pooling** to prevent hangs
   - Timeout just hides the problem under a different name (dropped requests)

3. **Auto-selection without cost bounds**: If adding provider auto-discovery, still enforce per-provider budgets
   - Don't cascade to expensive tiers when cheap ones are rate-limited
   - SimplerLLM's failover doesn't mention cost-awareness

---

## Evidence / Sources

### SimplerLLM
- GitHub: [hassancs91/SimplerLLM](https://github.com/hassancs91/SimplerLLM)
- PyPI: [simplerllm](https://pypi.org/project/simplerllm/)
- Docs: [simplerllm.com](https://simplerllm.com/) and [docs.simplerllm.com](https://docs.simplerllm.com/)
- README: Verified via raw.githubusercontent.com

### SimpleLLM (EU Service)
- Organization: [github.com/SimpleLLM](https://github.com/SimpleLLM)
- Website: [simplellm.eu](https://simplellm.eu/)
- Repositories: sdk-js, sdk-rs, sdk-go, sdk-cpp, openclaw-provider, opencode-provider
- **No Python package**

### simplyllm (Target)
- PyPI link (from search): [pypi.org/project/simplyllm](https://pypi.org/project/simplyllm)
- **Page failed to load** (WebFetch returned error)
- **No GitHub repository found** despite multiple search queries
- **Features `max_wait` / automatic failover / rate limiting: UNVERIFIED**

### Search queries performed
1. `simplyllm PyPI package multi-provider OpenAI SDK compatible`
2. `simplyllm package max_wait automatic fallback rate limiting`
3. `"simplyllm" PyPI` → Found PyPI link but page failed to load
4. `simplyllm GitHub repository source code`
5. `"simplyllm" github max_wait fallback rate limiting`
6. `site:github.com/SimpleLLM` (found EU service, not Python package)
7. `"max_wait" python OpenAI SDK compatible automatic failover rate limiting`
8. `simplyllm python package site:github.com OR site:pypi.org`
9. Various exclusions and negations to eliminate SimplerLLM and other similar projects

---

## Recommendation for v2

**Do not pursue simplyllm as a direct comparison target** unless:
1. You can provide a working GitHub/PyPI link
2. You can confirm the `max_wait` behavior (how it's implemented, not just claimed)
3. You can verify OpenAI-SDK compatibility via actual code inspection

**If the package is real**, the most valuable research would be:
- **OpenAI-SDK compatibility technique**: Inspect how it subclasses/wraps the SDK, what breaks, coverage gaps
- **max_wait timeout behavior**: Trace what happens when timeout expires (exception? fallback to worse provider? partial response?)
- **Rate limiting state**: Where is it stored (in-memory? distributed?), how is it synchronized across instances?

**In the meantime**, focus on flexrouter's proven strengths (tier isolation, audit trail, cost tracking) and defensible gaps:
- Add timeout-bounded `wait()` as an option (hard stop at N seconds)
- Improve provider discovery (refresh stale models automatically)
- Consider SDK-compatibility as a separate project (high effort, high payoff for adoption)
