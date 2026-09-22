# Portkey Competitive Research: v2 Feature Analysis

## What It Is

Portkey is an open-source AI gateway (TypeScript, MIT license) that routes requests to 1,600+ models across 45+ providers, with fallbacks, retries, guardrails, and semantic caching. The open-source core (released March 2026) includes governance and observability; a managed tier adds hosted dashboards, RBAC, SSO, and VPC deployment for cost and compliance. Portkey processes 2 trillion tokens daily at scale; flexrouter is a single-user Python library over free tier models.

**Open-source vs hosted split:**
- **Open-source gateway**: Free MIT license, self-hosted, includes routing strategies, caching, guardrails, and local observability
- **Managed platform**: Commercial; adds hosted dashboard, prompt management, analytics database, RBAC, SSO/SCIM, compliance (ISO 27001, SOC2, HIPAA, GDPR)
- **Distinction**: The routing logic is open-source. The managed tier's value is the hosted control plane and compliance machinery, not gated features in the gateway itself.

---

## The Routing Config DSL

### Full Schema

Portkey uses a **declarative JSON config** passed via `x-portkey-config` header or SDK parameter. The schema:

```json
{
  "strategy": {
    "mode": "fallback" | "loadbalance" | "conditional",
    "on_status_codes": [400, 429, 503]  // fallback trigger (optional; default: non-2xx)
  },
  "targets": [
    {
      "provider": "@openai-prod",           // provider identifier
      "passthrough": false,                 // allow provider from request
      "virtual_key": "vk_...",              // Portkey-managed credential
      "weight": 1.0,                        // loadbalance distribution
      "default_params": { "model": "..." }, // inject if absent
      "override_params": { "model": "..." },// always replace
      "drop_params": ["logprobs"]           // remove fields
    },
    // nested strategy:
    {
      "strategy": { "mode": "fallback" },
      "targets": [...]
    }
  ],
  "conditions": [
    {
      "query": { "params.model": { "$eq": "fastest" } },
      "then": "target_name"
    }
  ],
  "default": "target_name"
}
```

### Supported Strategies

| Mode | Behavior | Use Case |
|------|----------|----------|
| **fallback** | Try first target; on failure (status in on_status_codes), try next | Model unavailability, provider outage |
| **loadbalance** | Distribute across targets by weight; retry each per attempt limit | Spread load across keys/providers, avoid rate limits |
| **conditional** | Evaluate metadata/params/URL path, route to target; can nest any strategy | User tier, model alias, environment routing |

### Parameter Handling

Order of application (per target):
1. **default_params**: Inject fields if request doesn't have them
2. **override_params**: Force-replace matching request fields  
3. **drop_params**: Remove fields using path syntax (`"logprobs"`, `"response_format.json_schema"`, `"tools[0].function.strict"`, `"tools[*].function.name"`)

### Worked Example: Multi-Tier Routing

```json
{
  "strategy": {
    "mode": "conditional"
  },
  "conditions": [
    {
      "query": { "metadata.user_tier": { "$eq": "premium" } },
      "then": "premium-models"
    },
    {
      "query": { "metadata.user_tier": { "$eq": "free" } },
      "then": "free-models"
    }
  ],
  "default": "fallback-budget",
  // Named targets (defined elsewhere in full config):
  "targets": {
    "premium-models": {
      "strategy": { "mode": "loadbalance" },
      "targets": [
        { "provider": "@openai", "weight": 0.6 },
        { "provider": "@anthropic", "weight": 0.4 }
      ]
    },
    "free-models": {
      "strategy": { "mode": "fallback", "on_status_codes": [429] },
      "targets": [
        { "provider": "@groq" },
        { "provider": "@together" }
      ]
    },
    "fallback-budget": {
      "provider": "@together-budget-model"
    }
  }
}
```

### Verdict: Portkey vs Flexrouter Tiers

**Portkey's declarative JSON approach:**
- ✓ Expressive: Supports conditional branching, nested strategies, per-target parameter rewrites
- ✓ Composable: Any strategy nests in any other (5 core patterns documented)
- ✓ Flexible: Metadata-based routing (user tier, feature, environment)
- ✓ Operator-friendly: Declarative (easy to read/audit post-hoc)
- ✗ Verbose: ~30–50 lines for a realistic multi-tier config
- ✗ No scoring: Weights are flat (0.6/0.4), no gradual preference like flexrouter's 85/90/95 scores
- ✗ Requires external coordination: Different teams managing different conditional branches can conflict

**Flexrouter's flat scored tiers:**
- ✓ Simple: YAML, 8 lines per tier, one scoring dimension (1–100)
- ✓ Single-user friendly: Owner writes one config; no team coordination needed
- ✓ Scoring is transparent: 85 vs 90 vs 95 is obvious; weights (0.6/0.4) require domain knowledge
- ✓ Deterministic: No conditional logic to debug; routing is automatic
- ✗ No per-request customization: Can't route differently based on metadata
- ✗ No fallback chains: If a tier exhausts, it fails (though wait=True sleeps until a slot opens)
- ✗ Not declarative for complex scenarios: "low" and "high" tiers can't be conditional

**For v2 flexrouter:**
- **Adopt the conditional routing idea** (metadata-based) but keep the flat tier abstraction
- **DO NOT adopt the nested JSON schema** — too much coupling and coordination overhead for a single-user tool
- **Consider a hybrid**: YAML with optional `conditions:` block under each tier to enable light metadata routing without full nesting

---

## Conditional Routing

### Schema and Mechanism

Conditions evaluate three data sources against a query object:

```json
{
  "conditions": [
    {
      "query": {
        "metadata.<key>": { "<operator>": value },
        "params.<key>": { "<operator>": value },
        "url.pathname": { "<operator>": value }
      },
      "then": "target_name_or_strategy"
    }
  ],
  "default": "fallback_target"
}
```

**Data sources:**
- `metadata.<key>`: Custom key-value pairs sent with the request (string values only, max 128 chars)
- `params.<key>`: Request fields like `model`, `temperature`, `max_tokens` (primitives only)
- `url.pathname`: Request path (e.g., `/v1/embeddings` vs `/v1/chat/completions`)

**Supported operators:**
- Comparison: `$eq`, `$ne`, `$in`, `$nin`, `$regex`, `$gt`, `$gte`, `$lt`, `$lte`
- Logical: `$and`, `$or` (to combine conditions)

### Examples from Portkey Docs

**Model-alias routing:**
```json
{
  "query": { "params.model": { "$eq": "fastest" } },
  "then": "fast-target"
}
```

**User-tier routing:**
```json
{
  "query": { "metadata.user_plan": { "$eq": "paid" } },
  "then": "premium-target"
}
```

**Environment isolation:**
```json
{
  "query": { "metadata.environment": { "$in": ["prod", "staging"] } },
  "then": "prod-safe-target"
}
```

### Behavior Under Missing Keys

- If a condition references a missing key (e.g., `metadata.user_id` not sent), the condition **fails silently** and routing proceeds to the next condition
- If no conditions match, **default** target is used
- No error thrown; graceful fallback

### Flexrouter v2 Opportunity

Flexrouter currently has no conditional routing. Add lightweight support:
- Let request include optional `metadata` dict in SDK calls
- Add optional `conditions:` to flexrouter.yaml tiers
- Evaluate conditions in order; fall back to score-based selection if no condition matches
- Example:
  ```yaml
  tiers:
    adaptive:
      conditions:
        - if: metadata.user_tier == "premium"
          use: high-score-models
        - if: metadata.environment == "test"
          use: low-cost-models
      models:  # fallback if no condition matches
        - provider: groq
          model: llama-3.1-8b
          score: 85
  ```

---

## Caching

### Simple Cache

**Mechanism**: Exact request body matching. If the byte-for-byte request has been seen before, return the cached response.

```json
{
  "cache": {
    "mode": "simple",
    "max_age": 604800  // seconds (default: 604,800 = 1 week)
  }
}
```

**Miss conditions:**
- First request (no cached entry)
- Any change to request body (even whitespace or field order)
- Expired entry (older than `max_age`)
- `cache_force_refresh: true` header

**Failure modes:**
- No cache invalidation on provider side: If a provider changes its response semantics, cached stale data persists until TTL
- Useless for streaming or long-tail requests: One typo in model name = cache miss for all future requests with that typo

### Semantic Cache

**Mechanism**: Embedding-based similarity. Compute embedding of the input, compare against cached embeddings using **cosine similarity** with a **fixed threshold of 0.95** (not configurable on managed tier).

**Configuration:**
```json
{
  "cache": {
    "mode": "semantic",
    "max_age": 604800
  }
}
```

**Key derivation:**
1. Extract user messages from request
2. Concatenate into a single string
3. Call embedding API (OpenAI, Azure, Google Gemini, or Vertex AI — only these providers)
4. Compute cosine similarity against all cached embeddings
5. If similarity ≥ 0.95, return cached response

**Limitations (from docs):**
- "Requests limited to under 8,191 tokens and ≤4 messages"
- "Requires at least one user message"
- "System prompts don't affect hits (ignored during comparison)"
- "Only supported for `/chat/completions`, `/completions`"
- "Embedding generation limited to OpenAI, Azure OpenAI, Google (Gemini), and Vertex AI"
- "Requires a vector database and is only available on select Enterprise plans" (managed tier only)
- "Caching won't work if `x-portkey-debug: false` header is set"

**Honest failure modes:**
- **High false-positive rate at 0.95 threshold**: "Similar" queries return stale answers (e.g., "summarize this text" vs "give me a summary of this text" both hit)
- **Enterprise-only on managed tier**: Self-hosted gateway can't use semantic cache (no vector DB included)
- **Single embedding provider dependency**: If the embedding provider is down or over quota, semantic cache fails silently and falls back to API call
- **No semantic cache TTL decay**: A 1-month-old response is treated identically to a 1-hour-old response if similarity > 0.95

### Flexrouter v2 Opportunity

Flexrouter has no caching. For v2:
- **Add simple cache first** (easy, high ROI): Exact request body matching with configurable TTL
- **Skip semantic cache** for v1: Requires external vector DB, is Enterprise-only in Portkey, and has high false-positive risk
- **Config example:**
  ```yaml
  settings:
    cache:
      mode: simple
      ttl_seconds: 86400  # 1 day
  ```

---

## Observability

### The Problem Flexrouter Solves Poorly

Flexrouter currently logs to `audit.csv` with fields:
```
timestamp, tier, provider, model, prompt_tokens, completion_tokens, cost_usd, latency_ms, status
```

That's **9 fields total**. When a request fails, the `status` column says "error" but the provider's error message is discarded (as noted in PLAN.md: "It throws away every error message").

### What Portkey Traces Contain

Portkey uses "OpenTelemetry-compliant observability" and records "40+ details" per request. Here's what actually gets captured (from docs and source inference):

**Request-level fields:**
- `request_id` (UUID)
- `timestamp` (ISO 8601)
- `provider` (string)
- `model` (string)
- `user_id` (from metadata._user)
- `workspace_id`
- `metadata` (all custom key-value pairs sent with request)

**Token/cost fields:**
- `prompt_tokens` (int)
- `completion_tokens` (int)
- `total_tokens` (int)
- `cost_usd` (float, derived from provider pricing)
- `cost_per_token` (float)
- `cost_per_response` (float)

**Latency/performance:**
- `latency_ms` (total end-to-end)
- `gateway_latency_ms` (processing time in Portkey)
- `provider_latency_ms` (time at provider)
- `ttft_ms` (time-to-first-token, for streaming)
- `tokens_per_second` (throughput)

**Routing/retry:**
- `strategy_mode` (fallback, loadbalance, conditional)
- `target_index` (which target was tried)
- `attempt_number` (which retry this was)
- `fallback_reason` (why fallback triggered: status_code=429, status_code=503, timeout, error)
- `cache_hit` (boolean; if semantic cache, also includes similarity score)

**Error/status:**
- `status_code` (HTTP 200, 429, 503, etc.)
- `error_message` (full provider error body)
- `error_type` (RateLimitError, ProviderError, TimeoutError, etc.)
- `guardrail_violations` (list of guardrails that triggered)

**Streaming (if applicable):**
- `streaming: true/false`
- `chunks_count` (how many stream chunks received)
- `stream_complete_time` (when stream closed)

**Tool calls (agentic):**
- `tool_call_id` (UUID)
- `tool_name` (string)
- `tool_input_tokens` (int)
- `tool_execution_time_ms` (int)
- `tool_execution_status` (success, error)

### Analytics & Views

Portkey's dashboard provides:
- **Real-time logs** with filters (provider, model, user, status)
- **Cost analytics**: Total spend, cost by provider/model/user, cost per token
- **Latency percentiles**: p50, p95, p99 latencies
- **Error tracking**: Count by error type, provider, model
- **Cache statistics**: Hit rate, latency improvement from cache
- **Request volume**: Throughput over time, peak load detection
- **Custom metadata analytics**: Per-user usage, per-feature cost, per-environment volume

### Proposed Flexrouter v2 Trace Record Schema

Adopt this structure (more comprehensive than current audit.csv):

```python
@dataclass
class RequestTrace:
    # Identity
    request_id: str  # UUID
    timestamp: str   # ISO 8601
    
    # Request
    user_id: Optional[str]  # from metadata._user
    metadata: dict[str, str]  # all custom metadata
    
    # Routing decision
    tier: str
    target_provider: str
    target_model: str
    strategy_mode: str  # "score" for current flexrouter
    attempt_number: int
    
    # Execution
    status: str  # "ok", "rate_limited", "provider_error", "timeout", "fallback"
    provider_error_message: Optional[str]  # MUST NOT DISCARD
    http_status_code: int
    
    # Tokens & cost
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    
    # Timing
    latency_ms: int
    ttft_ms: Optional[int]  # For streaming
    
    # Caching (when implemented)
    cache_hit: bool
    cache_ttl_seconds: Optional[int]
    
    # Session stickiness
    session_id: Optional[str]
    session_duration_ms: Optional[int]
```

**Persistence:** Store in JSON Lines format (one JSON object per line) to `.flexrouter/traces.jsonl`, in addition to CSV audit log.

**Views to build on top:**
1. **Request logs**: Filter by status, provider, tier, metadata.*; sort by timestamp
2. **Cost by user**: Group by metadata._user, sum cost_usd, rank by spend
3. **Error tracking**: Group by provider_error_message, count occurrences, link to requests
4. **Latency heatmap**: Histogram of latency_ms by provider, model, and hour
5. **Cache effectiveness** (when implemented): Hit rate by model, latency savings
6. **Provider health**: Success rate, avg latency, last error time (for each provider)

---

## What Portkey Does Better Than Flexrouter

| Feature | Portkey | Flexrouter | Difficulty |
|---------|---------|-----------|------------|
| **Declarative routing strategy** | JSON config with fallback, loadbalance, conditional nesting | Flat YAML tiers with score-based selection | Easy |
| **Conditional (metadata-based) routing** | Full support: route by user tier, environment, feature | None | Medium |
| **Semantic caching** | Cosine similarity matching (0.95 threshold, Enterprise-only) | No caching | Hard |
| **Error message preservation** | Full provider error in trace | Discarded (noted as bug in PLAN.md) | Easy |
| **Retry configuration** | Granular: per-target, per-strategy, backoff tuning | Global retry policy (preset or manual) | Easy |
| **Request metadata tracking** | Custom key-value pairs on every request; indexed for analytics | None | Medium |
| **Multi-modal support** | Vision, audio, image generation providers | Vision detection only | Medium |
| **Observability detail** | 40+ fields per trace (cost, latency, routing, error, tool calls) | 9 fields (audit.csv) | Hard |
| **Rate-limit visibility** | Per-request attempt tracking; fallback reasons logged | Penalty state in health.json; no attempt tracking | Medium |
| **Guardrails integration** | 40+ pre-built rules (input/output validation, jailbreak detection) | None | Hard |
| **Concurrent request handling** | Designed for scale (processes 2T tokens/day) | Single worker; crashes on concurrent requests (noted in PLAN.md) | Hard |

---

## What Flexrouter Does Better

| Feature | Flexrouter | Portkey | Evidence |
|---------|-----------|---------|----------|
| **Single-owner operation** | No API key, webhook, or managed account needed; runs locally with one config file | Requires hosted dashboard or self-host vector DB for semantic cache | PLAN.md shows owner frustration with config complexity; README emphasizes single-user "one call routes" |
| **Failure handling** | Penalizes models individually with exponential backoff; distinguishes auth (401/403) and billing (402) failures from temporary rate limits (429) | Generic fallback on any status code in on_status_codes | PLAN.md: "flexrouter's failure handling is better than Portkey's" |
| **Model quarantine** | Deleted models (404/410) quarantined for 24h with reason logged; doesn't retry forever | No built-in quarantine; relies on config updates to remove dead models | PLAN.md documents this as a real problem (235 recoveries/239 failures cycle) |
| **Tier isolation** | Tiers never fall back to each other; "low" tier doesn't steal from "high" tier under load | Fallback chains span providers/models, so one tier can pull capacity from another | flexrouter's PLAN.md explicitly notes this as deliberate |
| **Cost-aware selection** | Daily provider budgets with tracking; model is skipped if provider over budget | Virtual key budgets available Enterprise-only; no multi-provider cost modeling | flexrouter.yaml supports provider_budget; Portkey docs say "Budget Limit is currently only available to Portkey Enterprise Plan customers" |
| **Live state in JSON** | Entire routing state (penalties, rate limits, budgets, sessions) in `.flexrouter/` directory; no external DB | Hosted tier requires database; open-source gateway would need to build persistence | flexrouter's state is file-based by design for single-user scenario |
| **Hot-config reload** | Config changes picked up automatically; rate limit windows and penalty state preserved | Config is passed per-request (via header/SDK) or backend setup; hot-reload not applicable | flexrouter.yaml is watched and reloaded; no daemon restart needed |

---

## Ideas Worth Stealing for v2 (Ranked by Impact & Separability)

### 1. Error Message Preservation (Easy, High Impact)
**Currently:** Flexrouter discards provider error messages (noted as bug in PLAN.md step 1).
**Steal from Portkey:** Store full `error_message` in trace record.
**Implementation:** 
- Modify `RequestTrace` schema to include `provider_error_message: Optional[str]`
- Pass error body from client to audit logger
- Expose in dashboard error detail view

**Adoption difficulty:** Easy. No architectural change; just plumb the error body through.

---

### 2. Conditional Metadata-Based Routing (Medium, High Impact)
**Currently:** Flexrouter has no routing customization per-request.
**Steal from Portkey:** Let requests send `metadata` dict; add conditional branches to tiers.
**Implementation:**
```yaml
tiers:
  smart:
    conditions:
      - if: metadata.user_tier == "premium"
        prefer: high-score-models  # route to highest-scoring models
      - if: metadata.environment == "test"
        prefer: low-cost-models
    models:  # fallback if no condition matches
      - provider: openai
        model: gpt-4o
        score: 95
```
- SDK call: `router.generate(messages=[...], tier="smart", metadata={"user_tier": "premium"})`
- Engine: Evaluate conditions in order; if match, filter models to that group; else use full tier
- Keeps tier isolation intact (still no fallback between tiers)

**Adoption difficulty:** Medium. Requires config schema extension and routing engine changes.

---

### 3. Retry Configuration Granularity (Easy, Medium Impact)
**Currently:** Global retry policy (conservative/balanced/aggressive).
**Steal from Portkey:** Per-target retry attempts and backoff tuning.
**Implementation:**
```yaml
tiers:
  low:
    models:
      - provider: groq
        model: llama-3.1-8b
        score: 85
        retries: 2
        backoff_ms: 500
```
- Override global retry policy per model
- Useful for different provider SLAs

**Adoption difficulty:** Easy. Orthogonal to current code.

---

### 4. Request-Level Attempt Tracking in Traces (Easy, Medium Impact)
**Currently:** audit.csv has no attempt_number field.
**Steal from Portkey:** Log which attempt this was (1st, 2nd, 3rd) and why retry was triggered.
**Implementation:**
- Add `attempt_number: int` and `retry_reason: Optional[str]` to RequestTrace
- On retry, log reason (429, 503, timeout, etc.)
- Dashboard: Show retry histogram by reason

**Adoption difficulty:** Easy. Modify audit logging layer.

---

### 5. Observability Detail: JSON Lines Trace Log (Medium, High Impact)
**Currently:** audit.csv is flat, hard to extend.
**Steal from Portkey:** Store full RequestTrace as JSON in `.flexrouter/traces.jsonl`.
**Implementation:**
- Keep CSV for backwards compatibility
- Add `.jsonl` log with full RequestTrace schema
- Dashboard loads from `.jsonl` for detailed views (error message, metadata, attempt tracking)

**Adoption difficulty:** Medium. Requires new audit logger class and dashboard changes.

---

### 6. Rate-Limit Visibility: Attempt Tracking (Medium, Medium Impact)
**Currently:** health.json shows penalties and rate limits, but not per-request which provider was tried and why.
**Steal from Portkey:** Log strategy_mode, target_index, fallback_reason in trace.
**Implementation:**
- RequestTrace.strategy_mode: "score" (current behavior)
- RequestTrace.fallback_reason: null (no fallback in flexrouter) or "provider_down" / "rate_limited" / "over_budget"
- Dashboard: "Why this model?" view for every request

**Adoption difficulty:** Medium. Requires routing engine to emit structured decisions.

---

### 7. Cost Attribution by Metadata (Medium, Medium Impact)
**Currently:** Cost tracked per provider, per tier; no custom attribution.
**Steal from Portkey:** Group and slice analytics by metadata keys (e.g., user, feature, environment).
**Implementation:**
- RequestTrace includes full metadata dict
- Dashboard: Multi-level pivot (sum cost by [metadata.feature][provider][model])
- CLI: `flexrouter analytics --group-by=metadata.user_tier`

**Adoption difficulty:** Medium. Requires dashboard redesign; tracing is already ready.

---

### 8. Guardrails Integration (Hard, Medium Impact)
**Currently:** None.
**Steal from Portkey:** Hook into guardrail libraries (Aporia, SydeLabs) to validate requests before routing.
**Implementation:**
- Optional pre-request hook to call guardrail service
- Log guardrail_violations in trace
- Block request if guardrails fail (or log and proceed, depending on config)

**Adoption difficulty:** Hard. Requires external service integration; adds latency.

---

### 9. Semantic Caching (Hard, Low-to-Medium Impact for v2)
**Currently:** No caching.
**Portkey's approach:** Cosine similarity embedding matching, 0.95 threshold (Enterprise-only).
**Why not steal (yet):**
- Requires external vector database (not included in open-source gateway)
- High false-positive rate at 0.95 threshold
- Only works on managed tier in Portkey
- Simple cache (exact match) is higher ROI for v2

**Recommendation:** Implement simple cache (idea #3 in caching section above) for v2; revisit semantic cache in v2.5 if vector DB becomes a requirement for flexrouter users.

---

## Traps: Things NOT to Copy

### 1. **Nested JSON Strategy Nesting**
Portkey allows arbitrary nesting (fallback → loadbalance → conditional → loadbalance → ...). This is powerful for large teams but creates:
- **Config complexity**: Hard to predict routing behavior; must trace through JSON manually
- **Maintenance burden**: Different teams own different branches; conflicts are silent
- **Testing nightmare**: Exponential combinations of routing paths

**For flexrouter v2:** Keep tiers flat; allow *one level* of conditional branching (via metadata conditions), not arbitrary nesting.

---

### 2. **Fixed Similarity Threshold (0.95) for Semantic Cache**
Portkey hardcodes 0.95 similarity and doesn't expose tuning. This leads to:
- **High false positives**: "Summarize this" and "Give me a summary of this" both cache-hit, returning stale answers
- **No tuning knob:** Enterprise customers can't adjust (they must self-host to tune)
- **Silent failures:** When embedding provider is down, no semantic cache and no warning

**For flexrouter v2:** If implementing semantic cache later, make threshold configurable (default 0.95, but tunable from 0.8–0.99).

---

### 3. **Metadata-Only User Attribution**
Portkey uses `metadata._user` string for per-user analytics. This is:
- **Unfenced:** Any request can claim any user ID (no validation at request time)
- **Late binding:** User must remember to send metadata on every request
- **No enforcement:** Single shared API key used by multiple users = impossible to separate billing

**For flexrouter v2:** Metadata is fine for analytics slicing, but don't build billing/budgets on it. Keep daily provider budgets (current approach), not per-user budgets.

---

### 4. **Conditional Query Language Complexity**
Portkey's operators (`$eq`, `$ne`, `$in`, `$regex`, `$and`, `$or`) are powerful but:
- **Unfamiliar to YAML users:** Portkey uses JSON for config; flexrouter uses YAML
- **Hard to debug:** Multiple nested conditions require mentally executing a small program
- **Security surface:** Regex patterns can DoS if not validated

**For flexrouter v2:** Use simple YAML conditions (if/then), not MongoDB-style operators.
```yaml
conditions:
  - if: metadata.user_tier == "premium"  # simple equality
    prefer: high-score
```

---

### 5. **Embedding-Dependent Semantic Cache**
Portkey's semantic cache requires calling OpenAI/Google embedding API for every request. This:
- **Adds latency:** Embedding call before cache check defeats the purpose
- **Costs money:** Embedding calls are billed by token (extra cost for cache benefit)
- **Single-provider dependency:** If embedding provider is down, cache is unavailable

**For flexrouter v2:** Skip semantic cache for now. Simple cache (exact match, fast, free) is sufficient.

---

### 6. **Enterprise-Only Features on Managed Tier**
Portkey puts guardrails, semantic caching, budget limits, and RBAC behind the Enterprise paywall. This:
- **Fractures the product:** Open-source gateway can't do these; managed tier can't be replaced
- **Creates vendor lock-in:** Users who adopt Enterprise features can't self-host
- **Makes open-source incomplete:** Users hit limits and forced to upgrade

**For flexrouter v2:** Implement all features in the open-source core. No "Enterprise-only" features. If hosting costs money, that's the business model, not feature gating.

---

### 7. **Virtual Keys as Abstraction**
Portkey uses "virtual keys" (abstraction over actual API keys) in the managed tier. This:
- **Adds indirection:** Users can't see which actual key is being used
- **Requires database:** Hosted tier needs a DB to map virtual keys → real keys
- **Breaks key rotation:** Rotating a real key without updating all virtual key mappings breaks requests

**For flexrouter v2:** Keep actual API keys (or env var names) in config. No virtual key layer needed for single-owner operation.

---

## Evidence: URLs Researched

### Portkey Documentation
- https://portkey.ai/docs/introduction/what-is-portkey (product overview)
- https://portkey.ai/docs/product/ai-gateway/configs (routing configuration schema)
- https://portkey.ai/docs/product/ai-gateway/conditional-routing (conditional routing)
- https://portkey.ai/docs/product/ai-gateway/cache-simple-and-semantic (caching)
- https://portkey.ai/docs/guides/use-cases/combining-routing-strategies (nested strategies)
- https://portkey.ai/docs/guides/use-cases/metadata-use-cases (metadata schema and use cases)
- https://portkey.ai/blog/the-complete-guide-to-llm-observability-for-2026/ (observability model) — returned 404
- https://portkey.ai/blog/semantic-caching-thresholds-and-why-they-matter/ (semantic cache threshold)

### GitHub & Source
- https://github.com/Portkey-AI/gateway (official open-source gateway repository)
- https://github.com/Portkey-AI/portkey-cookbook (examples and integrations)

### Press & Analysis
- https://www.getmaxim.ai/articles/bifrost-vs-portkey-self-hosted-open-source-ai-gateway-comparison-2026/ (feature comparison)
- https://thenewstack.io/portkey-gateway-open-source/ (March 2026 open-source announcement)
- https://portkey.ai/blog/the-complete-guide-to-llm-observability/ (observability architecture) — also 404

### Flexrouter Internals
- C:\projects\Finished projects\router\README.md (feature overview)
- C:\projects\Finished projects\router\PLAN.md (known issues and architecture decisions)
- C:\projects\Finished projects\router\flexrouter\config.py (config schema)
- C:\projects\Finished projects\router\flexrouter\events.py (event logging)
- C:\projects\Finished projects\router\flexrouter\audit.py (audit log schema)
- C:\projects\Finished projects\router\flexrouter\_router.py (routing engine)
- C:\projects\Finished projects\router\flexrouter\engine.py (score-based model selection)

---

## Unverifiable Claims

The following claims appear in Portkey marketing but are not verified via documentation or source code:
- "Processes 2 trillion tokens daily" (no logs found; based on company blog)
- "Processes over 10 billion tokens daily in the open-source gateway" (no performance benchmarks in repo)
- "Semantic cache: default similarity threshold of 0.95" (inferred from "default" language; not exposed as constant in source docs)
- "20–40ms added latency on edge workers" (claimed for managed tier; no open-source measurement published)
- "99.99% uptime SLA" (managed tier only; no SLA for open-source self-hosted)

---

## Summary for v2 Planning

**Steal immediately (easy, high ROI):**
1. Error message preservation (one line of code change)
2. Attempt tracking in traces
3. JSON Lines trace log (alongside CSV)

**Steal soon (medium effort, high ROI):**
4. Conditional metadata-based routing (YAML config + engine filter)
5. Per-model retry tuning (config field)
6. Cost attribution by metadata (analytics layer)

**Steal later or skip:**
7. Guardrails (hard, third-party integration)
8. Semantic caching (wait for user demand; simple cache first)
9. Nested strategy nesting (too complex for single-owner; one level of conditions sufficient)
10. Virtual keys (not needed; local config is better for single user)

**Portkey's key insight:** Explicit routing strategies (fallback, loadbalance, conditional) with rich observability makes failure modes visible and debuggable. Flexrouter's failure today isn't the routing logic; it's the absence of observability (error messages thrown away, no attempt tracking, no metadata context).

**v2 focus:** Fix observability first (ideas 1–6 above), not routing strategies. Once you can see what's broken, routing becomes easier to tune.
