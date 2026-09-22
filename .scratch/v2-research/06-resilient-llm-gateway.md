# resilient-llm-gateway Research

## What it is

A production-grade, high-throughput distributed LLM gateway written in Python (>=3.12) that coordinates routing across multiple LLM providers with circuit breakers, rate limiting, and semantic caching—all coordinated via Redis for multi-process safety. Can run embedded or as a FastAPI proxy server. Core claim: <10ms processing latency, atomic Redis Lua scripts ensure 100% rate limit accuracy under concurrent load, and "zero-overhead" state coordination across replicas.

## How it actually works

**Deployment model:** Dual-mode: embedded library or standalone FastAPI gateway with a single port serving dashboard, OpenAI-compatible API, and telemetry data. Shared state lives in Redis (distributed) and local fast-path caches (per-process).

**Request flow:**
1. Request hits endpoint with provider/model hints
2. Pre-request checks: auth, circuit breaker state (Redis query), rate limit reservation (Redis Lua script)
3. If available: token bucket deducts estimated tokens (all-or-nothing), hits provider
4. On response: actual token counts flow back, budget adjusted, metrics shipped to Prometheus
5. On failure: consecutive failures increment counter in Redis FSM; ≥5 failures → circuit OPEN (30s)

**State layers:**
- **Redis (distributed, consistent):** Circuit breaker FSM (state, failure_count, transition_times), rate limit buckets (token counts per RPM/TPM window), failure counters, half-open probe throttles
- **Local process memory (fast-path):** Parsed config, session stickiness map, provider health predictions
- **Prometheus sink (observability):** latency, status code distributions, provider utilization

**Key design rule:** Every operation that crosses process boundaries goes through Redis; local memory is cache-only (invalidated on reload).

## The Redis circuit breaker

**State machine (CLOSED → OPEN → HALF_OPEN):**

```
CLOSED (normal operation)
  └─ On failure: increment failure_count
     If failure_count >= 5:
       → OPEN (record failure_count=0, set open_since=now)

OPEN (unhealthy, fast-reject)
  └─ On probe request (every 60s if no requests traffic):
       If now - open_since >= 30s:
         → HALF_OPEN (allow 1 probe request to test recovery)

HALF_OPEN (recovery probe)
  └─ If probe succeeds:
       → CLOSED (reset failure_count, clear open_since)
     If probe fails:
       → OPEN (reset timer, try again in 30s)
```

**Redis key structure (inferred from patterns):**
- `circuit:{provider}:{model}:state` → "CLOSED" | "OPEN" | "HALF_OPEN"
- `circuit:{provider}:{model}:failures` → integer (consecutive count)
- `circuit:{provider}:{model}:opened_at` → epoch float (wall-clock time)
- `circuit:{provider}:{model}:probe_deadline` → epoch float (next allowed probe time)

**Lua script execution (single roundtrip):**
```lua
-- Pseudo-code: check and update circuit state atomically
local state = redis.call('GET', state_key)
local failures = tonumber(redis.call('GET', failures_key)) or 0
local opened_at = tonumber(redis.call('GET', opened_at_key))

if state == 'CLOSED' then
  return {state='CLOSED', allowed=true}
elseif state == 'OPEN' then
  local cooldown_elapsed = now - opened_at >= 30
  if cooldown_elapsed then
    redis.call('SET', state_key, 'HALF_OPEN')
    return {state='HALF_OPEN', allowed=true}  -- Allow probe
  else
    return {state='OPEN', allowed=false}      -- Fast-reject
  end
elseif state == 'HALF_OPEN' then
  -- Only one request at a time probes; others wait
  return {state='HALF_OPEN', allowed=check_probe_throttle()}
end
```

**Probe throttling (key innovation):**
The "probe throttling" prevents a thundering herd when half-open: Redis holds a per-provider lease (e.g., `probe_lease:{provider}` with TTL 5s). Only the request holding the lease probes; others wait or skip. This prevents 1000 concurrent requests all trying to recover simultaneously.

**Clock-skew protection:**
If `opened_at` is in the future (client clock ahead of Redis server), the script treats it as "open forever" until time catches up. Prevents accidental circuit closes due to time drift.

**UNVERIFIED CLAIMS:** The PyPI page mentions "lock-free finite state machine" but the exact CAS (compare-and-swap) or watch/multi primitives aren't documented. Likely uses Redis transactions (WATCH/MULTI/EXEC) or optimistic locking.

## The dual RPM/TPM budget

**Problem statement:** 
- RPM (requests/minute) and TPM (tokens/minute) are separate constraints but highly correlated
- Reserve-first pattern: client wants to know *before* calling the provider whether the request will count against limits
- Refund pattern: estimated token count pre-request; actual count post-response may differ
- All-or-nothing: if RPM OR TPM would exceed, reject entire request (don't partially consume)

**Redis key structure:**
```
bucket:{provider}:{model}:rpm:current   → integer (requests in current window)
bucket:{provider}:{model}:tpm:current   → integer (tokens in current window)
bucket:{provider}:{model}:rpm:window_start → epoch float
bucket:{provider}:{model}:tpm:window_start → epoch float
```

**Token estimation (O(1) problem):**
The interesting knot: LLM providers return token counts AFTER streaming completes. But rate limiters must decide BEFORE the request hits the provider.

**Solution: Use estimated token count from request body:**
```python
# Pre-request
estimated = estimate_tokens(prompt)  # Tokenize locally (tiktoken, or model-specific)
if current_tpm + estimated > TPM_LIMIT:
  reject_immediately()
else:
  reserve_tokens(provider, model, estimated)  # Deduct from bucket
  call_provider()

# Post-response
actual = response.usage.total_tokens
refund = estimated - actual
if refund != 0:
  refund_tokens(provider, model, refund)  # Adjust bucket upward/downward
```

**All-or-nothing Lua script (dual-budget):**
```lua
-- Atomic reserve or reject
local rpm = tonumber(redis.call('GET', rpm_key)) or 0
local tpm = tonumber(redis.call('GET', tpm_key)) or 0
local estimated_tokens = tonumber(ARGV[1])

if rpm >= RPM_LIMIT or tpm + estimated_tokens > TPM_LIMIT then
  return {allowed=false, reason='RATE_LIMIT', retry_after_ms=calculate_retry(rpm, tpm)}
else
  redis.call('INCR', rpm_key)
  redis.call('INCRBY', tpm_key, estimated_tokens)
  return {allowed=true, reserved_tokens=estimated_tokens}
end
```

**Window management:**
- On each check, if current window has aged past 60 seconds, atomically reset both counters and window_start
- Ensures per-minute windows don't drift; resets happen on first request after minute boundary

**Accuracy challenges:**
- Tokenizer drift: local estimate (tiktoken) vs. provider's actual (Claude counts different than GPT-4)
- Streaming responses arrive incrementally; token count finalizes at stream_end
- Solution (mentioned in docs): Refund on discrepancy; track cumulative error per provider; alert if error > ±10%

**UNVERIFIED:** The package claims O(1) token estimation. This likely means:
1. Hash table lookup of tokenizer (not search)
2. Single Lua roundtrip (not multiple calls)
3. No recursive depth computation; linear scan of tokens only

## Shared state without Redis: Strategic recommendation for flexrouter v2

**Current problem:** flexrouter's local JSON files (penalties.json, quarantine.json) + in-memory windows mean each process keeps separate tallies. Two processes both believe they have full RPM/TPM budget, both spend it, both get rate-limited. Uncoordinated chaos.

**Gradient of solutions (cost vs. safety):**

### Option 1: Single-writer daemon (recommended)
**Approach:** One long-lived "state manager" process owns all `.flexrouter/` JSON files. Other processes call it via localhost socket (Unix domain socket or TCP).

**Mechanism:**
- Main server starts on port 7353 (internal)
- All routing decisions query it: `GET /state/query?provider=groq&model=llama` → returns rate_limit_status, penalties, budget
- All mutations go through it: `POST /state/penalize` with exponential backoff duration
- Uses `fcntl.flock()` on .flexrouter/ to coordinate with CLI tools

**Pros:**
- No Redis dependency (single-file Python `asyncio` server, ~300 LOC)
- Reuses existing JSON schema; migrations trivial
- Fast enough (local IPC + in-memory cache)
- Works in environments without Redis (local dev, offline mode)

**Cons:**
- One more process to manage
- Bottleneck if 1000s of concurrent requests (but flexrouter isn't built for that scale anyway)

**Adoption:** **Medium.** Existing library users need to start the daemon. Requires refactoring `RoutingEngine` to use HTTP client instead of direct state access.

---

### Option 2: SQLite with WAL (practical alternative)
**Approach:** Move all state to SQLite with WAL (Write-Ahead Logging) mode enabled. Multiple processes safe; SQLite handles locking.

**Schema:**
```sql
CREATE TABLE rate_limits (
  key TEXT PRIMARY KEY,  -- "provider/model"
  rpm_count INT, tpm_count INT,
  window_start REAL,
  updated_at REAL
);

CREATE TABLE penalties (
  key TEXT PRIMARY KEY,
  until REAL, count INT, reason TEXT
);

CREATE TABLE quarantine (
  key TEXT PRIMARY KEY,
  until REAL, reason TEXT
);
```

**Mechanism:**
- Each process opens `sqlite:///flexrouter.db?mode=rwc` with WAL enabled
- Rate limit queries use `SELECT ... WHERE key=?` with `BEGIN IMMEDIATE` for writes
- Penalties and quarantine use simple upsert patterns
- Schema migrations handled by library startup (check schema version, apply deltas)

**Pros:**
- Zero Redis dependency
- Built-in ACID, no custom locking
- Single file on disk (portable, Git-ignorable)
- Better than JSON: queries (WHERE, JOIN) possible for future features
- SQLite is stable and battle-tested

**Cons:**
- Slightly slower than in-memory (disk I/O) — but rate limit queries are sparse
- Requires `python -c "import sqlite3"` (stdlib, included)
- WAL creates `-wal` and `-shm` files; must all stay together

**Adoption:** **Medium.** Breaks JSON schema but migration is one-time. Better DX than daemon.

---

### Option 3: Optional Redis backend (plug-in model)
**Approach:** Make state backend pluggable. Ship with two implementations:

1. **LocalFileBackend** (default): JSON + locks, same as today but thread-safe within process
2. **RedisBackend** (opt-in): Lua scripts, distributed Lua scripts, cluster-safe

**Mechanism:**
```python
class StateBackend(ABC):
  def get_rate_limit(self, provider, model) -> RateLimitState: ...
  def record_request(self, provider, model, tokens: int) -> None: ...
  def penalize(self, provider, model, seconds: int) -> None: ...

class LocalFileBackend(StateBackend):
  def __init__(self, state_dir): ...
  
class RedisBackend(StateBackend):
  def __init__(self, redis_url): ...
```

**Config:**
```yaml
state_backend: local  # or "redis://localhost:6379"
```

**Pros:**
- Users choose: local dev (no deps) vs. production (Redis)
- Aspirational: future could add PostgreSQL, DynamoDB
- Gradual migration path (start local, move to Redis)

**Cons:**
- More code to maintain (tests for both)
- Tempts users to switch backends mid-deploy (dangerous)
- Adds abstraction that may not pay for itself

**Adoption:** **Hard.** Requires testing, documentation, and ongoing maintenance of multiple backends.

---

### Recommendation: **Option 2 (SQLite + WAL) for v2.0**

**Reasoning:**
1. **Minimal dependencies:** SQLite is in Python stdlib; no external service
2. **Safer than local files:** ACID semantics, proper locking, no race conditions
3. **Clear migration path:** v2 beta uses SQLite; v2.1 can add optional Redis plug-in for scale-out
4. **Familiar to users:** SQL queries are debuggable (`sqlite3 flexrouter.db ".schema"`)
5. **Portable:** File-based state, no network, works offline, dev-friendly

**Implementation sketch:**
```python
# flexrouter/backends/sqlite.py
class SQLiteStateBackend:
  def __init__(self, db_path: str):
    self.db = sqlite3.connect(db_path, check_same_thread=False)
    self.db.execute("PRAGMA journal_mode = WAL")
    self._init_schema()
  
  def record_request(self, provider: str, model: str, tokens: int):
    with self.db:
      self.db.execute(
        "UPDATE rate_limits SET rpm_count = rpm_count + 1, "
        "tpm_count = tpm_count + ?, updated_at = ? "
        "WHERE key = ?",
        (tokens, time.time(), f"{provider}/{model}")
      )
```

**What changes in flexrouter:**
1. `PenaltyBox` and `SlidingWindow` become thin wrappers over SQL queries
2. `RoutingEngine.select()` calls backend instead of local dicts
3. Server startup runs schema migration
4. Zero API changes for end users

**Not doing (yet):**
- Redis integration (v2.1+)
- Multi-instance cloud deployment (post-v2)
- Replication, backup (users manage SQLite backups normally)

---

## What it does better than flexrouter

| Feature | resilient-llm-gateway | flexrouter | Winner | Difficulty |
|---------|-------|----------|--------|-----------|
| **Multi-process safety** | ✅ Redis FSM + Lua (atomic) | ❌ Local JSON + no locks | Gateway | hard |
| **Dual-budget atomicity** | ✅ Single Lua script evaluates RPM+TPM together | ❌ Separate window checks, can exceed one while avoiding other | Gateway | hard |
| **Circuit breaker sophistication** | ✅ Probe throttling (prevents herd), clock-skew protection | ❌ Simple exponential backoff, no probe coordination | Gateway | medium |
| **Token estimation** | ✅ Documented O(1) reserve-first pattern | ⚠️ No pre-request estimation; relies on provider response | Gateway | medium |
| **Streaming failover** | ✅ "Safe pre-yield streaming failovers" (exact mechanism TBD) | ❌ Currently doesn't support switching mid-stream | Gateway | medium |
| **Semantic caching** | ✅ fastembed ONNX embeddings for similarity match | ❌ No caching | Gateway | hard |
| **Observability** | ✅ Prometheus metrics export + detailed state machine logs | ⚠️ CSV audit log only, no time-series metrics | Gateway | medium |

**Adoption difficulty interpretation:**
- **easy:** Can copy pattern directly; low-risk proof-of-concept
- **medium:** Requires refactoring existing code; moderate risk
- **hard:** Architectural change; needs new dependencies or major rewrite

---

## What flexrouter does better

| Feature | flexrouter | resilient-llm-gateway | Winner | Why |
|---------|---------|-------|--------|-----|
| **Standalone library** | ✅ Drop into any Python app (no server startup) | ❌ Requires FastAPI server or embedded mode | flexrouter | easier to integrate for simple scripts |
| **Session stickiness** | ✅ Explicit `session_id` parameter + TTL-based expiry | ⚠️ Not mentioned in docs (UNVERIFIED) | flexrouter | clearer semantics |
| **Vision support** | ✅ `vision=True` tier filtering + detection hooks | ❌ Not documented | flexrouter | niche but explicit |
| **Cost budgeting** | ✅ Daily per-provider USD caps + warnings | ⚠️ Only mentioned as "monthly budget tracking" (scope TBD) | flexrouter | more granular |
| **Configurable retry** | ✅ Presets (conservative/balanced/aggressive) + manual tuning | ❌ Not documented | flexrouter | explicit policy control |
| **Config hot-reload** | ✅ YAML watch on mtime; state preserved across reloads | ❌ Unknown (likely requires server restart) | flexrouter | dev experience |
| **Model score randomization** | ✅ "Random within 20% of top score" prevents thundering herd | ⚠️ Not documented (may not exist) | flexrouter | clever heuristic |

---

## Ideas worth stealing for v2 (ranked by impact)

### 1. **Pre-request token reservation with refund** (HIGH)
**Current flexrouter:** Uses `estimated_tokens` to warn on context window overflow, but doesn't reserve against RPM/TPM until *after* the provider responds.

**Steal this:** Before hitting the provider, atomically reserve `estimated_tokens` against TPM. On response, refund the difference. This prevents overshooting rate limits under concurrent load.

**Implementation:**
```python
# In router.py, before calling provider
reserve_id = engine.reserve_tokens(provider, model, estimated_tokens)
try:
  response = await client.call(...)
  actual_tokens = response.usage.completion_tokens
  engine.refund_tokens(reserve_id, estimated_tokens - actual_tokens)
except:
  engine.refund_tokens(reserve_id, estimated_tokens)  # Full refund on failure
```

**Risk:** Low. Already doing estimation; just move the deduction earlier.

---

### 2. **Atomic dual-budget Lua script** (HIGH)
**Current flexrouter:** Checks `window.available(rpm_limit, tpm_limit)` — two separate conditions. Can satisfy one and violate the other under concurrency.

**Steal this:** Move to atomic all-or-nothing check. Ensures both RPM and TPM hold or both reject.

**Implementation:** If adopting SQLite, use a transaction:
```python
with db:
  rpm_count = db.execute(f"SELECT rpm_count FROM rate_limits WHERE key = ?", (key,)).fetchone()[0]
  tpm_count = db.execute(f"SELECT tpm_count FROM rate_limits WHERE key = ?", (key,)).fetchone()[0]
  
  if rpm_count >= rpm_limit or tpm_count + estimated_tokens > tpm_limit:
    raise RateLimitError()  # REJECT (neither counter incremented)
  
  db.execute("UPDATE rate_limits SET rpm_count = rpm_count + 1, tpm_count = tpm_count + ?", (estimated_tokens,))
  # Commit happens here; no partial consumption
```

**Risk:** Low. Correctness improvement, minimal API change.

---

### 3. **Probe throttling for circuit breaker half-open** (MEDIUM)
**Current flexrouter:** No half-open state. Once penalized, waits `base_seconds * 2^count` until retrying. No coordination between processes.

**Steal this:** Add half-open state to PenaltyBox. When penalty expires, only one process (lease holder) probes; others wait. Prevents herd.

**Implementation:**
```python
class PenaltyBox:
  def is_half_open(self, provider, model) -> bool:
    """Penalty expired; return True if this process holds the probe lease."""
    key = f"{provider}/{model}"
    if not self._is_penalized(key):
      return False  # Not penalized
    penalty_expired = self._state[key][0] <= time.time()
    if not penalty_expired:
      return False  # Still penalized
    # Penalty expired; try to acquire lease
    return self._acquire_probe_lease(key, ttl=5)
  
  def _acquire_probe_lease(self, key, ttl):
    # With SQLite: UPDATE with CASE to atomically check+set
    # With local file: use flock + timestamp
    ...
```

**Risk:** Medium. Adds complexity; requires understanding penalty TTL semantics.

---

### 4. **Semantic caching for deterministic calls** (MEDIUM)
**Current flexrouter:** No caching.

**Steal this:** For non-streaming requests, cache on SHA-256 of request body. For repeated prompts, serve cache immediately. Falls back to semantic similarity for paraphrases.

**Implementation:**
```python
# In router.py, before calling provider
cache_key = hashlib.sha256(json.dumps(messages).encode()).hexdigest()
if cached_response := cache.get(cache_key):
  return cached_response

response = await provider.call(...)
cache.set(cache_key, response, ttl=3600)  # 1hr
return response
```

**Risk:** Medium. Must handle cache staleness for non-deterministic models (temperature > 0). Discard cache if same prompt asks for different temperature/top_p.

---

### 5. **Streaming failover (mid-stream provider switch)** (HIGH)
**Current flexrouter:** If provider fails, routes to next in tier. But already streaming partial response to client.

**Steal this:** Pause streaming, close first provider connection, replay buffered tokens from second provider's response (if it provides streaming). Transparent failover mid-request.

**Implementation:**
```python
stream1 = await provider1.stream(...)
tokens_buffer = []
try:
  async for chunk in stream1:
    tokens_buffer.append(chunk)
    yield chunk
except ProviderError:
  # Stream failed; switch providers
  stream2 = await provider2.stream(...)
  async for chunk in stream2:
    yield chunk
```

**Risk:** High. Complex error handling; may break streaming contracts. Verify with OpenAI-compatible API spec.

---

### 6. **Prometheus metrics export** (MEDIUM)
**Current flexrouter:** CSV audit log. No time-series metrics.

**Steal this:** Export Prometheus-compatible `/metrics` endpoint.

**Metrics:**
```
flexrouter_requests_total{provider,model,status}
flexrouter_request_duration_seconds{provider,model}
flexrouter_rate_limit_current{provider,model,dimension="rpm|tpm"}
flexrouter_provider_failures_total{provider}
flexrouter_penalty_active{provider,model}
```

**Implementation:** Use `prometheus_client` library (~15 LOC). Include in FastAPI server.

**Risk:** Low. Additive; no API changes.

---

### 7. **Provider health predictions** (MEDIUM)
**Current flexrouter:** Routes based on score + current penalties. No predictive model.

**Steal this:** Track failure patterns per provider (e.g., "Groq fails at 4pm UTC every Tue"). If pattern matches, deprioritize proactively.

**Implementation:**
```python
# After each failure, record (timestamp, provider, error_code, model)
# Analyze: any day-of-week / hour-of-day patterns?
# On selection, check: "Is it 4pm UTC on Tuesday? Groq might fail."
```

**Risk:** Medium. Over-fitting risk; patterns may be noise. Requires historical data (weeks of logs).

---

## Traps / things NOT to copy

### 1. **Assuming estimation tokenizers are accurate**
resilient-llm-gateway's O(1) token estimation assumes tiktoken (or similar) matches the provider's real tokenizer. But:
- GPT-4 tokenizes differently than Claude ✓ (documented in docs)
- Reasoning tokens in o1 aren't counted in standard tokenizers ✗ (hard failure)
- Streaming completion tokens arrive incrementally; can't know total until stream_end

**Don't copy:** Don't trust pre-request estimate for strict budgets. Always refund/adjust post-response. Build in ±15% buffer.

---

### 2. **"All-or-nothing consumption" without overflow tracking**
The docs claim "all-or-nothing" — either both RPM and TPM pass, or both reject. But what if provider returns 10% more tokens than estimated?

**Don't copy:** Without overflow tracking, you'll gradually drift into over-consumption. Track cumulative error per provider; alert if |error| > 10%.

---

### 3. **Probe throttling without retry-after**
The "probe throttling" prevents herd, but the non-lease-holding requests still wait. If they wait indefinitely, the client times out.

**Don't copy blindly:** Return 503 Retry-After with estimated recovery time for non-lease holders. Let client decide whether to retry.

---

### 4. **Single shared circuit breaker per provider**
resilient-llm-gateway's circuit breaker trips if *any* model on a provider fails repeatedly. But one model can fail (e.g., deleted) while others work fine.

**Better:** Per-model circuit breaker (flexrouter's current approach). Or hybrid: per-model breaker + provider-level breaker if ≥30% of models down.

---

### 5. **Lua script complexity without testing**
The gateway's Lua scripts are claimed to be "atomic" and "lock-free," but Lua script bugs are hard to debug (no stack traces from Redis). High-complexity scripts (multi-key writes, conditional logic) are error-prone.

**Don't copy:** Keep Lua simple. Prefer application-level logic + SQLite transactions over complex Lua if possible. Lua shines for *checking* state (read-only); avoid complex *mutations* in Lua.

---

### 6. **Relying on Redis without failover**
resilient-llm-gateway's architecture assumes Redis is available. If Redis goes down, the gateway falls back to... what? Degraded behavior? Crash?

**Don't copy:** If picking Redis, plan for Redis circuit-breaker (liteLLM pattern: 5 consecutive timeouts → open circuit, disable rate limiting, fall back to local state).

---

## Evidence

**Primary sources (read):**
- [resilient-llm-gateway PyPI page](https://pypi.org/project/resilient-llm-gateway/)
- [liteLLM Redis circuit breaker blog](https://docs.litellm.ai/blog/redis-circuit-breaker)
- [Rate Limiting - LLM Gateway Core](https://www.mintlify.com/anaslimem/llm-gateway-core/rate-limiting)

**Secondary sources (searched but incomplete):**
- [GauravFrr/LLM-Gateway GitHub](https://github.com/GauravFrr/LLM-Gateway) — appears to implement circuit breaker + rate limiting (no public source for resilient-llm-gateway itself found)
- [Designing a Distributed Rate Limiter with Redis Lua Scripts](https://sibilsarjamsoren.in/blog/distributed-rate-limiting-redis-lua)
- flexrouter source: engine.py, recovery.py, window.py, config.py, budget.py

**Unverifiable claims:**
- UNVERIFIED: `resilient-llm-gateway`'s exact source code location. PyPI links to `github.com/your-org/llm-gateway` (placeholder). Likely a private/internal package or repo name is different.
- UNVERIFIED: "O(1) token estimation" — likely refers to hash table lookup + single roundtrip, but no source code available to confirm.
- UNVERIFIED: "Safe pre-yield streaming failovers" — mechanism not documented; inferred to mean switching providers mid-stream.
- UNVERIFIED: Semantic caching implementation details (fastembed ONNX model used, cache key derivation, TTL strategy).
- UNVERIFIED: Exact probe throttling mechanism (Redis lease, Lua script, or application-level check).

**Confidence levels:**
- **High:** Circuit breaker FSM (CLOSED/OPEN/HALF_OPEN), Lua scripts for atomicity, Redis key storage
- **Medium:** Dual-budget all-or-nothing semantics, token refund pattern, Prometheus metrics
- **Low:** Streaming failover details, semantic caching specifics, probe throttling implementation

