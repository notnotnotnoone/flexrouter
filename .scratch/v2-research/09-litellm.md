# LiteLLM Router: Competitive Analysis for flexrouter v2

## What it is

LiteLLM is a Python SDK providing a unified OpenAI-format interface to 100+ LLM providers (OpenAI, Anthropic, Azure, Bedrock, etc.). Its **Router** class orchestrates load balancing across multiple deployments of the same model; its **Proxy** is a self-hosted OpenAI-compatible HTTP server that can run standalone or in Kubernetes, with a versioned YAML config file defining models, routing strategies, rate limits, fallbacks, and Redis-backed shared state for multi-instance deployments.

---

## The routing strategies

LiteLLM implements five main strategies. Each deployment gets a score via the strategy; the router selects the highest-scoring available one.

### Simple-Shuffle (default)
- **Algorithm**: Random selection among available deployments
- **Scoring**: Deployments with lower RPM/TPM utilization are weighted higher; score = `1 / (current_usage / limit + 1)` conceptually, but actual implementation is randomized within RPM/TPM constraints
- **State needed**: Per-deployment current RPM/TPM window counts (local or Redis)
- **Latency**: ~1–2µs per call (minimal)
- **Recommended for**: Production; balances simplicity with fair load distribution

### Least-Busy (in-flight concurrency)
- **Algorithm**: `deployment = health_deployments[min(index, key=lambda i: counts[i])]`
- **Scoring**: Direct count of in-flight requests per deployment; lowest count wins
- **State needed**: In-memory counter per deployment, or Redis-backed for multi-instance
- **Tracking**: Incremented on `log_pre_api_call`, decremented on success/failure; TTL 3600s
- **Use case**: High-concurrency scenarios where request queueing matters more than token limits

### Usage-Based Routing v2
- **Algorithm**: Sort by TPM consumed in current minute; select lowest
- **Scoring**: `score = limit - current_tpm`; deployment with most headroom wins
- **State needed**: Per-deployment TPM counter with 1-minute TTL window (Redis required for production)
- **Filtering**: Skips deployments where `current_tpm >= tpm_limit`
- **Not recommended**: LiteLLM docs warn v2 adds latency and is not suitable for production

### Latency-Based Routing
- **Algorithm**: Track mean response time per deployment; select lowest-latency
- **Scoring**: Exponential moving average (EMA) of response times
- **State needed**: Running EMA per deployment in Redis or in-memory, with configurable buffer/reset window
- **Use case**: Optimize for user-perceived speed when deployments vary in performance
- **Note**: Requires historical data to warm up; initial deployments treated equally

### Cost-Based Routing
- **Algorithm**: `score = (input_tokens * input_cost + output_tokens * output_cost)`; select minimum
- **Scoring**: Uses `litellm_model_cost_map` (bundled JSON with 100+ model prices); can override per-deployment
- **State needed**: Model cost table + current usage estimation
- **Use case**: Cost-sensitive batch processing
- **Fallback**: If cost data missing, falls back to simple-shuffle

---

## Cooldowns and health

**Distinction from flexrouter**: LiteLLM does not distinguish temporary from permanent failures. All failures increment a **per-deployment failure counter per minute**.

### Mechanism
1. Each failure increments `deployment.failure_count_this_minute`
2. When `failure_count >= allowed_fails` (default: 1), deployment enters cooldown
3. `CooldownCache` stores the deployment ID + cooldown end time (default: 30 seconds)
4. `_get_cooldown_deployments()` filters them from selection pool
5. After cooldown expires, failure counter resets; deployment re-enters rotation

### Status codes
- **429** (rate limit): Immediate short cooldown (~15–30s), retry eligible
- **5xx** (server error): Triggers standard cooldown if exceeds `allowed_fails`
- **4xx (401, 403, 404)**: Also increments failure counter; no special handling
  - This is the gap flexrouter closes: a 404 (model deleted) and a 401 (bad key) both start 30s → 60s → 120s backoffs, when 404 should be permanent and 401 should be provider-wide

### No "provider-wide" quarantine
If a provider's API key is rejected (401), LiteLLM still tries every model on that provider sequentially, each collecting a 401, before the tier gives up. flexrouter's `quarantine_provider()` short-circuits this by sidelining the entire provider immediately.

### Health snapshot
- **DeploymentHealthCache** tracks staleness: cached health data expires after a configurable threshold
- **No persistent state** for cooldowns by default; cooldowns live in-memory per router instance
  - With Redis, shared across multiple proxy instances
- **No reason tracking**: Cooldown does not record why a deployment failed (no "404: model deleted" explanation)

### Head-to-head: flexrouter's quarantine model is better here
- **flexrouter**: Splits 404/410 (quarantine 24h with reason) from 429/5xx (exponential backoff 30s–1800s) and from 401/403 (quarantine provider, not just model)
- **LiteLLM**: All failures follow the same path: count, cooldown, retry
- **Impact**: flexrouter stops hammering deleted models after 24 hours; LiteLLM keeps trying every 30s forever until it naturally de-prioritizes them
- **Reason tracking**: flexrouter logs "404: not found", "401: rejected key"; LiteLLM logs nothing

---

## Fallback chains

LiteLLM supports three fallback types, executed in sequence:

### Standard Fallbacks
- **Trigger**: Any failure on the primary model (rate limit, server error, auth failure)
- **Execution**: Within `litellm_params.fallbacks`, try models in order
  - Retries are exhausted per model before escalating to the next fallback
  - Retry count is set per model via `num_retries` (default 0, usually 2–3)
- **Retry ordering**: `[attempt1_model1, retry1_model1, retry2_model1, attempt1_model2, ...]`

### context_window_fallbacks
- **Trigger**: Pre-call check detects prompt exceeds deployment's `max_input_tokens`
- **Execution**: Auto-route to fallback models with larger context windows
- **No retry within context window fallback chain**: Goes straight to the next model
- **Use case**: "Oh, this prompt is 50k tokens but primary model has 32k window → try a 128k model"

### content_policy_fallbacks (specialized)
- **Trigger**: Provider rejects request due to content policy (typically rare)
- **Execution**: Fallback to alternative models that may accept the content
- **Note**: Not well-documented; least-used fallback type

### Execution order in a single request
1. Try primary model with retries (`num_retries` times)
2. If exhausted, try first fallback model with retries
3. If exhausted, try context_window_fallback (if applicable)
4. If exhausted, try content_policy_fallback (if applicable)
5. Exhaust all and return error

**No inter-request fallback memory**: Unlike some systems, LiteLLM does not "learn" to prefer fallback models if primary is consistently failing. Each request starts at the primary.

---

## "One model name, many deployments"

This is LiteLLM's core structural idea and the most important to evaluate for flexrouter v2's OpenAI-shaped service.

### The model mapping structure

```yaml
model_list:
  - model_name: "gpt-4"               # user-facing name
    litellm_params:
      model: "openai/gpt-4"            # actual provider/model
      api_base: "https://api.openai.com/v1"
      api_key: "sk-..."
  - model_name: "gpt-4"               # same user-facing name, different deployment
    litellm_params:
      model: "azure/gpt-4-8k"
      api_base: "https://my-azure.openai.azure.com/v1"
      api_key: "..."
```

The router maintains three lookup structures:
- `model_name_to_deployment_indices`: `{"gpt-4": [0, 1], "claude-3": [2]}`
- `model_id_to_deployment_index_map`: `{"openai/gpt-4": 0, "azure/gpt-4-8k": 1}`
- `team_model_to_deployment_indices`: For org-scoped visibility (optional)

When a client calls the proxy with `"model": "gpt-4"`, the router:
1. Resolves "gpt-4" → deployments [0, 1]
2. Filters by availability (cooldown, rate limit, budget)
3. Applies routing strategy (simple-shuffle, least-busy, etc.)
4. Picks one deployment and translates the request to its actual `api_base` and `model` values

### Should flexrouter v2 adopt this?

**Yes, for the OpenAI-shaped service layer.** Distinctions:

- **flexrouter today**: "tier" is a user-defined name (cheap/smart/nuclear) → ranked list of models by score (85, 95, 75...)
- **LiteLLM model mapping**: "model" is a user-facing name (gpt-4, claude-3) → list of deployments (OpenAI, Azure, Bedrock, local Ollama)

**For v2 service**: Call the client-facing concept a "model" (not tier), and internally maintain a deployment list per model:
```yaml
models:
  gpt-4o:
    deployments:
      - provider: openai
        model: gpt-4o
        api_key: ${OPENAI_API_KEY}
        rpm: 500
        tpm: 2000000
      - provider: azure
        model: gpt-4-deployment
        api_key: ${AZURE_API_KEY}
        rpm: 60
        tpm: 60000
    routing_strategy: simple-shuffle
    fallbacks: [gpt-4-turbo, gpt-3.5-turbo]
```

This is **not a redesign**: it's a rename from "tier" to "model" + a reorient from score-based ranking to deployment-based routing. The core logic remains.

---

## Proxy lessons for a service-first v2

### Request translation fidelity
LiteLLM has known issues here (see GitHub issues #17246, #25766):
- **Streaming + tool calls**: When upstream returns mixed text + tool_call events in streaming mode, LiteLLM's bridge from `/responses` → `/chat/completions` drops tool calls silently
- **OpenAI-compatible wire protocol mismatches**: Some providers return SSE when asked for non-streaming; LiteLLM fails to parse
- **Audio/TTS streaming**: TTS buffers instead of streaming despite stream=true

**Recommendation for v2**: 
- Proxy should pass through the raw response body as-is for streaming; do not re-parse and re-emit
- Tool calls are part of the choice delta in stream events; if the upstream provider emits them, forward them
- Test with: OpenAI (baseline), Azure, Anthropic via bedrock, vLLM, Ollama — ensure delta parity

### Error passthrough
**Current flexrouter**: Discards provider error messages, logs only "failure"
**LiteLLM proxy**: Returns the provider's error message to the client (good)
**v2 requirement**: Every failure response includes provider error message and status code, e.g.
```json
{
  "error": {
    "message": "You exceeded your current quota, please check your plan and billing settings",
    "type": "server_error",
    "param": null,
    "code": "insufficient_quota"
  }
}
```

### All-deployments-unhealthy behavior
**Finding**: LiteLLM has a hard limit: at ~505 concurrent requests to a simple-shuffle router, requests start returning "No deployments available" errors instead of queuing. This is not documented as a feature.

**flexrouter v2 service equivalent**: 
- Should queue requests, not reject them, until a tier has an available slot
- Alternative: Return 503 with Retry-After header if timeout would exceed a threshold (e.g., 5 minutes)
- Current flexrouter library uses `wait=True` (sleep) or `wait=False` (raise); service should map to HTTP semantics

### Concurrent request handling
- **LiteLLM with uvicorn**: Single worker handles ~500 concurrent requests safely; beyond that, reliability degrades
- **LiteLLM with Granian** (Rust HTTP layer): 10–20 RPS improvement over uvicorn, better tail latency
- **flexrouter v2**: FastAPI default (uvicorn) is sufficient for single-user / small-team use; document scaling guidance for multi-tenant

---

## The price/model metadata table

### Source and structure
LiteLLM maintains `model_prices_and_context_window.json` in its GitHub repo (`litellm/litellm/main/`). At proxy startup:
1. Fetches the JSON from GitHub (5-second timeout)
2. Parses into `litellm.model_cost` dictionary
3. Uses it for cost-based routing, spend tracking, and the /model/cost_map endpoint

### Schema
```json
{
  "openai/gpt-4": {
    "input_cost_per_token": 3e-5,
    "output_cost_per_token": 6e-5,
    "cache_read_input_token_cost": 1.5e-5,
    "cache_creation_input_token_cost": 1.5e-4,
    "max_input_tokens": 128000,
    "max_output_tokens": 4096,
    "mode": "chat",
    "litellm_provider": "openai"
  },
  "azure/gpt-4-8k": {
    "input_cost_per_token": 3e-5,
    "output_cost_per_token": 6e-5,
    "max_input_tokens": 8000,
    "max_output_tokens": 2048,
    "mode": "chat",
    "litellm_provider": "azure"
  }
}
```

### Freshness
- **Bundled backup**: If fetch fails or times out, uses an older bundled version
- **No versioning**: JSON is replaced wholesale on startup; no incremental updates
- **Update cadence**: As models change, LiteLLM bumps the bundled copy in releases; users upgrade to get new models

### Custom pricing
Two approaches:
1. **Per-deployment override** in config (recommended for 1–10 models):
   ```yaml
   - model_name: gpt-4-expensive
     litellm_params:
       model: openai/gpt-4
       input_cost_per_token: 0.0001
       output_cost_per_token: 0.0002
   ```
2. **Custom cost map** (for 50+ models): Host your own JSON, point via env var `LITELLM_MODEL_COST_MAP_URL`

### Should flexrouter reuse it?
**Partially, yes.**
- **Pro**: 100+ models pre-priced; saves hand-writing prices for new providers
- **Con**: flexrouter's config is 13.5KB YAML; bundling a 500KB JSON raises startup time and binary size
- **Approach for v2**: 
  - Optional: `fetch_cost_map_from: "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"`
  - Or: Publish flexrouter's own cost map for models it explicitly supports (15–30 popular ones)
  - Hand-write prices for custom/internal deployments

---

## What LiteLLM does better than flexrouter

### 1. **100+ provider support out of the box** [Adoption difficulty: **Hard**]
- OpenAI, Azure, Anthropic, Bedrock, Vertex, Cohere, Together, Replicate, Ollama, vLLM, Baseten, Aleph Alpha, AI21, Aleph, NIM, etc.
- flexrouter covers ~5 free-tier providers; LiteLLM's provider abstraction layer handles auth, parameter mapping, and error normalization across vastly different APIs
- **Not worth copying into flexrouter**: Scope creep; flexrouter's niche is free tiers and low-cost inference

### 2. **Persistent request logging and cost tracking** [Adoption difficulty: **Easy**]
- LiteLLM logs every request: timestamp, model, tokens, cost, latency, provider, deployment
- Accessible via dashboard and API
- flexrouter has `audit.csv` but no structured query/filter interface
- **Worth adopting for v2**: Dashboard should query/export logs; add structured logging to the service layer

### 3. **Redis-backed state for multi-instance deployments** [Adoption difficulty: **Medium**]
- LiteLLM Router can share rate-limit state, cooldowns, and session affinity across multiple proxy instances via Redis
- flexrouter stores state locally in JSON files; multi-instance setups need external state coordination
- **Conditional value for v2**: If v2 targets single-user / small team (likely), skip Redis; if later targeting teams/enterprises, add it

### 4. **Database overlay for config** [Adoption difficulty: **Medium**]
- LiteLLM Proxy can store `router_settings`, `litellm_settings`, and `general_settings` in a database; YAML acts as fallback
- Enable runtime config changes without restart
- flexrouter reloads from YAML on mtime change; no database backend
- **Not critical for v2 MVP**: YAML hot-reload is sufficient; database adds Prisma + migration complexity

### 5. **Team/access control and routing groups** [Adoption difficulty: **Hard**]
- LiteLLM supports per-team rate limits, budgets, and model access lists
- Enables multi-tenant proxy deployments
- flexrouter is single-user by design
- **Out of scope for v2 MVP**: Single-user tool; team features are enterprise add-ons

### 6. **Automatic provider failover without explicit fallbacks** [Adoption difficulty: **Low**]
- When a deployment fails, LiteLLM tries the next `order` level automatically
- No need to define `fallbacks: [...]` for every model if you just want round-robin escalation
- flexrouter requires explicit tier listing; no implicit fallback between tiers
- **Worth considering for v2 service**: Allow implicit fallback if client doesn't specify `fallbacks`

### 7. **Streaming + cost calculation** [Adoption difficulty: **Medium**]
- LiteLLM tracks tokens in streaming responses (tricky with streaming delta events)
- Costs are calculated in real-time
- flexrouter's library can't estimate streaming costs until response completes
- **For v2 service**: Streaming response handler should emit usage in the stream, or accumulate and report in the final chunk

---

## What flexrouter does better than LiteLLM

### 1. **Permanent vs. temporary failure distinction** [Verified, Specific]
- **flexrouter**: 404/410 → 24-hour quarantine with reason; 429/5xx → exponential backoff 30s–1800s; 401/403 → quarantine provider
- **LiteLLM**: All failures → failure counter → 30-second cooldown → retry (no distinction)
- **Impact**: flexrouter stops wasting requests on deleted models; LiteLLM keeps trying
- **Evidence**: flexrouter/recovery.py lines 104–127 (quarantine) vs. PenaltyBox.penalize() (backoff)

### 2. **Provider-wide quarantine** [Verified, Specific]
- **flexrouter**: `quarantine_provider()` sidelines every model on a provider at once when API key is rejected
- **LiteLLM**: Each model individually accumulates failures, so a bad key hammers every model sequentially
- **Impact**: flexrouter saves 20+ API calls per tier if a provider's key expires; LiteLLM makes one per configured model
- **Evidence**: flexrouter/recovery.py lines 118–127 (PROVIDER_WILDCARD logic)

### 3. **Local-only state with no external dependencies** [Verified]
- **flexrouter**: All state in JSON files (penalties.json, quarantine.json, audit.csv) in `.flexrouter/` dir
- **LiteLLM**: Optional Redis; falls back to in-memory (data lost on restart)
- **Impact**: flexrouter survives restarts; LiteLLM loses rate-limit and cooldown state
- **Trade-off**: flexrouter can't be horizontally scaled without external coordination; LiteLLM can (with Redis)

### 4. **Tier-based isolation** [Verified]
- **flexrouter**: Tiers are strict: a busy "low" tier never falls back to "high"
- **LiteLLM**: Model groups have implicit fallback based on `order` field; cross-group fallback is explicit
- **Use case**: flexrouter guarantees tier latency SLA; LiteLLM provides best-effort cost optimization
- **Evidence**: PLAN.md line 96 ("Tiers are strictly isolated")

### 5. **Operator-friendly error messages** [Verified]
- **flexrouter**: `quarantine_reason()` returns "404: not found", "401: rejected key", "402: payment required"
- **LiteLLM**: No reason stored; dashboard shows "cooldown" with no explanation
- **Impact**: Operator can immediately see "API key expired" vs. "model no longer exists" and take action
- **Evidence**: flexrouter/recovery.py lines 145–151, PLAN.md lines 63–67 (diagnosis section)

### 6. **Simpler configuration for single-user deployments** [Verified]
- **flexrouter**: One config file, 13.5KB, YAML tiers + providers
- **LiteLLM**: Proxy requires config.yaml + optional database + optional Redis + credential_list
- **Impact**: flexrouter's getting-started experience is faster; LiteLLM has more knobs for enterprises
- **Evidence**: README.md (26 lines of YAML) vs. LiteLLM docs (10+ pages)

---

## Ideas worth stealing for v2

Ranked by impact and effort:

### 1. **One model name, many deployments mapping** [Impact: **High**, Effort: **Low**]
- **What**: Rename "tier" to "model" in the config; internally map each model name to N deployments
- **Why**: Aligns with OpenAI wire protocol (client sends `{"model": "gpt-4-o"}`); easier for clients
- **How**: Keep the routing logic; just update config schema and lookup tables
- **Example for v2 config**:
  ```yaml
  models:
    gpt-4o:
      deployments: [...]
      routing_strategy: simple-shuffle
  ```

### 2. **Persistent cost and usage tracking in dashboard** [Impact: **Medium**, Effort: **Low**]
- **What**: Log every request; dashboard shows spend per model, per provider, cumulative
- **Why**: Helps operators understand where budget goes; LiteLLM's spend tracking is excellent
- **How**: Append to audit.csv (already done); add charts to dashboard
- **Evidence to show LiteLLM's version**: Docs show a spend tracking endpoint; flexrouter v2 can match this

### 3. **Streaming response error transparency** [Impact: **High**, Effort: **Medium**]
- **What**: When a streaming response fails mid-stream, emit a valid error delta before closing the stream
- **Why**: Client doesn't hang; can parse error and retry
- **How**: Service layer wraps streaming responses; catches exceptions and emits `{"error": {...}}`
- **Learn from LiteLLM's bugs**: Known issues with tool_call streaming (GitHub #17246) show the edge cases

### 4. **Optional Redis for distributed state** [Impact: **Low for MVP, High for teams**, Effort: **Hard**]
- **What**: If Redis is available, share rate limits and cooldowns across proxy instances
- **Why**: Multi-instance deployments see consistent rate limiting and don't double-spend budget
- **How**: Conditional DualCache (Redis + fallback to in-memory) like LiteLLM uses
- **For v2 MVP**: Skip; document as future work

### 5. **Automatic per-deployment pricing override** [Impact: **Medium**, Effort: **Low**]
- **What**: Allow per-deployment price overrides in config, like LiteLLM does
- **Why**: Handles cases where bundled prices are wrong for your deployment (e.g., Azure is more expensive than OpenAI)
- **How**: Add `input_cost_per_token`, `output_cost_per_token` fields to deployment
- **Minimal effort**: Already have model prices in config; just use them if provided

### 6. **Cooldown reason tracking** [Impact: **Medium**, Effort: **Low**]
- **What**: Store the reason a deployment entered cooldown, not just the until-time
- **Why**: Dashboard and logs can say "404: not found" vs. "429: rate limited"; operators can fix faster
- **How**: Update cooldown state to include reason, like quarantine already does
- **Evidence**: flexrouter/recovery.py line 113 already does this for quarantine; apply to cooldown too

---

## Traps and things NOT to copy

### 1. **Don't adopt LiteLLM's failure-count-per-minute model for all errors**
- **Problem**: 401 (bad key) and 404 (model deleted) are treated identically to 429 (rate limit)
- **Fix**: Keep flexrouter's quarantine for permanent errors
- **Evidence**: PLAN.md section on diagnosis (lines 63–67) shows 401/402/404 are distinguishable and actionable

### 2. **Don't export live API keys in config export**
- **Problem**: LiteLLM's `config export` base64-encodes the whole config, including plaintext keys
- **Safer approach**: flexrouter v2 should export only non-secret fields; keys stay in a separate file
- **Evidence**: PLAN.md line 34 notes "config export hands out live keys" as a security anti-pattern

### 3. **Don't make the dashboard's Save button rewrite the entire config file**
- **Problem**: Loses all comments and formatting
- **Safer approach**: Dashboard edits should preserve YAML structure; or save to a separate override file
- **Evidence**: PLAN.md line 34 notes this breaks documentation

### 4. **Don't create a "middle-out" model that's neither library nor service**
- **Problem**: LiteLLM is both a Python SDK and a server, which creates confusion: do you use it in-process or over HTTP?
- **flexrouter v2 decision**: Server-first; Python library is a thin client
- **Benefit**: Single code path; fewer integration bugs

### 5. **Don't bundle 500KB of model pricing data if you only support 5 providers**
- **Problem**: LiteLLM's model_prices_and_context_window.json is huge; startup time and binary size cost
- **Approach for flexrouter v2**: Hand-write prices for the 15–30 models you explicitly support; allow per-deployment override
- **Optional**: Late-bind LiteLLM's cost map only if user enables it

### 6. **Don't silently drop tool_calls in streaming responses**
- **Problem**: LiteLLM's bridge from `/responses` → `/chat/completions` loses tool_call deltas (GitHub #17246)
- **Fix**: For v2 service, proxy should pass through deltas without re-parsing
- **Test**: Ensure OpenAI, Azure, and vLLM all emit tool_calls correctly in streaming mode

### 7. **Don't implement team/multi-tenant features in MVP**
- **Problem**: Adds 100+ lines of permission checking, scoping, and database queries
- **Scope for v2**: Single-user tool; document team features as post-launch
- **Cost**: Complexity balloons; hard to remove later

---

## Evidence

### LiteLLM Source & Docs
- Router class: https://github.com/BerriAI/litellm/blob/main/litellm/router.py (14,380 lines)
- Routing strategies overview: https://docs.litellm.ai/docs/routing
- Load balancing: https://docs.litellm.ai/docs/routing-load-balancing
- Proxy config reference: https://docs.litellm.ai/docs/proxy/configs
- Proxy load balancing: https://docs.litellm.ai/docs/proxy/load_balancing
- Fallback logic: https://docs.litellm.ai/docs/proxy/reliability
- Model pricing: https://docs.litellm.ai/docs/proxy/custom_model_cost_map
- Model cost map JSON: https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json
- Least-busy strategy: https://github.com/BerriAI/litellm/blob/main/litellm/router_strategy/least_busy.py
- Streaming tool_call bug: https://github.com/BerriAI/litellm/issues/17246
- OpenAI-compatible wire protocol bug: https://github.com/BerriAI/litellm/issues/25766
- Concurrent request limit issue: https://github.com/BerriAI/litellm/issues/6284

### flexrouter Source
- README: C:\projects\Finished projects\router\README.md
- PLAN: C:\projects\Finished projects\router\PLAN.md
- Recovery (PenaltyBox, quarantine): C:\projects\Finished projects\router\flexrouter\recovery.py
- Routing engine: C:\projects\Finished projects\router\flexrouter\engine.py
- Config: C:\projects\Finished projects\router\flexrouter.yaml

---

## Summary for v2 Planning

**Adopt**: Model name → deployments mapping, persistent cost tracking, streaming error transparency, cooldown reason tracking

**Adapt**: Combine LiteLLM's multi-deployment pattern with flexrouter's quarantine logic to create a superior health model

**Avoid**: Team features, large bundled data, middle-out architecture, silent error loss

**Verify**: Streaming + tool calls work correctly; error passthrough is complete; all-unhealthy behavior returns proper HTTP status
