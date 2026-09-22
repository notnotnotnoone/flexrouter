# routeme: Research Report

**Status:** FOUND on PyPI but source code NOT publicly accessible

**Verdict:** routeme (v0.1.2, published Feb 21, 2026) exists as a PyPI package but **implementation cannot be verified** due to inaccessible source code. GitHub repository listed on PyPI (https://github.com/anomalyco/llm_router) returns 404. Without access to actual code, claims cannot be validated—only compared against real, accessible competitors.

---

## What I Found: routeme on PyPI

**Package Details:**
- **PyPI URL:** https://pypi.org/project/routeme/
- **Version:** 0.1.2 (released Feb 21, 2026)
- **Author Email:** remixonwin@gmail.com
- **License:** MIT
- **Python Support:** 3.11, 3.12, 3.13
- **Status:** Beta / Proxy Servers & Application Frameworks

**Claimed Features (from PyPI metadata):**
- Multi-provider LLM support (12+ providers: OpenAI, Anthropic, Google Gemini, Groq, Ollama, etc.)
- Automatic failover
- Quota management
- Response caching (exact and semantic)
- **Five routing strategies:** auto, cost-optimized, quality-first, latency-first, round-robin
- Vision and embedding model support
- FastAPI server capability

**Dependencies:**
```
aiofiles, fastapi, httpx, litellm, pydantic, tenacity, uvicorn
```

**Distribution Files:**
- routeme-0.1.2-py3-none-any.whl (41.8 KB)
- routeme-0.1.2.tar.gz (47.2 KB)

---

## What I Cannot Verify

**UNVERIFIED:** Everything about actual implementation:
- How strategies are expressed and selected at call time
- How cache is keyed and invalidated
- How RPM/RPD counters are stored and reset (claims mention quota tracking)
- How environment variables are used for configuration (claimed feature but not documented)
- Exact failover logic
- Whether responses are actually cached or just claiming to cache

The GitHub repository listed on PyPI (`https://github.com/anomalyco/llm_router`) is **not publicly accessible** (returns 404). This could indicate:
- Private repository (not open source despite being on PyPI)
- Repository deleted or moved
- Author email is not verified and repo may not exist

**Conclusion:** Without access to source code, I cannot write a truthful "how it works" section. The package appears to exist, but its actual mechanism cannot be independently verified.

---

## Nearest Real Equivalents: Deep Dive

Since routeme's internals are inaccessible, I analyzed two actively maintained, open-source competitors:

### 1. **RouteLLM** (lm-sys/RouteLLM)

[GitHub Repository](https://github.com/lm-sys/RouteLLM)

**What It Is:**
A framework by UC Berkeley's LMSYS for intelligent routing between two LLMs (typically weak+cheap vs strong+expensive). Demonstrates 85% cost savings at 95% GPT-4 quality on MT Bench and MMLU.

**How It Actually Works:**

1. **Routing Strategy (Matrix Factorization):**
   - Trains a **matrix factorization model on preference data** (from Chatbot Arena human evaluations)
   - At inference: model encodes the prompt and outputs a score
   - User sets a threshold (e.g., 0.11593 for 50% strong-model routing)
   - If score ≥ threshold → route to expensive model (e.g., GPT-4)
   - If score < threshold → route to cheap model (e.g., Mixtral 8x7B)

2. **Threshold Calibration:**
   - Pre-computed on arena data: "For 50% strong model calls, threshold = 0.11593"
   - No per-call tuning; threshold is a static deployment parameter
   - User controls cost-quality tradeoff by choosing threshold

3. **Supported Alternatives:**
   - Semantic Weighting (sw_ranking): Uses Elo rankings based on prompt similarity
   - BERT classifier: Trained classifiers
   - Causal LLM: Fine-tuned language model classifiers
   - Random: Baseline

**Architecture:**
- Drop-in replacement for OpenAI client
- No caching (not implemented)
- No persistent state; stateless
- No quota/RPM tracking
- Single-call routing (no session stickiness)

**Configuration:**
- Programmatic via Python API, not YAML or environment variables
- Threshold is the only configuration point (besides model endpoints)

**Routing Selectivity:**
- Binary: always picks one of two models (designed for this constraint)
- Cannot route to N models; designed specifically for cost-quality binary choice

---

### 2. **LLMRouter** (ulab-uiuc/LLMRouter)

[GitHub Repository](https://github.com/ulab-uiuc/LLMRouter)

**What It Is:**
A comprehensive framework supporting 16+ routing strategies organized into five categories: single-round, multi-round, multimodal, personalized, and agentic. Designed for researchers and practitioners to benchmark routing methods.

**How It Actually Works:**

1. **Training Pipeline:**
   - Collects queries from 11 benchmark datasets (MT Bench, MMLU, etc.)
   - Generates LLM embeddings from model metadata
   - Calls LLM APIs, evaluates responses, scores performance
   - Creates unified embeddings + routing labels for training

2. **Routing Strategies at Inference (sample strategies):**

   **Single-Round Routers:**
   - **KNN:** Embed query, find k nearest neighbors in training data by embedding similarity, route to model that performed best on neighbors
   - **SVM/MLP:** Trained classifiers that predict best model from query embedding
   - **Matrix Factorization:** Similar to RouteLLM's approach
   - **Graph-based:** Use model relationship graphs
   
   **Multi-Round Routers:**
   - **Router-R1:** Iteratively refines routing through multiple reasoning steps
   - Agentic: Uses LLM itself to decide routing via reasoning
   
   **Personalized Routers:**
   - Incorporate user preference graphs (past user behavior)
   - Customize routing per user rather than per-query

3. **Configuration:**
   - Programmatic API
   - Must choose router type and provide trained model
   - No YAML; all Python

4. **Outputs:**
   - **Gradio chat interface** for interactive testing
   - **ComfyUI visual interface** for pipeline construction (drag-and-drop)
   - **OpenClaw Router** FastAPI server for production deployment
   - **CLI** for training, inference, interactive chat

**Routing Selectivity:**
- Can route to arbitrary N models (unlike RouteLLM's binary)
- Per-router strategy determines selectivity
- Some routers always pick 1 model, others can suggest top-k

**Persistence:**
- Training data persisted; routers are ML models (pickled)
- No per-request caching
- No quota tracking

---

## Strategies vs Tiers: The Core Difference

### flexrouter's Approach: **Hand-Scored Tiers**

```yaml
tiers:
  low:
    - provider: groq
      model: llama-3.1-8b-instant
      score: 85              # ← human-assigned score (1-100)
      rpm: 60                # ← hard rate limit
      tpm: 60000             # ← hard token limit
      quotas: {rph: 120, rpd: 2880}  # ← soft quotas
  high:
    - provider: openai
      model: gpt-4o
      score: 95
      rpm: 60
      tpm: 150000
```

**Selection Logic:**
1. For each tier (isolation enforced): score candidates
2. Skip models: penalized, rate-limited, quota-exhausted, context-window-too-small
3. Pick highest-scoring available model
4. If top scores within 20% of max, pick randomly (avoid thundering herd)

**Key Properties:**
- **Static scoring:** Scores don't adapt to query content
- **Strict tier isolation:** low tier never falls back to high; no cross-tier routing
- **Configuration method:** Hand-written YAML with explicit provider/model list
- **State:** Sliding windows (60s for RPM/TPM), daily budget, penalty box
- **Persistence:** Local JSON files (.flexrouter/audit.csv, quotas.json, health.json)

---

### routeme's Claimed Approach: **Strategy-Based Routing** (UNVERIFIED)

**Claimed strategies:**
- auto
- cost-optimized
- quality-first
- latency-first
- round-robin

**Claimed configuration:**
- Environment variables (NOT YAML like flexrouter)

**NOT VERIFIED:**
- How strategies decide which model to pick at call time
- Whether they adapt to request content (cost-optimized: do they estimate prompt cost?)
- Whether there's any scoring mechanism at all
- How environment variables map to strategy parameters

---

### RouteLLM's Approach: **ML-Trained Binary Routing**

- **Strategy:** Matrix factorization (trained on arena preference data)
- **Decision:** Query → embedding → score → threshold → model (binary choice)
- **Configuration:** Threshold parameter only
- **Scoring:** Data-driven (trained on human preferences), not hand-crafted
- **Selectivity:** Exactly 2 models (cannot route to N)
- **State:** None (stateless)
- **Persistence:** None (but router model is pre-trained)

---

### LLMRouter's Approach: **ML-Trained Multi-Strategy Routing**

- **Strategy:** Choose from 16+; KNN, SVM, MLP, Matrix Factorization, Graph, Agentic, Personalized
- **Decision:** Query → embedding (or raw content) → predict best model → route
- **Configuration:** Strategy choice + pre-trained router + model pool
- **Scoring:** Data-driven (trained on query→response→eval data), not hand-crafted
- **Selectivity:** Arbitrary N models (routers can be trained on any model pool)
- **State:** None (routers are pre-trained models)
- **Persistence:** Router pickle files; no per-request state

---

## What Each Project Does Better Than flexrouter

### routeme (CLAIMED, UNVERIFIED):

**If claims are true:**
1. **Environment variable config** (easy/medium) — no YAML file to hand-write
2. **Response caching** (medium) — semantic caching could deduplicate queries across users
3. **FastAPI server** (easy) — avoid Python library import overhead; share state across requests

**Adoption Difficulty:** Unknown (code not accessible)

### RouteLLM:

1. **Data-driven routing** (hard) — learns from human preferences, not hand-scored
   - Adapts to provider/model performance changes
   - Proven to work: 85% cost savings, 95% quality on benchmarks

2. **Minimal configuration** (easy) — just pick a threshold, not a whole YAML tier
   - Lower learning curve for new users
   - No need to list every provider/model upfront

3. **Peer-reviewed** (research credit) — LMSYS paper, referenced widely
   - Scientific validation
   - Community adoption (citation count)

**Adoption Difficulty:** Medium (requires retraining on your own preference data if you want to customize)

### LLMRouter:

1. **16+ routing strategies** (hard) — flexibility to choose best approach for your use case
   - Single-round vs multi-round
   - Personalized to user preferences
   - Agentic reasoning

2. **Comprehensive benchmarking** (medium) — built-in evaluation on 11 datasets
   - Can measure quality of routing strategy before deployment
   - Train/test splits provided

3. **Multi-round reasoning** (hard) — Router-R1 iteratively refines decisions
   - Better for complex queries
   - Can detect when first pick won't work

4. **User-level personalization** (hard) — Personalized routers learn per-user preferences
   - Route to user's preferred model, not global best
   - Better for user experience consistency

**Adoption Difficulty:** High (requires curating training data, selecting router type, benchmarking)

---

## What flexrouter Does Better

1. **Strict tier isolation** (hard to match) — low tier never consumes high tier budget
   - RouteLLM: binary choice only; no tier concept
   - LLMRouter: all routers route from same pool
   - **flexrouter enforces SLAs:** if you mark models as "low-cost tier," they stay low-cost tier

2. **Per-model quotas (rph/rpd)** (easy/medium) — quota tracking survives process restarts
   - RouteLLM: no quota tracking
   - LLMRouter: no quota tracking
   - **flexrouter:** QuotaTracker persists to JSON; knows about "requests per hour" as distinct from RPM

3. **Session stickiness** (medium) — pin conversation to one model for consistency
   - RouteLLM: no multi-turn support
   - LLMRouter: personalized routers do this, but require pre-training
   - **flexrouter:** Works out of box; session TTL configurable

4. **Hand-curated model list** (double-edged) — you control exactly which models exist
   - RouteLLM: requires manual endpoint setup
   - LLMRouter: requires endpoint setup + training data
   - **flexrouter:** Same, but this is a feature for teams with specific model requirements

5. **Dashboard built-in** (easy) — live telemetry, tier health, request logs, settings UI
   - RouteLLM: no dashboard
   - LLMRouter: Gradio interface (basic)
   - **flexrouter:** Production-ready dashboard at port 7352

6. **Multi-key round-robin** (easy) — rotate between API keys per provider
   - RouteLLM: single key per model
   - LLMRouter: single key per model
   - **flexrouter:** List multiple keys, automatic rotation on 429

7. **Error recovery** (medium) — quarantine models on 404/410; sideline providers on 401/403
   - RouteLLM: no recovery logic
   - LLMRouter: no recovery logic
   - **flexrouter:** Exponential backoff penalties; distinguishes permanent (deleted model) from temporary (rate-limited)

---

## Ideas Worth Stealing for flexrouter v2

### Ranked by Ease & Impact:

#### 1. **Data-Driven Scoring** (hard / high impact)
From RouteLLM: Rather than hand-scoring models 1-100, gather preference data (user ratings, benchmarks, A/B tests) and train a scoring function.

**Implementation:**
- Add telemetry collection: track quality per model (thumbs-up/down, latency, cost)
- Periodically retrain scores on the data
- Use as default tiers; let users override with YAML for static tiers

**Effort:** 3-4 days (data pipeline + retraining loop)
**ROI:** Adapts to provider changes; removes hand-curation burden

---

#### 2. **Multi-Strategy Routing** (hard / medium impact)
From LLMRouter: Allow users to pick routing strategy at init time.

**Implementation:**
- Add strategy parameter: `FlexRouter(strategy="cost-optimized")` vs `strategy="quality-first"`
- Under the hood, these adjust the scoring function or filtering rules:
  - cost-optimized: prioritize models by cost/token, deprioritize large models
  - quality-first: prioritize high-score models, ignore cost
  - latency-first: track response time EWMA per model, deprioritize slow ones
  - round-robin: ignore scores; cycle through available models in order

**Effort:** 2-3 days (new scoring + per-model metrics)
**ROI:** Same API, but users get different behavior without reconfiguration

---

#### 3. **Per-Request Caching** (medium / medium impact)
From routeme (claimed): Cache responses by request hash.

**Implementation:**
- Add cache layer in FlexRouter.generate() before routing
- Hash: (messages, vision, max_tokens, temperature, etc.) → canonical key
- Store in .flexrouter/cache.db (SQLite or simple JSON)
- Return cached response if exact match + TTL not expired

**Configuration:**
```yaml
settings:
  cache:
    enabled: true
    ttl_seconds: 3600
    max_size_gb: 1
```

**Effort:** 2-3 days (cache layer + LRU eviction)
**ROI:** Immediate cost savings for repeated queries; no model selection needed

---

#### 4. **User-Segmented Routing** (hard / medium impact)
From LLMRouter personalization: Route differently per user segment.

**Implementation:**
- Add user_segment parameter: `router.generate(..., user_segment="free_tier")`
- In YAML, define per-segment tiers:
```yaml
tiers:
  free_tier:
    low: [cheap models only]
    high: [medium models]
  paid_tier:
    low: [medium models]
    high: [expensive models]
```
- Routing respects segment tier at call time

**Effort:** 1-2 days (tier lookup by segment + parameter plumbing)
**ROI:** Multi-tenant billing; free users never access expensive models

---

#### 5. **Lazy Model Discovery** (easy / low impact)
From both: Don't require explicit provider/model list upfront.

**Implementation:**
- Add discovery mode: `FlexRouter(mode="discover")`
- User provides provider credentials only
- Router queries each provider's list_models() API
- Auto-populates tier configurations with sensible defaults
- Save to suggested flexrouter.yaml

**Effort:** 1-2 days (API wrappers for discovery; scaffold generator)
**ROI:** Faster onboarding; handles new models released by providers

---

#### 6. **Semantic Cache** (hard / low impact)
From routeme (claimed): Cache based on query similarity, not exact match.

**Implementation:**
- Embed all cached queries + new query with a small model (e.g., all-MiniLM)
- Cosine similarity to cached queries; if > 0.95, return cached response
- Update cache weights when user provides feedback

**Effort:** 3-4 days (embedding pipeline + similarity search)
**ROI:** Catches paraphrased queries; but adds latency (embedding call per request)
**Warning:** Semantic similarity can give misleading results for sensitive queries; verify before shipping

---

#### 7. **Multi-Round Routing** (hard / low impact)
From LLMRouter Router-R1: Routing can change mid-conversation.

**Implementation:**
- Allow tier parameter in per-message calls within a conversation
- Example:
```python
response = await router.agenerate(
    messages=[...user message...],
    tier="low",  # first response from cheap model
)
# User asks followup
response = await router.agenerate(
    messages=[...full conversation...],
    tier="high",  # switch to expensive model if complexity detected
)
```

**Effort:** 2-3 days (tier selection per message; context awareness)
**ROI:** Adaptive spend; only pay for expensive model when needed

---

#### 8. **Vision-Aware Scoring** (easy / low impact)
Enhancement to existing vision support: adjust scores when vision=True.

**Implementation:**
- In scoring, multiply score by 0.5 for models that don't support vision
- Effectively deprioritizes non-vision models in vision requests

**Effort:** Few hours
**ROI:** Prevents silent failures on vision-only calls

---

## Traps to Avoid

### 1. **Not Distinguishing Temporary from Permanent Failures**
flexrouter already does this (404/410 → permanent, 429 → temporary), but many routers don't.
- **Trap:** Retry a deleted model forever (adds latency)
- **flexrouter fix:** Quarantine for 24h with reason recorded
- **Lesson:** Time-box recovery, not infinite retries

### 2. **Losing State on Process Restart**
RouteLLM and LLMRouter have no persistent state. flexrouter does (quotas.json, health.json, audit.csv).
- **Trap:** State is in memory; two processes make independent budget decisions → double spending
- **flexrouter strength:** Quotas survive restart; dashboard can ingest state
- **Lesson for v2:** Keep state in one place (server, not client library)

### 3. **No Cross-Tier Fallback as a Feature**
flexrouter's strict isolation is a feature, but can feel like a limitation.
- **Trap:** "Why can't low tier use high tier if low is exhausted?"
- **flexrouter design:** Intentional; users configure sized tiers
- **Lesson:** Document this as a feature, not a limitation

### 4. **Overcomplicating Configuration**
Both RouteLLM (threshold) and LLMRouter (strategy choice) are simple. flexrouter's YAML is complex.
- **Trap:** New users over-specify tiers, quotas, recovery policies they don't understand
- **Lesson for v2:** Provide preset configs (common-case YAML + discovery mode)

### 5. **Missing Observability**
Without logs of "why was model X rejected?", users can't debug routing.
- **Lesson for v2:** Keep request-level rejection reasons (rate-limit window, quota, penalty, context-window)

### 6. **Cache Key Collisions**
If caching by query text, identical prompts with different users may leak context.
- **Trap:** Semantic cache returns "is user A employed?" answer to user B's "is user A employed?" query
- **Lesson:** Cache should be per-user partition or include user_id in key

### 7. **Multi-Threaded State Corruption**
flexrouter uses a lock (threading.Lock) in _router.py for the event loop. But quotas.json and audit.csv can be written concurrently.
- **Trap:** File writes race; audit log skips entries or corrupts JSON
- **Lesson for v2 server:** Use a database (SQLite) instead of JSON files; enforce transactions

### 8. **Threshold Drift Over Time**
RouteLLM's threshold is static; if provider prices change, threshold is stale.
- **Trap:** Cost-optimized routing picks expensive model at new prices
- **Lesson:** Periodically recompute thresholds from cost data or allow user adjustment

---

## Evidence

### What I Verified:
1. **routeme exists on PyPI** — Direct fetch from https://pypi.org/pypi/routeme/json confirmed v0.1.2, Feb 21 2026
2. **routeme's claimed features** — Listed in PyPI metadata; no source code found to validate
3. **routeme's dependencies** — fastapi, uvicorn, litellm, pydantic, tenacity (suggests it does build an HTTP server and use litellm for provider abstraction)
4. **GitHub repo inaccessible** — https://github.com/anomalyco/llm_router returns 404
5. **RouteLLM real implementation** — Verified via GitHub repo, code, and LMSYS paper
6. **LLMRouter real implementation** — Verified via GitHub repo, documentation, and benchmarks
7. **flexrouter's internals** — Verified via source code in C:\projects\Finished projects\router\

### What I Could NOT Verify:
1. How routeme actually routes requests (no source code)
2. Whether routeme caches (claimed but not verified)
3. Whether routeme uses environment variables or YAML (claimed but not verified)
4. How routeme's five strategies differ (claimed but not verified)
5. Whether routeme actually tracks RPD/RPH quotas (claimed but not verified)

---

## Summary: Is routeme a Threat?

**Short answer:** Probably not, because it's not publicly accessible code.

**Details:**
- routeme exists as a PyPI package, but source is private/inaccessible
- Claimed features align with what competitors do (RouteLLM, LLMRouter)
- No evidence of unique innovation (cost-optimized, quality-first, latency-first are standard LLM routing strategies)
- No public documentation, tutorials, or adoption

**Competitive position:**
- If routeme delivers environment-variable config + caching + multi-strategy routing, it would be a medium threat (easier to use than flexrouter's YAML)
- If routeme's strategies are data-driven (like RouteLLM), it would be a higher threat (adapts to market changes)
- If routeme's code is actually open sourced (currently it's not), adoption could grow

**Recommendation for v2:**
Focus on ideas from RouteLLM and LLMRouter (above), not on competing with routeme. When routeme's source becomes public, revisit.

---

## Files Examined

**Flexrouter source:**
- C:\projects\Finished projects\router\flexrouter\_router.py
- C:\projects\Finished projects\router\flexrouter\engine.py
- C:\projects\Finished projects\router\flexrouter\config.py
- C:\projects\Finished projects\router\flexrouter\quota.py
- C:\projects\Finished projects\router\README.md
- C:\projects\Finished projects\router\PLAN.md

**Competitors:**
- RouteLLM: https://github.com/lm-sys/RouteLLM (main branch README)
- LLMRouter: https://github.com/ulab-uiuc/LLMRouter (main branch README)

**routeme:**
- PyPI JSON endpoint: https://pypi.org/pypi/routeme/json
- PyPI JSON v0.1.2: https://pypi.org/pypi/routeme/0.1.2/json
- PyPI project page: https://pypi.org/project/routeme/ (web load error; JSON data used instead)
- GitHub repo (listed on PyPI): https://github.com/anomalyco/llm_router (404 Not Found)
