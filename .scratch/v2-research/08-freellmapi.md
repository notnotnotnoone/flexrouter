# FreeLLMAPI Competitive Analysis

## What it is

FreeLLMAPI is a self-hosted Node.js server that aggregates 34 free LLM providers (635 model endpoints) behind an OpenAI-compatible `/v1` endpoint. It features automatic model failover, per-key usage tracking against free-tier caps, rate-limit awareness, and a React dashboard. Its defining feature: **models are pulled from a signed remote feed** (`https://api.freellmapi.co/v1/latest`), so the model catalog and quota limits auto-update without code changes or git pulls.

---

## How it actually works

### Architecture
- **Server**: Express + TypeScript, runs as a persistent background service (like Ollama)
- **Storage**: SQLite with AES-256-GCM encrypted credentials
- **API**: OpenAI-compatible `/v1/` endpoints (chat completions, embeddings, audio transcription, image/video generation)
- **Dashboard**: React SPA served from the same port (default 3000)
- **Key rotation**: Multiple API keys per provider, auto-rotated round-robin; 429s immediately skip the failed key

### Tier System
No hard tiers. Instead, users define a "fallback chain" — a manually-ordered list of (provider, model) pairs. The router walks the chain sequentially, applying gates:
- Key health and decryption success
- Not on cooldown from recent 429s
- Daily provider caps not exceeded
- Per-minute/day request and token limits not violated
- Monthly budget not exhausted

First match wins. If exhausted, router returns diagnostic output (grouped by failure reason).

### Model Selection Without Hand Scores
Unlike flexrouter's `score: 85` field in YAML, FreeLLMAPI ranks models algorithmically using a **hybrid bandit strategy**:

- **Thompson sampling**: Maintains Beta posterior over reliability, speed, and intelligence; samples proportional to uncertainty (balances exploration/exploitation)
- **Catalog-provided ranks**: Each model gets `intelligenceRank` (1–1000, lower is smarter) and `speedRank` (1–11, lower is faster) from the signed feed
- **Community priors**: Reliability uses historical performance data blended with priors from other instances
- **Saturation curves**: Speed throughput uses a saturating function so one very fast tiny model can't dominate larger models; time-to-first-byte is separate
- **Capability tiers**: Frontier, Large, Medium, Small labels compress the intelligence rank with a square root so edits near the top are visible

**No hand-assigned quality scores**. The ranking emerges from (1) measured performance + (2) catalog metadata, not manual curation.

### Key Credentials Management
- Keys stored encrypted at rest (AES-256-GCM) in local SQLite
- Dashboard provides "Save & Test" UI: each key is tested immediately against a probe model, returns green/red per provider
- Supports declarative config via `FREEAPI_CONFIG_JSON` env var or `FREEAPI_CONFIG_PATH` file (full JSON schema)
- Never logs keys in plaintext; redaction filter on all stdout

---

## The catalog feed: deep dive

### Hosted Location & URLs
- **Feed URL**: `https://api.freellmapi.co/v1/latest` (configurable via `CATALOG_BASE_URL` env var)
- **Optional licensing endpoint**: checks premium tier status
- **HTTP method**: GET with optional `since=<version>` param for 304 (unchanged) responses

### Fetch Schedule
- **Initial fetch**: 10 seconds after server startup
- **Free tier**: Twice daily (12-hour intervals); receives "monthly snapshot" — models join the free tier approximately 30 days after entering the live feed
- **Premium tier**: Same-day updates (refreshed every 2–3 days)
- **Force refresh**: Triggered when license status changes or on admin request

### Format & Schema

**Top-level JSON structure**:
```json
{
  "version": "2026.09.18.3f7b7f",
  "generatedAt": "2026-09-18T15:30:00Z",
  "tier": "monthly",
  "counts": {
    "platforms": 34,
    "models": 474,
    "embeddings": ...,
    "transcriptions": ...
  },
  "platforms": [
    { "id": "openai", "name": "OpenAI" },
    ...
  ],
  "models": [ /* see below */ ]
}
```

**Model entry structure** (core fields):
```json
{
  "platform": "openai",
  "modelId": "gpt-4o",
  "displayName": "GPT-4o (128K ctx)",
  "intelligenceRank": 1,
  "speedRank": 8,
  "sizeLabel": "Frontier",
  "limits": {
    "rpm": 500,
    "rpd": 5000,
    "tpm": 150000,
    "tpd": null
  },
  "monthlyTokenBudget": "10M",
  "contextWindow": 128000,
  "enabled": true,
  "supportsVision": true,
  "supportsTools": true,
  "quirks": [
    {
      "slug": "expensive",
      "title": "Paid model",
      "body": "Free tier limited to 10M tokens/month",
      "severity": "warning"
    }
  ]
}
```

**Optional model fields**:
- `modality`: `"image"`, `"audio"`, `"transcription"`, `"video"` for non-text models
- `mediaNote`: Special usage notes for media endpoints
- `requestStyle`: `"multipart"` for multipart form data
- `premiumSince`: ISO timestamp for models added to premium tier

**Quirks** (zero or more per model): content advisories documenting limitations, platform-specific issues, or unusual behaviors.

### Signature & Verification

**Ed25519 cryptographic signing**:
- Signature passed in response header: `x-catalog-signature: <base64-ed25519-sig>`
- Verified against pinned public key:
  ```
  -----BEGIN PUBLIC KEY-----
  MCowBQYDK2VwAyEAq9yv4+3EeyMHKsfVYBhkcz1lYgIXSUeHNnN6tNgYX3k=
  -----END PUBLIC KEY-----
  ```
- Private key never left the catalog host

**Verification process** (TypeScript pseudocode):
```typescript
const signature = res.headers.get('x-catalog-signature');
if (!signature) throw new Error('catalog response missing signature');
const bytes = Buffer.from(await res.arrayBuffer());
const verified = crypto.verify(null, bytes, catalogPublicKey(), 
  Buffer.from(signature, 'base64'));
if (!verified) throw new Error('catalog signature verification FAILED — discarding response');
```

**Environment override**: `CATALOG_PUBKEY` env var allows custom public key (for testing or forked deployments).

**Tamper rejection**: Anything unsigned or with a failed signature is silently discarded. A compromised CDN or MITM cannot inject models.

### Caching & Offline Behavior

**Local cache storage**: Verified catalog is cached in SQLite (`SETTING_APPLIED_JSON` key) after signature verification succeeds.

**Offline startup**: On boot, `reapplyCachedCatalog()` re-applies the cached catalog synchronously, **without network access**. This keeps the catalog authoritative even when offline or if the feed is down.

**Version tracking**: Fetch requests include `since=<applied_version>` parameter. Server can return HTTP 304 (unchanged) to skip re-application, reducing bandwidth.

**License status**: License checks gracefully degrade — if offline or service down, the last known cached status is preserved for UI display. Entitlement is enforced server-side at `/v1/latest` anyway.

**Failure handling**: All network errors logged to `SETTING_LAST_ERROR` without disrupting the application. The app continues with the previous catalog.

---

## Could flexrouter have this?

### Strategic Recommendation: **Build a curated auto-updating feed + Thompson sampling**

**Yes, absolutely — and with high adoption value.**

Flexrouter's biggest known weakness is the hand-written 13.5KB YAML with ~96 models, of which 30 no longer exist. A signed catalog feed would solve this with minimal rework to the v2 architecture (which is already server-based).

### Three Approaches (ranked by effort vs. value)

#### 1. **Subscribe to FreeLLMAPI's feed** (EASIEST, LEAST CONTROL)
- Fetch their catalog directly
- Adapt the model schema to flexrouter's database
- **Pros**: No maintenance, auto-updates, covers 34 providers
- **Cons**: Locked to their model taxonomy; can't fork or customize; depends on their service uptime
- **Effort**: Medium (schema translation, caching layer)
- **Recommendation**: Skip. Their free tier gets monthly snapshots; for a router, you need faster updates.

#### 2. **Publish flexrouter's own signed catalog feed** (MEDIUM, RECOMMENDED)
- Curate a small subset of proven providers (Groq, OpenRouter, Together, Cerebras, etc.)
- Generate a signed JSON feed, host on a static CDN
- Include intelligenceRank, speedRank (from LM benchmarks + human curation), quotas from provider docs
- Update quarterly or on-demand (e.g., when a new model lands on OpenRouter)

**Proposed schema** (aligned with FreeLLMAPI but simpler):
```json
{
  "version": "2026.09.18.001",
  "generatedAt": "2026-09-18T15:30:00Z",
  "updatedBy": "flexrouter-maintainers",
  "providers": [
    {
      "id": "groq",
      "name": "Groq",
      "baseUrl": "https://api.groq.com/openai/v1",
      "freeQuota": "unlimited (rate-limited)"
    }
  ],
  "models": [
    {
      "provider": "groq",
      "modelId": "llama-3.1-70b-versatile",
      "displayName": "Llama 3.1 70B",
      "intelligenceRank": 12,
      "speedRank": 2,
      "sizeLabel": "Large",
      "limits": {
        "rpm": 30,
        "tpm": 300000
      },
      "contextWindow": 131072,
      "supportsVision": false,
      "supportsTools": true,
      "enabled": true,
      "lastVerified": "2026-09-18T10:00:00Z",
      "verifiedBy": "live probe"
    }
  ]
}
```

- Sign with EdDSA (same as FreeLLMAPI)
- Publish to GitHub Releases or a static CDN
- Server fetches on startup + weekly
- Cache locally; continue offline with cached version
- Daily liveness probe: quick HTTP HEAD to each model's endpoint (track 401/402/404 separately)

**Pros**:
- Full control over model taxonomy
- Quarterly maintenance (not continuous)
- Can fork or extend easily
- Smaller scope = easier to maintain
- Signed, so desktop/CI can validate integrity

**Cons**:
- Manual curation of quota limits
- Need to probe providers for endpoint changes
- Smaller provider ecosystem than FreeLLMAPI
- Must maintain public key distribution

**Adoption difficulty**: Medium. Requires:
- Schema migration (convert YAML tiers to feed-based model lookup)
- Caching layer (download + verify + apply atomically)
- Liveness probing (detect dead models early)
- Dashboard updated to show "last verified" + "verified by"

#### 3. **Build the catalog from provider APIs** (HARDEST, MOST FLEXIBLE)
- No feed. Instead, on first startup, probe each provider's `/models` endpoint and build a local catalog
- Add a small YAML overlay for unmeasurable fields (intelligenceRank, speedRank)
- Re-probe monthly or on admin request

**Pros**:
- Always reflects reality (provider's actual model list)
- Zero feed infrastructure
- Easy to add new providers (just add probe code)

**Cons**:
- No signed feed for CI/reproducibility
- Probe failures leave you with stale data
- No way to know about quota changes without hitting the provider
- Unmeasurable fields still require manual curation
- Many providers don't publish quotas publicly; you must probe live

**Adoption difficulty**: Hard. Requires:
- Provider-specific probe logic (each provider's `/models` response is different)
- Retry/backoff for flaky endpoints
- Fallback to cached catalog if probes fail
- Still need a small YAML overlay for ranking

### Recommended Data Model (Approach 2)

**Python SQLAlchemy schema** (for flexrouter v2 server):

```python
class Catalog(Base):
    __tablename__ = "catalogs"
    id = Column(Integer, primary_key=True)
    version = Column(String, unique=True, index=True)  # e.g., "2026.09.18.001"
    generated_at = Column(DateTime, index=True)
    updated_by = Column(String)  # e.g., "flexrouter-maintainers"
    data_json = Column(JSON)  # full signed payload
    signature_base64 = Column(String)
    applied_at = Column(DateTime, default=now)
    is_active = Column(Boolean, default=True)

class Provider(Base):
    __tablename__ = "providers"
    id = Column(Integer, primary_key=True)
    catalog_id = Column(ForeignKey("catalogs.id"))
    provider_id = Column(String)  # e.g., "groq"
    name = Column(String)
    base_url = Column(String)
    free_quota_desc = Column(String)

class Model(Base):
    __tablename__ = "models"
    id = Column(Integer, primary_key=True)
    catalog_id = Column(ForeignKey("catalogs.id"))
    provider_id = Column(String)
    model_id = Column(String)
    display_name = Column(String)
    intelligence_rank = Column(Integer)  # 1–1000, lower is smarter
    speed_rank = Column(Integer)  # 1–11, lower is faster
    size_label = Column(String)  # Frontier, Large, Medium, Small
    rpm_limit = Column(Integer, nullable=True)
    tpm_limit = Column(Integer, nullable=True)
    context_window = Column(Integer, nullable=True)
    supports_vision = Column(Boolean, default=False)
    supports_tools = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True)
    last_verified = Column(DateTime)
    verified_by = Column(String)  # "live probe" or "catalog"
    quirks_json = Column(JSON)  # list of {slug, title, body, severity}
    unique(provider_id, model_id, catalog_id)

class ModelProbeRecord(Base):
    __tablename__ = "model_probes"
    id = Column(Integer, primary_key=True)
    model_id = Column(ForeignKey("models.id"))
    probe_time = Column(DateTime, index=True)
    status_code = Column(Integer)  # 200, 401, 404, etc.
    error_msg = Column(String, nullable=True)
    tokens_available = Column(Integer, nullable=True)  # if provider exposes it
```

---

## Supply-chain safety: honest risk assessment

### Threats

1. **Feed compromise**: Attacker injects models pointing to their own endpoints or credential harvesters
   - **Mitigation**: Ed25519 signature + pinned public key in source code
   - **Residual risk**: High if private key leaks; private key stored only on catalog host (good), but no key rotation mechanism observed

2. **Signature bypass**: Attacker spoofs `x-catalog-signature` header
   - **Mitigation**: `crypto.verify()` enforces strict verification; unsigned responses discarded
   - **Residual risk**: Low if crypto library is trustworthy (Node.js crypto is battle-tested)

3. **Man-in-the-middle (MITM)**: Attacker intercepts feed over HTTP
   - **Mitigation**: HTTPS only (all real instances use HTTPS)
   - **Residual risk**: Low if cert pinning used (not observed in code; standard HTTPS TLS sufficient)

4. **Public key distribution**: Attacker replaces pinned key in source code via supply-chain attack (compromised repo, malicious PR)
   - **Mitigation**: Code review, signed git commits, GitHub branch protection
   - **Residual risk**: Moderate; requires defensive strategy

5. **Offline/cached catalog exploitation**: Attacker compromises local database, edits cached catalog
   - **Mitigation**: Cached catalog is re-verified on next fetch; user-defined models cannot override catalog
   - **Residual risk**: Low if database is not world-writable; SQLite permissions depend on OS permissions

6. **Rate-limit feed lag**: Free tier gets monthly snapshots; attacker launches model then removes it, router doesn't find out for 30 days
   - **Mitigation**: Daily live probes detect dead models quickly
   - **Residual risk**: Low with probing; high without it

### Required Safeguards (for flexrouter's own feed)

1. **Pinned public key in source code** with versioning:
   ```python
   CATALOG_PUBLIC_KEYS = {
       "2026-Q3": "MCowBQYDK2VwAyEAq9yv...",  # key active in Q3 2026
       "2026-Q4": "MCowBQYDK2VwAyEA...",      # staged rotation
   }
   ```
   Rotate keys quarterly. Never remove old keys immediately (grace period for clients).

2. **Daily liveness probes** for each model (HEAD request or a dummy query):
   - Track 401 (dead key), 404 (model gone), 429 (rate-limited), 5xx (temporary down)
   - Quarantine models that return 401/404 for 24h; disable after 3 consecutive days
   - Surface probe results in dashboard: "Last verified 6h ago, green"

3. **Signed feed + offline cache** (as described above)
   - Fetch weekly
   - Cache locally
   - Verify signature on every fetch

4. **Feed change log**:
   - Every catalog version includes a `changes` array: what was added, removed, modified since last version
   - Dashboard shows "Models added: gpt-4o, claude-3.5, llama-3.1-405b; Models removed: gpt-3.5-turbo"
   - Users can subscribe to a changelog feed (RSS or webhook)

5. **Staged rollout for breaking changes**:
   - If a model's quota or capabilities change dramatically, introduce a `deprecatedAt` field
   - Serve for 2 weeks with warnings before removal
   - Publish a migration guide

6. **Public key backup & recovery**:
   - Store private key in a Hardware Security Module (HSM) or encrypted key management service (AWS KMS, GCP KMS)
   - Publish multiple public keys; rotation plan documented in a README
   - If private key suspected leaked, publish an emergency catalog bump with new key

---

## Per-key free-tier accounting

FreeLLMAPI's `provider_quota_state` table tracks per-key usage across multiple dimensions:

```sql
CREATE TABLE provider_quota_state (
  id INTEGER PRIMARY KEY,
  key_id INTEGER NOT NULL,
  platform VARCHAR NOT NULL,
  model VARCHAR,  -- nullable; null means platform-level pool
  quota_pool_key VARCHAR,  -- "openai::free", "groq::account", etc.
  requests_limit INTEGER,
  requests_remaining INTEGER,
  tokens_limit INTEGER,
  tokens_remaining INTEGER,
  reset_at TIMESTAMP,
  reset_strategy VARCHAR,  -- "provider_reported" or "token_bucket"
  confidence FLOAT,  -- 0.0–1.0; ≥0.7 used in routing decisions
  last_updated TIMESTAMP
);
```

### How it works

1. **Pool inference**: For a given key + platform + model, `inferPoolForPlatform()` determines the quota pool:
   - **Groq**: One key = one pool (per-key limit)
   - **OpenRouter**: Free models share one pool (`openrouter::free`); paid models share another
   - **Cerebras**: All free requests share one account-level pool (not per-model)
   - **Anthropic**: Per-model limits

2. **Observation collection**:
   - Extract quota from response headers (e.g., `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`)
   - Parse reset time from `x-ratelimit-reset-requests` or `retry-after`
   - For providers without headers, infer from errors: 429 means remaining=0, retry-after becomes reset time

3. **Balance coherence**:
   - Only update an older observation if new data includes actual remaining values
   - Prevents stale probes from overwriting fresh measurements
   - Confidence score reflects data source: headers get ≥0.7; client-side estimates get <0.7

4. **Reset strategies**:
   - `provider_reported`: Reset time from header or API
   - `token_bucket`: No reset; assume continuous refill (rare)

5. **Headroom calculation** (cached for 5 seconds):
   ```python
   headroom_requests = remaining_requests - (rpm_limit * seconds_until_reset / 60)
   headroom_tokens = remaining_tokens - (tpm_limit * seconds_until_reset / 60)
   available = headroom_requests > 0 and headroom_tokens > 0
   ```
   Gates the routing decision: only use keys with positive headroom.

6. **Daily provider caps**:
   - Separate table: `provider_daily_spend` tracks cumulative cost (USD) per provider per calendar day
   - If spend exceeds limit (e.g., $5/day for OpenAI), all keys for that provider are disabled
   - Resets at UTC midnight

### Differences from flexrouter's current approach

| Aspect | flexrouter | FreeLLMAPI |
|--------|-----------|-----------|
| **Window type** | Sliding window (fixed duration) | Per-provider (varies: RPM, hourly, daily, monthly) |
| **Reset tracking** | Manual cron job | Header parsing + timestamp from provider |
| **Multi-key pooling** | Round-robin (no quota awareness) | Pool-based (understands shared vs. per-key limits) |
| **Per-key state** | Single counter per model | Key + platform + model lookup → quota observation |
| **Confidence scoring** | Binary (limit or not) | Float 0.0–1.0; ≥0.7 used in decisions |
| **Headroom calculation** | None (checks only current window) | Proactive: accounts for time until reset |

---

## What it does better than flexrouter

1. **Auto-updating catalog** (HIGHEST IMPACT)
   - Models auto-update from signed feed; flexrouter requires git pull + restart
   - Dead models (404) detected and quarantined within 24h; flexrouter retries forever
   - Adoption difficulty: Medium (schema + caching + verification)

2. **Thompson sampling + no hand scores**
   - Ranking emerges from measured performance + catalog metadata
   - flexrouter's `score: 85` is static and requires curation
   - Adoption difficulty: Medium (requires Thompson sampler implementation + historical perf table)

3. **Per-provider quota pooling**
   - Understands that OpenRouter's free models share one pool, not per-model
   - Tracks confidence score; ≥0.7 used in routing (avoids unreliable measurements)
   - flexrouter tracks only per-model sliding windows
   - Adoption difficulty: Hard (requires quota observation refactor)

4. **Graceful degradation on auth failures**
   - 401 sidelines the key but tries other keys for the model
   - flexrouter had a single auth failure abort the entire tier
   - Adoption difficulty: Easy (error classification + skip-list)

5. **Multi-modal model support** (chat, embeddings, image, audio, transcription)
   - flexrouter v2 will need this anyway
   - Adoption difficulty: Medium (schema + routing logic per modality)

6. **Dashboard debugging views**
   - Quota state, cooldown timers, per-key status, model availability reasons
   - flexrouter's dashboard is simpler (no quota breakdown)
   - Adoption difficulty: Medium (UI + backend queries)

---

## What flexrouter does better

1. **Python library + server dual-mode**
   - Library can be imported and used directly in Python; FreeLLMAPI is server-only
   - Python developers don't have to start a separate service
   - Adoption value: High (low friction)

2. **Session stickiness**
   - Pin a conversation ID to one model for consistency
   - FreeLLMAPI doesn't expose this (though server could implement it)
   - Adoption value: Medium (nice-to-have for conversational apps)

3. **Live dashboard testing**
   - "Chat" tab lets you test a tier live with custom messages
   - FreeLLMAPI's "Playground" exists but not as well integrated
   - Adoption value: Low (UX polish)

4. **Cost tracking per tier**
   - Audit log breaks down cost by (timestamp, tier, provider, model)
   - Easy to see "how much did 'low' tier cost today?"
   - FreeLLMAPI tracks total spend; per-provider breakdown visible
   - Adoption value: Medium (useful for cost management)

5. **No dependency on external service**
   - flexrouter catalog is local; never calls home
   - FreeLLMAPI depends on `api.freellmapi.co` being up (though it caches)
   - Adoption value: High for privacy-conscious users

---

## Ideas worth stealing for v2 (ranked)

### TIER 1: Must have

1. **Signed catalog feed**
   - Publish flexrouter's own feed (small, curated set of proven providers)
   - Schema: provider, model, intelligenceRank, speedRank, limits, enabled, lastVerified
   - Sign with Ed25519; pin key in source code
   - Effort: 3–4 weeks (schema design, caching, sig verification, dashboard UI)
   - Value: Eliminates hand-written YAML; catches dead models quickly
   - Recommendation: **Build it. This is the #1 weakness.**

2. **Quarantine dead models (404/410) separately from rate limits (429)**
   - 404 = model gone, disable for 24h then remove
   - 429 = temporary, wait 30s–300s then retry
   - Current flexrouter treats both as "penalty"
   - Effort: 2–3 weeks (error classification, quarantine table, dashboard UI)
   - Value: Reduces false retries; clear visibility into "why is this model unavailable?"
   - Recommendation: **Do this in parallel with #1.**

3. **Per-key quota pooling**
   - Understand that some providers have shared pools (e.g., OpenRouter's free tier)
   - Track observations: (key, provider, pool) → (remaining_requests, remaining_tokens, reset_at, confidence)
   - Use headroom calculation to avoid exhausting a pool
   - Effort: 4–5 weeks (quota observation refactor, headroom logic, confidence scoring)
   - Value: Higher throughput; fewer false "no keys available" errors
   - Recommendation: **Do this after #1 & #2; it's the biggest architectural change.**

### TIER 2: Should have

4. **Thompson sampling for model selection**
   - Maintain Beta posterior over reliability, speed, intelligence
   - Sample from posteriors; pick model with highest expected utility
   - Replaces static scores
   - Effort: 2–3 weeks (Thompson sampler library, historical perf tracking, tuning)
   - Value: Automatic ranking based on observed performance; no hand curation
   - Recommendation: **Nice to have; do after Tier 1.**

5. **Daily liveness probes**
   - HEAD request to each model's endpoint; track 200 vs. 401/404/429
   - Detect dead models within 24h
   - Surface "Last verified: 6h ago, green" in dashboard
   - Effort: 1–2 weeks (probe scheduler, result table, dashboard UI)
   - Value: Catches model sunsetting early; complements #2
   - Recommendation: **Pair with #2; easy win.**

6. **Graceful auth failure handling**
   - 401 → sideline key, try next key for model
   - 402 → sideline provider, try next provider
   - Don't abort entire tier on single key failure
   - Effort: 1 week (error classification, skip-list, routing refactor)
   - Value: Higher availability; matches FreeLLMAPI's robustness
   - Recommendation: **Do early; found as #1 bug in v2 runtime testing.**

### TIER 3: Nice to have

7. **Confidence scoring for quota observations**
   - Track source (header vs. client-side estimate); ≥0.7 used in routing
   - Avoid decisions based on unreliable measurements
   - Effort: 1 week
   - Value: More robust quota tracking
   - Recommendation: **Include with #3 if time permits.**

8. **Multi-modal model support**
   - Catalog includes modality (chat, embedding, image, audio, transcription)
   - Routing logic per modality
   - Effort: 2–3 weeks (schema, routing, dashboard)
   - Value: Prepares for expansion beyond chat
   - Recommendation: **Do as part of v2 if supporting embeddings/audio is in scope.**

9. **Changelog feed**
   - Catalog includes `changes` array: added, removed, modified models since last version
   - Dashboard shows "6 models added, 2 removed" on update
   - Effort: 1 week
   - Value: Transparency; helps users understand catalog evolution
   - Recommendation: **Polish; optional.**

---

## Traps / things NOT to copy

1. **Node.js instead of Python**
   - FreeLLMAPI is built in Node.js/TypeScript. flexrouter is Python.
   - **Don't rewrite in Node.js.** Stick with Python; the ecosystem is better for LLMs.

2. **No library mode**
   - FreeLLMAPI is server-only. Python users can't import and use it directly.
   - **Don't lose the library mode.** Keep it as a thin client.

3. **License tier gating**
   - FreeLLMAPI has "free" and "premium" tiers with different update cadence.
   - **Don't add licensing complexity.** Keep the model list fully public; updates are free.

4. **Monthly snapshot delays**
   - Free tier gets monthly snapshots; new models take 30 days to appear.
   - **Don't delay updates.** Publish weekly or on-demand.

5. **Catalog hosted on freellmapi.co**
   - Entire deployment depends on external service uptime.
   - **Host on GitHub Releases or a CDN** with fallback to cached version. No single point of failure.

6. **Quirks as unstructured text**
   - FreeLLMAPI's quirks are markdown fragments in a `body` field.
   - **Use structured fields** instead: `deprecated_at`, `context_window_broken_above`, `requires_vision_key`, etc.
   - Easier to parse and act on programmatically.

7. **No explicit model retirement policy**
   - Models go from enabled→disabled; hard to distinguish "temporarily down" from "EOL".
   - **Add explicit states**: `enabled`, `deprecated`, `retired`, `archived`.
   - Include `deprecated_at`, `removal_date`, `reason` fields.

---

## Evidence

### URLs actually read (in order)

1. [FreeLLMAPI GitHub README](https://raw.githubusercontent.com/tashfeenahmed/freellmapi/main/README.md) — Architecture overview, catalog auto-update claim
2. [API Catalog Response](https://api.freellmapi.co/v1/latest) — Live schema inspection (format, fields)
3. [catalog-sync.ts](https://raw.githubusercontent.com/tashfeenahmed/freellmapi/main/server/src/services/catalog-sync.ts) — Feed URL, fetch schedule, Ed25519 verification, caching strategy
4. [catalog public key](https://raw.githubusercontent.com/tashfeenahmed/freellmapi/main/server/src/services/catalog-sync.ts) — Pinned Ed25519 key, verification code
5. [scoring.ts](https://raw.githubusercontent.com/tashfeenahmed/freellmapi/main/server/src/services/scoring.ts) — Thompson sampling, hybrid ranking, no hand scores
6. [router.ts](https://raw.githubusercontent.com/tashfeenahmed/freellmapi/main/server/src/services/router.ts) — Model selection logic, failover, bandit strategies
7. [provider-quota.ts](https://raw.githubusercontent.com/tashfeenahmed/freellmapi/main/server/src/services/provider-quota.ts) — Per-key quota pooling, observation tracking, headroom calculation
8. [model-state.ts](https://raw.githubusercontent.com/tashfeenahmed/freellmapi/main/server/src/services/model-state.ts) — Tombstone pattern, dead vs. rate-limited, quarantine + reinstatement
9. [index.ts (server entry)](https://raw.githubusercontent.com/tashfeenahmed/freellmapi/main/server/src/index.ts) — Catalog sync scheduling
10. [declarative-config.ts](https://raw.githubusercontent.com/tashfeenahmed/freellmapi/main/server/src/services/declarative-config.ts) — Config schema, JSON format

### Unverifiable claims

- **"7.4 billion tokens per month"** — marketing claim; not verified in source
- **"Premium subscribers get same-day updates"** — mentioned in README; not verified in code
- **"34 providers, 635 endpoints"** — counts in live API; observed as accurate on inspection date (2026-09-18)

### Claims I could NOT verify (no evidence found)

- Whether the private key is actually stored in an HSM or just on disk
- Whether the catalog has ever been rotated (only one public key found in source)
- Whether a MITM/CDN compromise has ever occurred (no incident reports found)
- Whether monthly snapshot delay is still the default (code mentions both "monthly" and "2-3 day" refresh)
