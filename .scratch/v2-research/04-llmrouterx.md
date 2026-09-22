# llmrouterx: Competitor Research Report

**Researcher:** Claude Haiku 4.5  
**Date:** 2026-09-18  
**Package:** llmrouterx v0.1.32 (Apache 2.0, Python 3.10+)  
**Repository:** https://github.com/amar8737/LLMRouter  
**PyPI:** https://pypi.org/project/llmrouterx/

---

## What it is

llmrouterx is a Python routing and load-balancing layer for multiple LLM providers. It intelligently selects among providers and API keys, handling failover, rate-limit awareness, metrics collection, and per-key circuit breakers. It presents a unified async interface over OpenAI-compatible endpoints (OpenAI, Groq, Cohere, Anthropic, etc.) and scales to handle concurrent requests across multiple keys per provider.

---

## How it actually works

### Architecture: Three-tier routing hierarchy

The system organizes routing through a strict hierarchy:

```
LLMRouter (main entry point)
└── CompositeRouter (manages failover across providers)
    ├── ProviderRouter (represents one provider, e.g. "openai")
    │   └── ClientNode[] (one per API key, e.g. sk-xxx-1, sk-xxx-2, sk-xxx-3)
    │       ├── CircuitBreaker (per-key state machine)
    │       └── Adapter (wraps provider SDK: AsyncOpenAI, AsyncAnthropic, etc.)
    │
    └── ProviderRouter (next provider, e.g. "groq")
        └── ClientNode[]
```

When a request arrives at `LLMRouter`, it delegates to `CompositeRouter`, which tries each `ProviderRouter` in order. Each `ProviderRouter` uses a scheduler to pick a healthy `ClientNode` (i.e., a specific API key). If a key's circuit breaker is open, the scheduler skips it. If all keys fail, failover moves to the next provider.

### Request flow and retry logic

From the code: "failover happens only *before* the first token is emitted" for streaming. This means:

1. **Provider selection**: CompositeRouter iterates through providers in configured order
2. **Key selection**: ProviderRouter's scheduler picks a ClientNode (key)
3. **Execution**: Request executes against the chosen key
4. **Failure handling**: If transient, retry within the same provider (different key); if all keys in provider fail, failover to next provider
5. **Permanent failure**: The code "unwraps failures: if any of those is transient, unwrap it so the shared retry policy can still classify and retry it"

---

## Per-key circuit breakers: detailed mechanism

### State machine: CLOSED → OPEN → HALF_OPEN → CLOSED

Each `ClientNode` wraps a `CircuitBreaker` with explicit three-state transitions:

**CLOSED** — Normal operation. Requests pass through.

**OPEN** — Failure threshold reached. Requests are rejected immediately.

**HALF_OPEN** — After cooldown expires, trial requests are allowed to probe recovery.

### State transitions with exact thresholds

| From | To | Trigger | Code |
|------|-----|---------|------|
| CLOSED | OPEN | Consecutive failures reach `failure_threshold` (default: 5) | `record_failure()` increments counter; at threshold, state = OPEN |
| OPEN | HALF_OPEN | Cooldown timer expires (default: 30s) | `maybe_advance()` checks elapsed time; if timeout exceeded, state = HALF_OPEN |
| HALF_OPEN | CLOSED | Single successful request during trial phase | `record_success()` resets failure counter to 0 |
| HALF_OPEN | OPEN | Any failure during trial phase | `record_failure()` in HALF_OPEN state reopens immediately |
| OPEN | CLOSED | `reset_if_expired()` called after cooldown | Full recovery, bypass HALF_OPEN (alternative fast path) |

### Configurable recovery strategies

The implementation supports **two recovery modes**:

1. **Aggressive recovery** (default): Single success in HALF_OPEN → CLOSED. Failure counter resets to 0.
2. **Gradual recovery** (with `success_decay_factor < 1.0`): Each success decrements counter by decay factor until zero.

### Key implementation details

- **Thread-safe via Lock**: All state transitions protected by `asyncio.Lock` per ClientNode
- **Time-based gating**: `maybe_advance()` is called before attempting the request; avoids automatic state changes
- **TOCTOU-safe**: Atomic gating prevents race conditions during concurrent operations
- **State stored in-memory**: Circuit state is ephemeral (survives only for the process lifetime); no persistence to disk

Configuration via environment variables:
```
LLMROUTER_CIRCUIT_BREAKER=true|false  # enable/disable
LLMROUTER_CB_THRESHOLD=5              # failures to trigger OPEN
LLMROUTER_CB_RESET_TIMEOUT=30         # cooldown in seconds
```

### Per-key active-request tracking

The `ClientNode` tracks concurrent load via:

```python
async def acquire():
    await semaphore.acquire()           # Global per-key slot limit
    with lock:
        active_requests += 1             # Increment counter

async def release():
    try:
        # ... do work
    finally:
        with lock:
            active_requests -= 1         # Decrement counter
        semaphore.release()              # Release slot
```

The `is_saturated` property returns true when all concurrency slots are occupied, enabling the scheduler to skip overloaded keys before attempting the request.

---

## Schedulers: abstraction and implementations

### Scheduler interface

All schedulers inherit from `BaseScheduler` and implement:

```python
async def select(provider_router: ProviderRouter) -> ClientNode | None
```

The method filters to healthy, non-saturated clients and returns one or `None`. The `_healthy_clients()` helper catches and logs exceptions during health checks without failing.

### Five built-in schedulers

**LeastBusyScheduler** — "for client in healthy_clients: if load < best_load" where `load = client.active_requests`. Returns the key with fewest concurrent requests.

**RoundRobinScheduler** — Maintains `_indices` dict (per provider router) and `_locks` for thread-safety. Calculates `(idx + i) % len(healthy_clients)` to cycle through available keys. Wraps around using modulo arithmetic.

**WeightedScheduler** — Weighted random selection. Retrieves each client's `weight` attribute (default 1), validates non-negative, builds cumulative probability distribution, generates random number scaled by total weight, selects the corresponding client.

**PriorityScheduler** — Ordered by `priority` attribute (higher = preferred). Selects the highest-priority healthy, non-saturated client.

**RandomScheduler** — Uniform random selection from healthy clients.

All schedulers skip unhealthy clients (circuit breaker OPEN) and saturated keys (all concurrency slots occupied).

---

## Health checks: passive monitoring

llmrouterx implements **passive health monitoring**, not active probes:

1. **Failure tracking**: During request execution, exceptions are caught and `circuit_breaker.record_failure()` is called
2. **Success recording**: Successful requests call `circuit_breaker.record_success()`
3. **Health status**: Emerges from request outcomes, not dedicated health endpoints

The router provides a simple health API:

```python
health_report = await router.health()  # Returns {provider_name: is_healthy}
is_healthy = await client_node.is_healthy(force=False)  # Check cached or forced
```

**No active probes**: The code does not send dedicated health-check requests (e.g., a dummy chat completion). Health is inferred from real request outcomes during normal operation. This avoids:
- Wasting API quota on synthetic requests
- Adding latency overhead
- Complicating billing (passive monitoring = zero overhead cost)

---

## API key rotation mechanism

### Automatic key discovery

The system scans environment variables for numbered key variants:

```
OPENAI_API_KEY      → ClientNode 1
OPENAI_API_KEY_1    → ClientNode 1 (same)
OPENAI_API_KEY_2    → ClientNode 2
OPENAI_API_KEY_3    → ClientNode 3
```

The config resolves keys via `resolve_api_key()`:

```python
def resolve_api_key():
    # Try legacy methods first:
    if "api_key" in config: return config["api_key"]
    if "api_key_env" in config: return os.environ[api_key_env]
    if "api_key_file" in config: return read_file(api_key_file)
    # Try database lookup:
    if api_key_db: validate_from_db(config["api_key"])
```

### Scheduler-driven rotation

Once discovered, keys are **not rotated round-robin automatically**. Instead, the per-provider scheduler chooses which key to use on each request:

- **LeastBusy**: Picks the key with fewest active requests (load-aware)
- **RoundRobin**: Cycles through keys in order
- **Weighted**: Selects based on key-level weight (different weights per key)
- **Priority**: Picks highest-priority key first

This is more sophisticated than simple counter-based round-robin: **keys with lower load or higher priority get more traffic**, which naturally spreads rate-limit burden.

### Key management (new feature in v0.1.32+)

The `api_keys_db.py` module supports persistent key storage with:
- `create_key(expires_in="24h" | "7d" | "30d")` — Create expiring keys
- `revoke_key(prefix)` — Revoke keys by partial match
- `is_valid` property — Check "not revoked and not expired"
- `last_used_at` — Audit trail tracking

This enables rotating keys without editing config files.

---

## What it does better than flexrouter

### 1. **Per-key circuit breakers** (hard to adopt)

llmrouterx isolates failures at the key level. If one API key is broken, its circuit opens; other keys on the same provider continue serving. flexrouter penalizes at (provider, model) granularity, so a dead key blocks all models at that provider.

**Adoption difficulty: hard.** Requires rewriting flexrouter's penalty system from (provider, model) tuples to (provider, model, key) tuples, and restructuring the routing hierarchy.

### 2. **Multiple scheduling strategies** (easy to adopt)

Five scheduler implementations enable tuning key selection: least-busy load-balancing spreads requests by active count, weighted lets you prefer certain keys (e.g., higher-tier accounts), priority queues premium keys first. flexrouter rotates keys blindly with `counter % len(keys)`.

**Adoption difficulty: easy.** Add a scheduler abstraction, implement 2–3 strategies (LeastBusy, WeightedRandom, Priority), plumb the scheduler into the key selection logic in `_make_result()`.

### 3. **Per-key saturation awareness** (medium to adopt)

The scheduler checks `client.is_saturated` before selecting a key, skipping overloaded keys proactively. flexrouter does not track key-level concurrency; it tracks only (provider, model) rate limits.

**Adoption difficulty: medium.** Add a semaphore per key (for global concurrency cap) and an `active_requests` counter. Check saturation in the scheduler before picking a key.

### 4. **Hierarchical request routing** (hard to adopt)

The CompositeRouter → ProviderRouter → ClientNode hierarchy cleanly separates provider failover (across ProviderRouters) from key rotation (within a ProviderRouter). flexrouter's flat (provider, model) + (model, api_key) structure conflates the two decisions.

**Adoption difficulty: hard.** Requires a major refactor of the routing engine and the tier/model config structure.

### 5. **Configurable recovery strategies** (easy to adopt)

Circuit breaker offers aggressive vs. gradual recovery modes (decay factor). flexrouter's exponential backoff is fixed.

**Adoption difficulty: easy.** Add a `success_decay_factor` parameter to `CircuitBreaker`; make recovery mode configurable in flexrouter.yaml.

### 6. **Key-level expiration and revocation** (medium to adopt)

Database-backed key management enables rotating keys without restarting; audit trail via `last_used_at`. flexrouter requires editing the config file.

**Adoption difficulty: medium.** Add an optional SQLite DB (or JSON file) for key metadata; validate keys on startup; support invalidation via API/dashboard.

---

## What flexrouter does better

### 1. **Per-provider and per-model quarantine** (with reasons)

flexrouter explicitly distinguishes three failure modes:

- **404/410** (model permanently deleted): Quarantine the model for 24h, reason recorded
- **401/403** (auth failure): Quarantine the provider, not just one key
- **402** (billing failure): Quarantine the provider

This prevents infinite retries on unrecoverable failures. llmrouterx's circuit breaker will eventually enter HALF_OPEN and re-probe, risking live requests on dead resources.

### 2. **Local penalty/recovery state survives process restart**

flexrouter writes penalties and quarantine reasons to `.flexrouter/penalties.json` and `.flexrouter/quarantine.json`, so the router "remembers" failed models across restarts. llmrouterx circuit breaker state is in-memory only; restart clears all open circuits.

### 3. **Rate-limit headroom tracking**

flexrouter's `RateLimitStore` tracks not just (rpm, tpm) usage but also provider-reported remaining headroom from response headers (e.g., `x-ratelimit-remaining-tokens`). It can predict exactly when a model becomes available again. llmrouterx relies on circuit breaker failure counts, not provider feedback.

### 4. **Provider-wide quarantine for auth failures**

When flexrouter sees 401/403 (bad API key), it quarantines the whole provider. A tier that mentions that provider falls back to other providers in the tier. llmrouterx would only open the circuit for that one key; if the tier has no other providers, retries still fail.

### 5. **Session stickiness with fallback**

flexrouter supports `session_id` to pin a conversation to one (provider, model) pair. If that model becomes unavailable mid-session, routing transparently moves to the next best model, preserving the session ID. llmrouterx does not have session stickiness.

### 6. **Transparent error capture and explanation**

flexrouter logs every failure reason (e.g., "404: model deleted", "401: auth failure", "402: billing") to the audit log and events. The dashboard shows which models failed and why. llmrouterx circuit breaker counts failures but discards the reason.

### 7. **Daily provider budget tracking**

flexrouter optionally enforces daily USD spend caps per provider (e.g., OpenAI ≤ $5/day). Routes switch to cheaper providers when a cap is exceeded. llmrouterx has no spend cap feature.

### 8. **Token-based quota tracking**

flexrouter tracks per-provider request/token quotas (optional per-tier, from config). Routes skip providers if their quota is exhausted in the current period. llmrouterx does not have this feature.

---

## Ideas worth stealing for v2 (ranked by impact)

### Tier 1: High-impact, medium-effort

1. **Per-key circuit breaker** (hardest, but highest value)  
   Replace the (provider, model) → (until, count) penalty tuple with (provider, model, key) → CircuitBreaker object. Model the state machine: CLOSED (healthy) → OPEN (N failures) → HALF_OPEN (after cooldown, probe recovery). This isolates one bad key from blocking all models at a provider. **Effort: 2–3 days.** Payoff: Dramatically improves robustness when multiple keys are in use.

2. **Scheduler abstraction**  
   Define a `Scheduler` interface; implement LeastBusyScheduler (picks key with fewest active requests), WeightedScheduler (respects key-level weights from config), PriorityScheduler. Let users set `scheduler: weighted` in the provider config. **Effort: 1 day.** Payoff: Enables load-aware key rotation instead of blind round-robin.

3. **Per-key active-request tracking**  
   Add `active_requests` counter and semaphore per key (not per model). The scheduler skips saturated keys proactively. **Effort: ½ day.** Payoff: Avoids queueing requests on overloaded keys; spreads load more evenly.

### Tier 2: Medium-impact, low-effort

4. **Configurable recovery strategy for circuit breaker**  
   Add `success_decay_factor: 0.8` to circuit breaker config. In HALF_OPEN, success decrements failure count by decay factor instead of fully resetting. Enables gradual recovery instead of one-shot success = closed. **Effort: 2 hours.** Payoff: Finer control over how aggressively the router re-trusts a recovering key.

5. **Key expiration and revocation**  
   Add optional SQLite DB (or persistent JSON) for key metadata: creation time, expiration, revoked flag, last_used_at. Validate keys on startup; skip revoked/expired keys in selection. **Effort: 1 day.** Payoff: Rotate keys without restarting; audit key usage.

6. **Persistent circuit breaker state**  
   Write circuit breaker state (per key) to `.flexrouter/circuit_breakers.json` on state changes. Load on startup. Survives process restart. **Effort: ½ day.** Payoff: Dead keys don't spring back to CLOSED after restart.

### Tier 3: Nice-to-have, lower-priority

7. **Active health probes** (with smart stagger)  
   Send lightweight probes to closed-circuit keys periodically (e.g., once per 5 minutes). Transition OPEN → HALF_OPEN immediately if probe succeeds. Reduces MTTR (mean time to recovery). **Effort: 1 day.** Payoff: Faster recovery detection; risk: uses quota.

8. **Per-key weight in config**  
   Allow `api_keys: [{env: KEY_1, weight: 2}, {env: KEY_2, weight: 1}]` to prefer certain keys. Weighted scheduler respects weights. **Effort: 2 hours.** Payoff: Route more traffic to high-tier/high-limit keys without code changes.

9. **Key-level metrics**  
   Track per-key: success/failure count, circuit breaker state transitions, active requests over time. Dashboard shows which keys are healthy/overloaded. **Effort: 1 day.** Payoff: Operator visibility into key health and utilization.

---

## Traps / things NOT to copy

### 1. **In-memory circuit state with no persistence**

llmrouterx's circuit breaker state is ephemeral (process-only). Restart the server → all open circuits close → dead keys immediately get traffic → failures spike. flexrouter's persistent quarantine is better. **Solution:** Always persist circuit state to disk and load on startup (see idea #6).

### 2. **No explanation of failures**

The circuit breaker increments a failure counter but discards the HTTP status, error message, and exception type. Later, an operator sees "5 failures" but not *why*. flexrouter's "reason" field (e.g., "404: model deleted") is essential for debugging. **Solution:** Store the reason for the most recent failure in circuit breaker state.

### 3. **HALF_OPEN state allows unlimited trial requests**

The `half_open_max_calls` parameter (not shown in docs) gates how many requests can probe during HALF_OPEN. If it's too high, a recovering but still-broken key gets hammered. If it's too low, recovery is slow. flexrouter's exponential backoff is more predictable. **Solution:** Test your `half_open_max_calls` value empirically; document it in v2 config.

### 4. **No distinction between transient and permanent failures**

llmrouterx circuit breaker increments on *any* failure. flexrouter's PenaltyBox skips transient errors (5xx) by default and only penalizes on 4xx/429. A key that temporarily times out should not open the circuit. **Solution:** Differentiate error types; only penalize true availability failures (429, certain 5xx), not client errors.

### 5. **No auth failure handling at the provider level**

When one key on a provider is rejected (401/403), llmrouterx opens that key's circuit. But other keys on the same provider are also likely to be rejected (same account credentials). flexrouter's provider-wide quarantine is smarter. **Solution:** If a key returns 401/403, consider quarantining the whole provider (or at least warn the operator).

### 6. **Scheduler cannot veto based on error category**

Schedulers pick healthy keys but cannot say "if all keys returned 429 last time, wait before picking any again." flexrouter's `seconds_until_available()` method factors in rate-limit recovery time. **Solution:** Let scheduler access per-key recovery state; skip keys in OPEN state longer than the provider's typical rate-limit window.

---

## Evidence

### Primary sources (direct code inspection)

- **Repository structure & key files:**
  - https://raw.githubusercontent.com/amar8737/LLMRouter/main/README.md (architecture overview)
  - https://raw.githubusercontent.com/amar8737/LLMRouter/main/llmrouterx/retry/circuit_breaker.py (state machine)
  - https://raw.githubusercontent.com/amar8737/LLMRouter/main/llmrouterx/client/client_node.py (per-key tracking)
  - https://raw.githubusercontent.com/amar8737/LLMRouter/main/llmrouterx/router/llmrouter.py (composite routing)
  - https://raw.githubusercontent.com/amar8737/LLMRouter/main/llmrouterx/scheduler/base.py (scheduler interface)
  - https://raw.githubusercontent.com/amar8737/LLMRouter/main/llmrouterx/scheduler/least_busy.py (example scheduler)
  - https://raw.githubusercontent.com/amar8737/LLMRouter/main/llmrouterx/scheduler/weighted.py (example scheduler)
  - https://raw.githubusercontent.com/amar8737/LLMRouter/main/llmrouterx/scheduler/round_robin.py (state management)
  - https://raw.githubusercontent.com/amar8737/LLMRouter/main/llmrouterx/config/api_keys.py (key resolution)

### Package metadata

- **PyPI:** https://pypi.org/project/llmrouterx (v0.1.32, Apache 2.0, Amar Jeet Kushwaha)
- **GitHub:** https://github.com/amar8737/LLMRouter (active development, Python 3.10+)

### Unverified claims in documentation

- "Health checks with timeouts" — the code does not show active health probes; monitoring is passive
- "Structured JSON logging" — not verified by code inspection
- "Streaming support" — claimed in README, not examined in detail

---

## Conclusion

llmrouterx excels at **per-key isolation and scheduler flexibility**. Its circuit breaker prevents one bad key from taking down all models at a provider; its scheduler abstraction enables load-aware and priority-based key rotation.

flexrouter excels at **failure classification and persistent state**. It distinguishes permanent (404/410) from transient errors, quarantines providers on auth failure (not just one key), and remembers penalty state across restarts. Its rate-limit headroom tracking is more sophisticated.

**For v2, the highest-value adoption is per-key circuit breakers + scheduler abstraction** (medium-effort, high-payoff). These two features would make flexrouter more resilient under load and in production. The other ideas are lower-priority optimizations.

