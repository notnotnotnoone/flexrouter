# tokentaxi: Research Report

**Research Date:** 2026-09-18  
**Status:** Package exists on PyPI but source code not publicly accessible.

## Verdict: Limited Findings

tokentaxi exists as a published PyPI package (version 1.1.4, released March 17, 2026), confirmed via PyPI JSON API at `https://pypi.org/pypi/tokentaxi/json`. However:

- The PyPI web page fails to load (JavaScript errors)
- The project URL in metadata is a placeholder: `https://github.com/your-org/tokentaxi`
- No public GitHub repository can be found via direct search or code search
- Source code is not publicly accessible for review

**Searches performed:**
- PyPI direct API calls (tokentaxi, token-taxi)
- GitHub site search (tokentaxi)
- GitHub code search for distinctive phrases ("session affinity" + "TPM" + "headroom")
- General web search (multiple keyword combinations)
- Direct GitHub repository guesses

Only the package metadata is available; implementation details cannot be verified.

## What was claimed

User provided the following claims about tokentaxi's features:

> "tracks RPM and TPM in a rolling 60-second window and routes to the provider with the most headroom; session affinity via a `session_id`; priority lanes; circuit breakers; latency-aware EMA scoring. In-process library, no extra network hop. Its pitch is 'bring your own client' rather than YAML-configured tiers."

Additionally, from the PyPI metadata we retrieved:

> "adaptive rate-limit-aware LLM routing" that allows developers to work with existing LLM SDK clients; intelligent request routing based on provider health and rate limits; automatic fallback between providers; circuit breaker pattern with auto-recovery; session affinity and priority lane support; zero external dependencies for core functionality

**All implementation details are UNVERIFIED.** No source code review possible.

## Ideas worth evaluating anyway

These two concepts merit investigation for flexrouter v2, regardless of whether tokentaxi implements them. They are grounded in the claimed features list but evaluated independently.

### 1. Headroom-aware tie-breaking (MEDIUM value, MEDIUM effort)

**Concept:** When multiple models in a tier have the same score, prefer the one with more TPM/RPM headroom remaining.

**Why it's worth evaluating:**
- Improves utilization without abandoning score-based routing or tier isolation
- Dynamic: adapts to real-time rate-limit state
- Does not replace operator intent; enriches it

**Implementation sketch:**
```python
def _pick(self, candidates, tier):
    # candidates already filtered by score; pick randomly from top 20%
    # NEW: among candidates at same score, prefer higher headroom
    scored = [(c, self._headroom_for(c)) for c in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[0][0]

def _headroom_for(self, model_config):
    k = f"{model_config.provider}/{model_config.model}"
    window = self._windows.get(k)
    if not window:
        return float('inf')
    return model_config.tpm - window.total_tokens_in_window()
```

**Honest assessment:**
- Low risk; orthogonal to existing score filtering
- Requires tracking TPM consumption in sliding window (flexrouter has this)
- Minimal performance impact
- Would need testing to verify it actually improves utilization over time

### 2. Latency-aware secondary scoring (MEDIUM value, MEDIUM effort)

**Concept:** Track EMA (exponential moving average) of provider response latencies. Use as a tie-breaker when multiple models have the same score and similar headroom.

**Why it's worth evaluating:**
- Routes around consistently slow providers
- Balances throughput with latency; doesn't ignore slow providers just because they have capacity
- Useful in production where latency variance matters

**Implementation sketch:**
```python
class LatencyTracker:
    def __init__(self, alpha=0.2):  # EMA smoothing factor
        self.ema = {}  # provider -> float (ms)
        self.alpha = alpha
    
    def record(self, provider, latency_ms):
        current = self.ema.get(provider, latency_ms)
        self.ema[provider] = self.alpha * latency_ms + (1 - self.alpha) * current
    
    def get_ema(self, provider):
        return self.ema.get(provider, 0.0)

# In _pick():
scored = [(c, self._headroom_for(c)) for c in candidates]
# Tier by headroom
scored.sort(key=lambda x: x[1], reverse=True)
# Then by latency EMA for same headroom
scored.sort(key=lambda x: self._latency_tracker.get_ema(x[0].provider))
return scored[0][0]
```

**Honest assessment:**
- Requires explicit latency measurement (add to audit log)
- EMA smoothing prevents single slow request from affecting routing
- Risk: may concentrate traffic on low-latency providers; tier isolation still holds but usage becomes uneven
- Would need monitoring to verify it helps or hurts cost

## What flexrouter does better

### 1. **Strict tier isolation**
Tier boundaries are hard; a busy `low` tier never falls back to `high`. Operators have predictable SLOs per tier. This is a design choice that tokentaxi (without source access) appears not to enforce.

### 2. **Persistent state across restarts**
Rate-limit windows, penalty counters, and quarantine state survive process restart via `.flexrouter/` JSON files. In-process libraries lose this on restart.

### 3. **Model quarantine for permanent deletions**
Detects permanently-deleted models (404/410) and quarantines for 24h with reason recorded. Detects auth failures (401/403/402) and sidelines the entire provider. Does not retry permanently-broken models indefinitely.

**Code reference:** `flexrouter/recovery.py` lines 9-12, 57-75 (quarantine load/save with reason tracking)

### 4. **Blind multi-key round-robin**
Multiple API keys per provider rotate automatically. A 429 on one key skips it immediately for that provider. Spreads rate limits across key pool.

**Code reference:** `flexrouter/_router.py` implements key rotation per provider

### 5. **FastAPI server + React dashboard**
Live telemetry (RPM/TPM bars, penalty countdowns), request logs, provider cost tracking, config editor with key testing. In-process libraries don't have this.

### 6. **Hot reload**
Edit `flexrouter.yaml` while the app runs. Config reloaded on next call; rate-limit windows and penalty state preserved. Allows operational changes without restart.

**Code reference:** `flexrouter/engine.py` lines 46-57 (`update_config()` preserves window state)

### 7. **Error context preservation**
Provider error messages are kept and recorded (not discarded). Failures are logged with full context for debugging.

**Code reference:** `flexrouter/events.py` records full error messages; `flexrouter/recovery.py` tracks reason in quarantine

## Evidence

### Positive: tokentaxi PyPI metadata confirmed

- **PyPI JSON API:** https://pypi.org/pypi/tokentaxi/json — returns package metadata for version 1.1.4
- **Package name:** tokentaxi
- **Version:** 1.1.4
- **Release date:** March 17, 2026
- **License:** MIT
- **Python requirement:** ≥3.11
- **Description:** "adaptive rate-limit-aware LLM routing" (from metadata)
- **Repository URL in metadata:** https://github.com/your-org/tokentaxi (placeholder, not real)

### Negative: Source code not accessible

- PyPI web page (https://pypi.org/project/tokentaxi/) does not load (JavaScript errors)
- GitHub searches for "tokentaxi" return no results
- GitHub code search for distinctive phrases ("session affinity" + "TPM" + "headroom") finds other projects (busbarAI, various microservices) but no tokentaxi
- No public GitHub repository reachable under any organization

### Flexrouter verification

All claims about flexrouter advantages grounded in source code review:
- `C:\projects\Finished projects\router\flexrouter\engine.py` (routing logic, hot reload)
- `C:\projects\Finished projects\router\flexrouter\recovery.py` (quarantine mechanism, reason tracking)
- `C:\projects\Finished projects\router\flexrouter\_router.py` (key rotation, client logic)
- `C:\projects\Finished projects\router\flexrouter\window.py` (sliding window implementation)
- `C:\projects\Finished projects\router\README.md` (tier isolation, session stickiness docs)

---

## Recommendation

1. **For headroom and latency tie-breaking:** These are low-risk enhancements that don't require tokentaxi source code. Evaluate independently based on flexrouter's use cases.
2. **For detailed tokentaxi comparison:** Need access to actual source code or detailed docs. The package exists but is not publicly inspectable.
3. **For v2 architecture decisions:** Flexrouter's tier isolation and persistent state are strong advantages. Do not abandon them in pursuit of headroom-based routing unless there is a specific production need.
