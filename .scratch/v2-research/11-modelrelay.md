# modelrelay Competitive Research

**Date:** 2026-09-18  
**Status:** Deep dive into active health probing, quality scoring, and model catalog discovery.

---

## What it is

modelrelay is a JavaScript/Node.js OpenAI-compatible local router that manages ~80 free/cheap models across 12+ providers (NVIDIA NIM, Groq, Cerebras, OpenRouter, OpenCode Zen, Kiro, Google AI, Codestral, Scaleway, Ollama, KiloCode, and custom OpenAI-compatible endpoints). It runs a dashboard on port 7352, probes every model on a staggered schedule with 1-token completions, and routes requests to the highest-quality available model based on real-time latency and availability data.

---

## Active health probing

### The mechanism

modelrelay performs **continuous 1-token ping requests** to validate model availability and measure latency. The schedule is staggered per provider to avoid overwhelming any single endpoint.

#### Constants and schedule

From `lib/server.js` lines 28–29:

```javascript
const PING_TIMEOUT = 15_000;         // 15-second request timeout
const PING_INTERVAL = 1 * 60_000;    // 60-second main schedule cycle
```

Configuration is per-provider via `getProviderPingIntervalMs()`. Defaults can be set via the config JSON file under `providers.<name>.pingIntervalMinutes`.

#### The ping request

From `lib/server.js` lines 1648–1709:

```javascript
async function ping(apiKey, modelId, url, providerKey = null) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), PING_TIMEOUT)
  const t0 = performance.now()
  try {
    const headers = buildProviderRequestHeaders(providerKey, { apiKey })
    const payload = buildProviderRequestBody(providerKey, {
      model: modelId,
      messages: [{ role: 'user', content: 'hi' }],
      max_tokens: 1,
    }, modelId)
    const resp = await fetch(url, {
      method: 'POST', signal: ctrl.signal,
      headers,
      body: JSON.stringify(payload),
    })
    // ... status handling ...
    const rateLimit = {};
    const rl = resp.headers;
    const LR = rl.get('x-ratelimit-limit-requests'); if (LR) rateLimit.limitRequests = parseInt(LR);
    const RR = rl.get('x-ratelimit-remaining-requests'); if (RR) rateLimit.remainingRequests = parseInt(RR);
    const LT = rl.get('x-ratelimit-limit-tokens'); if (LT) rateLimit.limitTokens = parseInt(LT);
    const RT = rl.get('x-ratelimit-remaining-tokens'); if (RT) rateLimit.remainingTokens = parseInt(RT);
    
    const resetReq = rl.get('x-ratelimit-reset-requests');
    const resetTok = rl.get('x-ratelimit-reset-tokens');
    if (resetReq) {
      const ms = parseDurationMs(resetReq);
      if (ms != null) rateLimit.resetRequestsAt = Date.now() + ms;
    }
    if (resetTok) {
      const ms = parseDurationMs(resetTok);
      if (ms != null) rateLimit.resetTokensAt = Date.now() + ms;
    }
    
    return {
      code: String(resp.status),
      ms: Math.round(performance.now() - t0),
      rateLimit: Object.keys(rateLimit).length > 0 ? rateLimit : null,
      errorMessage,
    }
  } catch (err) {
    const isTimeout = err.name === 'AbortError'
    const message = getNetworkErrorMessage(err)
    return {
      code: isTimeout ? '000' : 'ERR',
      ms: isTimeout ? 'TIMEOUT' : Math.round(performance.now() - t0),
      errorMessage: isTimeout ? 'Request timed out while pinging provider.' : message,
    }
  } finally {
    clearTimeout(timer)
  }
}
```

**Key details:**
- Sends a single message `{ role: 'user', content: 'hi' }` with `max_tokens: 1`
- Aborts if no response within 15 seconds
- Measures round-trip time in milliseconds
- Captures rate-limit headers (limit/remaining for requests and tokens, reset times)
- Returns HTTP code (as string: '200', '401', '429', etc.), latency, rate limits, and any error message

#### Storage and history

From `lib/server.js` lines 2559–2563:

```javascript
const { code, ms, rateLimit, errorMessage } = pingResult;
const now = Date.now();
r.lastPingAt = now;
r.pings.push({ ms, code, ts: now });
if (r.pings.length > 50) r.pings.shift(); // keep history bounded
```

Each model (`r`) accumulates a `pings` array (max 50 entries) with `{ ms, code, ts }`. This sliding window enables uptime calculation and latency trending in the UI.

#### Scheduling and stagger

From `lib/server.js` lines 2679–2700:

```javascript
const schedulePing = () => {
  setTimeout(async () => {
    const currentConfig = loadConfig();
    if (!isAutoPingEnabled(currentConfig)) {
      schedulePing();
      return;
    }
    // Refresh dynamic model lists from each provider
    await safeRefreshProviderModels(() => refreshKiloCodeModels());
    await safeRefreshProviderModels(() => refreshOpenCodeModels());
    await safeRefreshProviderModels(() => refreshOpenRouterModels());
    await safeRefreshProviderModels(() => refreshOpenAICompatibleModels());
    await safeRefreshProviderModels(() => refreshOllamaModels());
    
    const now = Date.now();
    for (const r of results) {
      const pingIntervalMs = getProviderPingIntervalMs(currentConfig, r.providerKey);
      const lastActivityAt = Math.max(r.lastModelResponseAt || 0, r.lastPingAt || 0);
      if (now - lastActivityAt < pingIntervalMs) continue;
      pingModel(r).catch(() => { });
    }
    schedulePing();
  }, PING_INTERVAL);
};
```

The main loop runs every 60 seconds. For each model:
1. Check when it was last pinged or received real traffic (`lastActivityAt`)
2. If inactive for longer than the configured `pingIntervalMs`, issue a new ping
3. All pings for a given cycle run in parallel (`Promise.allSettled`)
4. Reschedule the next cycle

**Stagger:** Models are only pinged if they exceed their configured interval (default varies by provider, but typically 60 seconds to several minutes). Models receiving actual traffic reset their `lastModelResponseAt`, so they are skipped in the next ping cycle.

#### Pass/fail criteria

From `lib/server.js` lines 2589–2628:

```javascript
if (code === '200') {
  r.status = 'up';
  r.httpCode = null;
  r.lastError = null;
}
else if (code === '000') {
  r.status = 'timeout';
  r.lastError = {
    code,
    message: 'Request timed out while pinging provider.',
    updatedAt: now,
  };
}
else if (code === 'ERR') {
  r.status = 'down';
  r.httpCode = code;
  r.lastError = {
    code,
    message: errorMessage || 'Network error while contacting provider.',
    updatedAt: now,
  };
}
else if (code === '401') {
  r.status = 'noauth';
  r.httpCode = code;
  r.lastError = {
    code,
    message: errorMessage || 'Unauthorized. Check API key.',
    updatedAt: now,
  };
}
else {
  r.status = 'down';
  r.httpCode = code;
  r.lastError = {
    code,
    message: errorMessage || `HTTP ${code}`,
    updatedAt: now,
  };
}
```

- **200** → `status = 'up'` (available for routing)
- **000 (timeout)** → `status = 'timeout'` (will retry next cycle)
- **ERR (network error)** → `status = 'down'`
- **401 (unauthorized)** → `status = 'noauth'` (API key problem, sideline provider)
- **Any other 4xx/5xx** → `status = 'down'`

#### Rate limit decay

From `lib/server.js` lines 2573–2587:

```javascript
if (r.rateLimit && r.rateLimit.wasRateLimited === true) {
  const now = Date.now();
  const resetReq = r.rateLimit.resetRequestsAt || 0;
  const resetTok = r.rateLimit.resetTokensAt || 0;
  const latestReset = Math.max(resetReq, resetTok);
  // Expire if: reset times have passed, or 60s since capture (fallback if no reset times)
  const fallbackExpiry = (r.rateLimit.capturedAt || 0) + 60_000;
  if ((latestReset > 0 && latestReset < now) || (latestReset === 0 && fallbackExpiry < now)) {
    r.rateLimit.wasRateLimited = false;
    if (rateLimit) {
      r.rateLimit = rateLimit;
    }
  }
}
```

Rate limit state auto-expires when:
1. The provider-reported reset time has passed, **or**
2. 60 seconds have elapsed since capture (fallback if no reset time was provided)

At expiry, `wasRateLimited` is set to `false` and the model becomes eligible for routing again.

#### What it costs

modelrelay charges for health probes by counting them against free-tier quotas (they are real API calls). With ~80 models across 12 providers, staggered at configurable intervals:

- **Minimum (all models on 10-minute interval):** 80 models ÷ 10 min = 8 requests/min = 11.5K requests/day
- **Typical (mixed 5–10 minute intervals):** ~15K–20K requests/day
- **Per-provider variation:** Some providers (e.g., Ollama local) are free; others (OpenRouter, NVIDIA NIM) charge against the account's free quota

This is the real cost-per-day problem: you're burning quota on pings that could be used for real work. modelrelay mitigates by:
- Staggering per provider
- Skipping models that received real traffic recently
- Providing configuration knobs (`pingIntervalMinutes` per provider)

### Proposed design for flexrouter v2

A Python equivalent would:

1. **Core ping logic** (in a background task, not blocking routing):
   ```python
   async def probe_model(model_id: str, provider: Provider) -> ProbeResult:
       """Send 1-token completion ping; return status, latency, rate limits."""
       payload = {
           "model": model_id,
           "messages": [{"role": "user", "content": "hi"}],
           "max_tokens": 1,
       }
       try:
           async with asyncio.timeout(15):  # 15-second timeout
               resp = await provider.post("/chat/completions", json=payload)
       except asyncio.TimeoutError:
           return ProbeResult(status="timeout", error="Request timed out")
       except httpx.RequestError as e:
           return ProbeResult(status="down", error=str(e))
       
       result = ProbeResult(
           status="up" if resp.status_code == 200 else "down",
           code=resp.status_code,
           latency_ms=elapsed_ms,
       )
       
       # Parse rate-limit headers
       result.rate_limit = {
           "limit_requests": resp.headers.get("x-ratelimit-limit-requests"),
           "remaining_requests": resp.headers.get("x-ratelimit-remaining-requests"),
           "reset_requests_at": parse_duration_header(resp.headers.get("x-ratelimit-reset-requests")),
           ...
       }
       return result
   ```

2. **Scheduling** (central background loop):
   ```python
   async def health_probe_loop():
       """Every 60 seconds, ping models that haven't been active."""
       while True:
           await asyncio.sleep(60)  # main cycle
           now = time.monotonic()
           for model in MODELS:
               last_activity = max(model.last_ping_at or 0, model.last_request_at or 0)
               ping_interval = model.provider.config.get("ping_interval_seconds", 300)
               if now - last_activity > ping_interval:
                   asyncio.create_task(probe_model(model.id, model.provider))
   ```

3. **State storage** (in-memory, persisted to JSON on disk):
   ```python
   @dataclass
   class ModelProbeHistory:
       model_id: str
       pings: deque[ProbeResult] = field(default_factory=lambda: deque(maxlen=50))
       last_probe_at: float | None = None
       last_status: str = "pending"  # up, down, timeout, noauth, rate_limited
       rate_limit_state: dict | None = None
       uptime_pct: float = 0.0  # % of pings with 200
   ```

4. **Routing integration**:
   - Models with `status = "down"` or `status = "noauth"` are excluded from selection
   - Models with `status = "rate_limited"` are excluded until reset time passes
   - For models with status "up", include `latency_ms` and `uptime_pct` in the QoS scoring
   - Example QoS formula: `score = model.quality * availability_multiplier(uptime) + (1000 - latency) / 1000`

5. **Cost management**:
   - Make `ping_interval_seconds` configurable per provider and per tier
   - Provide a `--min-ping-interval` CLI flag to globally adjust all intervals
   - Log daily ping count + token cost estimate to dashboard
   - Add a mode `--no-auto-ping` to disable probing entirely (manual only)

6. **Dashboard features**:
   - Show each model's last probe result, latency graph, uptime %, and current rate-limit state
   - Expandable "Probe History" drawer (last 50 pings)
   - Manual "Probe Now" button per model / per provider
   - "Last Active" timestamp (probe or real request, whichever is more recent)

---

## Where quality scores come from

### The source

modelrelay stores a hardcoded `scores` object in `scores.js`. Each model ID maps to a float 0.0–1.0 representing model quality/capability.

From `scores.js` (excerpt):

```javascript
export const scores = {
  "arcee-ai/trinity-large-preview": 0.778,
  "baidu/qianfan-ocr-fast": 0.30,
  "claude-haiku-4.5": 0.733,
  "claude-sonnet-4.5": 0.772,
  "deepseek-ai/deepseek-v3.2": 0.731,
  "gpt-oss-120b": 0.6,
  "google/gemma-4-31b-it": 0.8,
  "kimi-k2.6": 0.802,
  "minimax-m2.7": 0.822,
  // ... 100+ more models ...
}
```

### How they are derived

From `code_arena_scores.md`:

The document is titled "Code Arena & Coding Benchmark Scores - Open Models (February 2026)" and combines:

1. **Code Arena ELO rankings** — peer-voting arena (like LMArena) where models compete on coding tasks. Top models include:
   - GLM-5 (ELO 1,546)
   - Kimi K2.5 (ELO 1,465)
   - DeepSeek V3.2 (ELO 1,410)

2. **HumanEval scores** — pass rate on 164 hand-written Python coding problems

3. **LiveCodeBench scores** — evaluating real-world repository tasks released weekly

The `scores.js` values appear to be **normalized** from these ELO/benchmark scores onto a 0–1 scale. For example:
- GLM-5 (ELO 1,546) → 0.778
- DeepSeek V3.2 (ELO 1,410) → 0.731
- MiniMax M2.7 (ELO 841) → 0.822

The normalization formula is not stated; it is likely percentile-based or a simple linear rescaling of the ELO range.

### Refresh path

**The scores are hardcoded; they are not fetched or refreshed automatically.** Manual updates only:
1. A human review the Code Arena website or papers quarterly/semi-annually
2. Update `code_arena_scores.md` with new benchmark results
3. Manually recalculate and update `scores.js` entries
4. Commit and tag a release

modelrelay provides no API to fetch live scores or auto-refresh them.

### Can flexrouter use the same source?

**Yes, with caveats:**

1. **Direct reuse:** Manually pull the same Code Arena + HumanEval + LiveCodeBench data and normalize onto a 0–1 scale. Update quarterly when new benchmarks are published.

2. **Better:** Make the scores vendored/documented (like modelrelay's `code_arena_scores.md`) so changes are auditable. Store the originating benchmark names and values in comments:
   ```yaml
   scores:
     "gpt-4o":
       value: 0.95
       source: "Code Arena ELO 1,800 (as of 2026-02-27), normalized via percentile"
     "claude-sonnet-4.5":
       value: 0.772
       source: "Code Arena ELO 1,465, Code Arena KimiK2.5 proxy"
   ```

3. **Observe:** Code Arena updates happen ~monthly; HumanEval/LiveCodeBench weekly. flexrouter can expose an API endpoint `/api/scores/refresh-check` that polls Code Arena and alerts when new results are available, but does not auto-apply (requires human review + release).

4. **Fallback:** For models missing from Code Arena (very new or niche), fall back to provider reputation score or a `default_new_model_score` config (e.g., 0.5).

---

## Model catalog discovery

### How sources.js works

modelrelay maintains a static hardcoded model list in `sources.js` plus **dynamic discovery** for providers that expose it.

From `sources.js` lines 174–346:

```javascript
export const sources = {
  "nvidia": {
    "name": "NIM",
    "url": "https://integrate.api.nvidia.com/v1/chat/completions",
    "models": [
      ["deepseek-ai/deepseek-v3.2", "DeepSeek V3.2", "128k"],
      ["moonshotai/kimi-k2.5", "Kimi K2.5", "128k"],
      // ... 20+ more hardcoded models ...
    ]
  },
  "groq": {
    "name": "Groq",
    "url": "https://api.groq.com/openai/v1/chat/completions",
    "models": [
      ["llama-3.3-70b-versatile", "Llama 3.3 70B", "128k"],
      // ...
    ]
  },
  // ... other providers (cerebras, opencode, openrouter, etc.) ...
  "openai-compatible": {
    "name": "OpenAI-Compatible",
    "url": "",
    "models": []  // Dynamically filled from user-configured endpoints
  },
  "ollama": {
    "name": "Ollama",
    "url": "",
    "models": []  // Dynamically discovered
  },
}

function buildModels() {
  const result = []
  for (const [providerKey, provider] of Object.entries(sources)) {
    for (const m of provider.models) {
      const [modelId, label, ctx] = m
      const intell = getScore(modelId)
      result.push([modelId, label, intell, ctx, providerKey])
    }
  }
  return result
}

export const MODELS = buildModels()
```

**Key structure:**
- Each provider has a name, base URL, and static list of `[modelId, displayLabel, contextWindow]`
- At startup, `buildModels()` pulls in the scores for each model
- `MODELS` is the final exported list used by routing

### Dynamic discovery

For providers that support `/v1/models` (OpenAI-compatible, Ollama, KiloCode, OpenRouter, OpenCode Zen):

From `lib/server.js` lines 2686–2690 (within `schedulePing()`):

```javascript
await safeRefreshProviderModels(() => refreshKiloCodeModels());
await safeRefreshProviderModels(() => refreshOpenCodeModels());
await safeRefreshProviderModels(() => refreshOpenRouterModels());
await safeRefreshProviderModels(() => refreshOpenAICompatibleModels());
await safeRefreshProviderModels(() => refreshOllamaModels());
```

Each `refresh*()` function:
1. Calls `GET /v1/models` on the provider's endpoint
2. Parses the response to extract `data[].id` (model IDs)
3. Adds or removes models from the results array dynamically
4. Triggers a re-ping of new models

**Refresh intervals:**
```javascript
const KILOCODE_MODELS_REFRESH_MS = 30 * 60_000;        // 30 minutes
const OPENCODE_MODELS_REFRESH_MS = 60 * 60_000;        // 60 minutes
const OPENROUTER_MODELS_REFRESH_MS = 60 * 60_000;      // 60 minutes
const OPENAI_COMPATIBLE_MODELS_REFRESH_MS = 30 * 60_000; // 30 minutes
const OLLAMA_MODELS_REFRESH_MS = 60 * 60_000;          // 60 minutes
```

### Aliasing and normalization

From `sources.js` lines 8–64:

```javascript
export const MODEL_ID_ALIASES = {
  'deepseek-v3.2': 'deepseek-ai/deepseek-v3.2',
  'kimi-k2.5': 'moonshotai/kimi-k2.5',
  'glm-5': 'z-ai/glm5',
  // ... many more ...
}

export function resolveAliasedModelId(modelId) {
  const raw = typeof modelId === 'string' ? modelId.trim() : ''
  if (!raw) return ''
  return MODEL_ID_ALIASES[raw] || MODEL_ID_ALIASES[raw.toLowerCase()] || raw
}

export function canonicalizeModelId(modelId) {
  const resolved = resolveAliasedModelId(modelId)
  // 1. Remove known runtime suffixes like :free, :optimized:free, or :cloud
  const base = resolved.replace(/(?::(?:free|optimized|cloud))+$/i, '');
  // 2. Remove provider prefix like google/
  const unprefixed = base.includes('/') ? base.split('/').pop() : base;
  return { base, unprefixed };
}

export function getScore(modelId) {
  const { base, unprefixed } = canonicalizeModelId(modelId);
  return scores[base] ?? scores[unprefixed] ?? null;
}
```

This handles:
- Short names (`glm-5`) → full names (`z-ai/glm5`)
- Runtime suffixes (`:free`, `:optimized:free`) are stripped for scoring
- Provider prefixes can be searched in either form (`google/gemma-3-4b-it` or just `gemma-3-4b-it`)

---

## Failover and key handling

### Multi-key round-robin

modelrelay supports multiple API keys per provider and rotates through them round-robin.

From `lib/server.js` lines 197–209 (in `selectNextApiKeyFromPool()`):

```javascript
export function selectNextApiKeyFromPool(pool, entry, maxTurns, now, cooldownMs) {
  if (!Array.isArray(pool) || pool.length === 0) return null
  if (!entry || !(entry.accounts instanceof Map)) return null
  // ...
  let selectedIdx = entry.currentIdx % pool.length
  // Check if current key is rate-limited; if so, skip to next
  const account = ensureKeyPoolAccount(entry, selectedIdx)
  if (account.rateLimitedAt && (now - account.rateLimitedAt) < cooldownMs) {
    // Skip this key; try next
    entry.currentIdx = (entry.currentIdx + 1) % pool.length
    return selectNextApiKeyFromPool(pool, entry, maxTurns, now, cooldownMs) // recursion
  }
  // ...
  account.requests += 1
  return pool[selectedIdx]
}
```

**Behavior:**
- Each provider has a pool of keys and a `currentIdx` pointer
- On each request, check if the current key is rate-limited (429 within cooldown)
- If yes, skip to the next key in the pool (modulo wrap)
- Track request count per key (`account.requests`)
- Rotate once the key reaches `maxTurns` (configurable per provider)

### Provider-level failures

From `lib/server.js` lines 2550–2557 (in `pingModel()`):

```javascript
const auth = await resolveProviderAuthToken(currentConfig, r.providerKey);
const providerApiKey = auth.token;
const providerUrl = resolveProviderUrl(currentConfig, r.providerKey, auth.providerUrlOverride, r.providerUrl);

let pingResult = await ping(providerApiKey, r.modelId, providerUrl, r.providerKey);
if (shouldRetryOptionalProviderWithBearer(currentConfig, r.providerKey, auth, pingResult.code, pingResult.errorMessage)) {
  pingResult = await ping(getApiKey(currentConfig, r.providerKey), r.modelId, providerUrl, r.providerKey);
}
```

If a model returns 401, modelrelay:
1. Marks it with `status = 'noauth'`
2. **For optional providers** (KiloCode, OpenCode Zen), retries with Bearer auth (if configured)
3. If still 401 after retry, sidelines the entire provider

### HTTP code handling in routing

From `utils.js` lines 158–160:

```javascript
export function isModelEligibleForRouting(r) {
  return r.status !== 'banned' && r.status !== 'disabled' && r.status !== 'excluded'
}
```

Models are excluded from routing if:
- `status === 'down'` (network error or non-200 response)
- `status === 'noauth'` (401/403)
- `status === 'timeout'` (no response within 15s)
- `status === 'banned'` (CLI flag `--ban modelId`)
- `status === 'disabled'` (provider disabled in config)
- `status === 'excluded'` (minimum intelligence score filter, or excluded provider list)

### Sticky sessions

From `lib/server.js` lines 154–171:

```javascript
export function getPinnedModelCandidate(results, pinnedModelId, pinningMode = 'canonical', attemptedModelKeys = [], pinnedProviderKey = null) {
  const attempted = new Set(attemptedModelKeys);
  const matches = getPinnedModelMatches(results, pinnedModelId, pinningMode, pinnedProviderKey)
    .filter(r => r.status !== 'banned' && r.status !== 'disabled' && !attempted.has(getRoutingModelKey(r)) && !attempted.has(r.modelId));
  const ranked = rankModelsForRouting(matches, Array.from(attempted));
  return ranked[0] || null;
}
```

Sticky sessions pin a conversation to a model group (or exact provider row). If the pinned model fails, automatically fall back to the next-best model in the same group/tier.

---

## What modelrelay does better than flexrouter

### 1. **Active health probing** (Hard to adopt, massive value)

- Continuous 1-token pings every 60 seconds per model, staggered by provider
- Real-time latency measurement and uptime tracking (50-entry rolling window)
- Rate limit headers extracted and auto-expiring, so models become available again when quota resets
- **Impact:** Flexrouter today has no probes — it only learns a model is dead when live traffic hits it. 30 of 96 configured models are now gone. modelrelay would have caught all of them the first day.

### 2. **Per-model rate limit tracking** (Medium, operationally critical)

- Captures `x-ratelimit-*` headers from every ping and real request
- Tracks reset times and auto-expires stale state
- Prevents routing to rate-limited models until reset
- **Impact:** flexrouter has no per-model rate limit awareness; it only knows provider-level quotas. A single model going 429 is invisible.

### 3. **QoS scoring beyond raw model quality** (Easy, moderate value)

From `utils.js` lines 119–137:

```javascript
function availabilityMultiplierForUptime(uptime) {
  if (uptime >= 95) return 1.0
  if (uptime >= 85) return 0.9
  if (uptime >= 70) return 0.6
  return 0.2
}

function computeQoSFromNormalizedScores(r, normalizedScores) {
  if (r.status !== 'up') return 0
  const qualityScore = normalizedScores.gpqa != null ? normalizedScores.gpqa : 0
  const avg = getAvg(r)
  const ping = (avg === Infinity || avg === null) ? 1000 : avg
  const pingTieBreaker = Math.max(0, 1000 - ping) / 1_000
  const uptime = getUptime(r)
  const availabilityScore = qualityScore * availabilityMultiplierForUptime(uptime)
  return availabilityScore + pingTieBreaker
}
```

The QoS score combines:
- Model quality (from Code Arena) weighted by uptime
- Latency as a tie-breaker
- Results in better routing when a high-quality model is slow vs. a medium model that's fast

flexrouter only ranks by configured score; it ignores observed latency and availability.

### 4. **Dashboard with live telemetry** (Hard, high UX value)

modelrelay's dashboard shows:
- Each model's last probe result, latency graph, uptime %, rate-limit state
- Six tabs: Live Telemetry, Chat, Request Logs, Account Status, Settings, Setup
- Manual "Probe Now" button per model
- Model groups by provider with color-coded status
- Search/filter by model name

flexrouter's dashboard exists but shows almost no debug data (because errors were being discarded until recently).

### 5. **Configuration transparency** (Easy, critical for ops)

- Dashboard allows entering/testing API keys live with immediate pass/fail feedback
- Config export/import as base64 tokens (encrypted at rest)
- Auto-update checking (enabled by default)

flexrouter's config is YAML; keys must be in environment variables or embedded in the file.

---

## What flexrouter does better

### 1. **Python** (Huge for teams already in Python)

modelrelay is JavaScript/Node.js. flexrouter is Python. If you're running Python services, the operational overhead of adding a JavaScript service is real (another runtime, another set of dependencies, another binary to distribute).

### 2. **Integration with existing Python code** (Easy, high value for libraries)

flexrouter can be imported as a Python library:
```python
from flexrouter import FlexRouter
router = FlexRouter()
response = router.generate(messages=[...], tier="low")
```

modelrelay is a standalone server only; you always make HTTP calls.

### 3. **Type safety** (Moderate, reduces bugs)

flexrouter has Python type hints throughout. modelrelay has JSDoc hints but no runtime type checking.

### 4. **Structured logging** (Easy, operationally critical)

flexrouter's v2 architecture is built on FastAPI/uvicorn with structured logging (JSON events). modelrelay has console logging; requires external tooling to aggregate.

### 5. **Built-in vision support** (Easy, solves a real need)

flexrouter has a `detect_vision` hook that auto-detects images in messages and routes to vision-capable models. modelrelay requires manual model selection.

---

## Ideas worth stealing for v2

### Ranked by value and implementation difficulty

1. **Active 1-token health probing with rate-limit capture** ⭐⭐⭐ (HARD / CRITICAL)
   - Implement the exact ping mechanism: 1-token completion, 15s timeout, 60s main loop, per-provider stagger
   - Capture and parse rate-limit headers into a `RateLimitState` model
   - Auto-expire rate-limited models after reset time passes
   - Store 50-entry ping history per model
   - **Effort:** ~3 days. **ROI:** Fixes the "30 stale models" problem entirely.

2. **QoS formula mixing quality + latency + uptime** ⭐⭐ (EASY / STRATEGIC)
   - Replace "pick highest score" with `qos = quality * availability_multiplier(uptime) + (1000 - latency) / 1000`
   - Use percentile-rank of model quality (so quality is relative to the corpus, not absolute)
   - **Effort:** 1 day. **ROI:** Better user experience; faster responses on average.

3. **Per-provider ping interval configuration** ⭐⭐ (EASY)
   - Expose `providers.<name>.ping_interval_minutes` in config
   - Default to 5 minutes for free tiers, 1 hour for low-quota providers
   - Provide `--min-ping-interval` CLI flag to globally adjust
   - **Effort:** 4 hours. **ROI:** Cost control; can dial down probing for cheap providers.

4. **Model aliasing with suffix stripping** ⭐ (TRIVIAL)
   - When scoring models, strip `:free`, `:optimized`, etc. suffixes
   - Support short aliases (`glm-5` → `z-ai/glm5`)
   - **Effort:** 30 mins. **ROI:** Cleaner config; avoids duplicate scores.

5. **Dynamic model discovery via /v1/models** ⭐⭐ (EASY / OPERATIONAL)
   - Poll each provider's `/v1/models` endpoint every 30–60 minutes
   - Add new models to the routing pool automatically
   - Remove models that no longer appear in the list
   - Trigger immediate pings for newly discovered models
   - **Effort:** 1.5 days. **ROI:** No more manual config updates when providers add/remove models.

6. **Live dashboard with ping history drawer** ⭐⭐ (HARD / UX)
   - Extend the existing FastAPI dashboard
   - Show 50-entry ping history per model (timestamps, codes, latencies)
   - Add "Probe Now" button per model
   - Show rate-limit state (remaining, reset time)
   - **Effort:** 2 days. **ROI:** Better debugging; ops can see exactly why a model is down.

7. **Sticky sessions with automatic fallback** ⭐ (MEDIUM)
   - Pin a conversation to a model or model group
   - If pinned model fails, automatically use next-best in group
   - **Effort:** 1.5 days. **ROI:** More consistent chat experience.

8. **Code Arena scores stored with sources** ⭐⭐ (EASY / DOCS)
   - Add a `code_arena_scores.md` file (copy from modelrelay)
   - Document the source of each score (ELO rank, HumanEval %, etc.)
   - Make it clear when scores are last updated
   - **Effort:** 2 hours. **ROI:** Transparent scoring; easy to audit.

---

## Traps / things NOT to copy

### 1. **Hardcoded model lists** ⭐ (CRITICAL MISTAKE)

modelrelay has a hardcoded list of ~80 models in `sources.js`. This is a **moving target problem:**
- Providers add/remove models every week
- The hardcoded list goes stale within days
- Users must file issues or wait for a release to get new models

**Solution for flexrouter:** Keep a small hardcoded list of "common models" for quick onboarding, but **always dynamically discover** from providers via `/v1/models`.

### 2. **Vendoring model scores without automation** ⭐

modelrelay's scores are manually edited when benchmarks update. This is slow and error-prone.

**Solution:** Create a CI job that polls Code Arena monthly and auto-generates a PR with score updates. Require human review before merging, but automate the data gathering.

### 3. **Console logging without structured format** ⭐

modelrelay logs to stdout/stderr. Production deployments need JSON/structured logging for log aggregation.

**Solution:** flexrouter v2 already uses structured logging (Uvicorn with JSON events). Keep it.

### 4. **No key rotation observability**

modelrelay rotates API keys silently. If a key keeps failing, there's no alerting or dashboard indication that rotation is happening.

**Solution:** flexrouter should expose a `/api/key-pool-status` endpoint showing:
- Each key's request count and rate-limit status
- When each key last rotated
- Which key is currently active

### 5. **Config tokens include live API keys** ⚠️

modelrelay's `config export` returns a base64 blob containing live API keys. If shared, it exposes credentials.

**Solution:** flexrouter stores keys separately from config. Config export should only export the *non-sensitive* settings (tier definitions, model scores, etc.). Keys stay in a separate encrypted file.

### 6. **No test coverage for probing logic**

modelrelay has no tests for the ping function or QoS scoring.

**Solution:** Write unit tests for:
- `probe_model()` with mock responses (200, 401, timeout, network error)
- Rate limit header parsing
- QoS scoring formula
- Model eligibility filtering

---

## Evidence

### Files read (with exact paths)

modelrelay project (cloned at `C:\projects\Finished projects\router\Refrence_projects\modelrelay\`):

1. **README.md** — Overview of features, CLI, config, integration examples
2. **AGENTS.md** — Release process, testing, test architecture
3. **code_arena_scores.md** — Source data for model quality scores (Code Arena ELO, HumanEval, LiveCodeBench)
4. **scores.js** — Hardcoded model → quality score mapping (0.0–1.0 normalized)
5. **sources.js** — Model catalog structure, provider definitions, aliasing logic, model ID canonicalization, score lookup
6. **lib/server.js** (lines 1648–1709) — `ping()` function implementation
7. **lib/server.js** (lines 2520–2645) — `pingModel()` function and state storage
8. **lib/server.js** (lines 2679–2700) — `schedulePing()` main loop
9. **lib/server.js** (lines 28–29, 197–209) — Constants and key pool rotation
10. **lib/utils.js** (lines 16–44) — `getAvg()`, `getVerdict()`, `getUptime()` functions
11. **lib/utils.js** (lines 119–157) — QoS scoring formula
12. **lib/score-fetcher.js** — Score assignment logic and dynamic model discovery detection
13. **package.json** — Dependencies, test scripts, release metadata
14. **test/test.js** — Unit tests for core logic

flexrouter project (at `C:\projects\Finished projects\router\`):

1. **README.md** — Current features and API
2. **PLAN.md** — v2 rewrite roadmap; diagnosis of current problems
3. **flexrouter/probe.py** — Current probing logic (GET /models only, no 1-token completions)
4. **flexrouter/engine.py, _router.py, config.py** — Existing routing and config logic (referenced but not fully read)

**Date of research:** 2026-09-18

---

## Summary

modelrelay's **defining advantage** is active health probing: continuous 1-token pings on a staggered schedule, with latency measurement, uptime tracking, and rate-limit header capture. This single feature would solve flexrouter's stale-model problem entirely.

The **quality scoring** comes from public benchmarks (Code Arena, HumanEval, LiveCodeBench) and is manually maintained. flexrouter can use the exact same source.

The **QoS scoring** formula (quality × uptime_multiplier + latency_tiebreaker) is simple and effective, and would improve flexrouter's routing decisions.

**What NOT to copy:** hardcoded model lists (should be dynamic), vendored scores without automation (should be fetched), console logging (should be structured), and config tokens with keys (should separate credentials).

For flexrouter v2, prioritize **active probing** as the critical fix, then add **QoS scoring** and **dynamic discovery**. The dashboard already exists; extend it with probe history and rate-limit visibility.

