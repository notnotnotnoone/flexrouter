# lmrelay: Competitive Research for flexrouter v2

## What it is

lmrelay (formerly freellama) is a credentialed HTTP relay daemon written in Python (FastAPI + uvicorn) that runs beside a local Ollama instance on its own port (default 11435). It selects upstream LLM providers (OpenAI, Anthropic, DeepSeek, Grok, Ollama, OpenRouter, Groq, Cerebras, HuggingFace, NVIDIA NIM, Google Gemini, Cloudflare Workers AI) by matching the first URL path segment to provider keys, forwards requests unchanged without wire-protocol translation, and provides token-based authentication, rate limiting, and optional hot-reload of TOML configuration.

## How it actually works

**Architecture**: Single Python daemon (FastAPI + uvicorn) running on its own port. It installs via pip, runs via `lmrelay serve` (background) or `lmrelay run` (foreground). On Linux/macOS it integrates with systemd --user (Linux) or launchd (macOS) for auto-start; Windows runs foreground only. Stores state in `~/.lmrelay/state.json` and config in `~/.lmrelay/lmrelay.toml` (or `./lmrelay.toml` when run from checkout, already gitignored).

**Request flow**:
1. Client sends HTTP request to 11435 (e.g., `POST /openai/v1/chat/completions`)
2. First path segment ("openai") is extracted and looked up in `[upstream]` keys
3. If match found, that provider is selected; otherwise `default_upstream` is used
4. Path is forwarded to the selected provider unchanged (no rewriting)
5. Response streamed back unchanged (verified by streaming tests that ensure "caller has the first line before upstream has written the last")

**Error prefixing**: Every error lmrelay generates begins with "lmrelay:" so it is never mistaken for the provider. For recognized mismatches (e.g., Anthropic request path sent to OpenAI upstream), relay answers 400 with lmrelay's own error message, not the provider's confusing response.

## Multi-protocol on one port

**No translation layer**: Unlike flexrouter (which normalizes to OpenAI wire format internally), lmrelay **does not translate between protocols**. It forwards method, path, query string, and body bytes unchanged. This is its core design choice and constraint.

**Protocol detection via path prefix**: The protocol is determined by the URL path itself:
- `/api/*` → Ollama dialect (default)
- `/v1/*` → OpenAI dialect
- `/anthropic/v1/*` → Anthropic dialect (if upstream supports it)

The relay does not parse request bodies or headers to detect protocol; it trusts the path. This means:
- An OpenAI client can reach Ollama, OpenAI, DeepSeek, or Grok "by changing only the path prefix" (per README)
- An OpenAI-shaped request sent to an Anthropic provider will be forwarded as-is; if Anthropic rejects the malformed request, lmrelay relays the rejection
- No streaming translators, no tool-call reshaping, no message format normalization

**Implication**: lmrelay only works reliably when the upstream provider's wire protocol matches the client's dialect. Clients configured for OpenAI wire format work with any OpenAI-compatible upstream; they do not work with Anthropic-native upstreams unless the client itself speaks Anthropic protocol.

## Hot-reload

**Mechanism**: File-watching on `lmrelay.toml` or `.env`. On file mtime change, the daemon re-reads the TOML and optional environment file without restart.

**Command**: `lmrelay reload` (manual trigger to re-read config + .env without restart).

**What reloads**: Presumably `[upstream]` provider URLs, credentials, and per-key model allow-lists. UNVERIFIED whether rate-limit windows or penalty state are preserved across reload (flexrouter explicitly preserves these; lmrelay documentation does not state either way).

**In-flight requests**: No documentation found on how in-flight requests are handled during reload. Reasonable assumption: existing requests complete against old config; new requests use new config (typical for a stateless request handler with config mutated at the top level).

**Validation before swap**: UNVERIFIED. No evidence of pre-flight validation of new config before applying it. If TOML is malformed, behavior unknown (likely daemon crashes or ignores the reload).

## Free-vs-paid catalog

**Per-key model allow-lists**: Each provider key optionally takes a `models = [...]` list of fnmatch glob patterns (e.g., `"*:free"`, `"llama-3.1-*"`). The relay only routes a (provider, key, model) triple through this key if at least one pattern matches the resolved model ID.

**Use case framing**: "One key has a quota only for a model family, or different keys belong to different paid sub-accounts." Not explicitly framed as "free-vs-paid catalog toggle" in the research; the feature is **model allow-lists per API key**, which can be used to model free-vs-paid distinctions if the provider includes `:free` or `:paid` in model IDs (e.g., OpenRouter does: `meta-llama/llama-3.1-70b-instruct:free`).

**Multi-key rotation**: Multiple API keys per provider rotate round-robin. A key that returns 429 is skipped immediately for that request.

**Catalog management**: UNVERIFIED how catalogs are kept current. flexrouter has a `flexrouter refresh` command to rediscover models; lmrelay documentation does not mention equivalent. Unclear if lmrelay has a model-discovery mechanism or if users must manually update the config with model IDs from each provider.

## Server vs library

**lmrelay is a server; flexrouter is (today) a library**.

**Advantages inherent to server shape**:
1. **Single source of truth for rate-limit state**: One daemon owns the RPM/TPM windows, so two concurrent clients do not both believe they have the full budget.
2. **Daemon lifecycle**: Stops and starts independently of any application; survives app restarts; can be restarted without restarting dependent apps.
3. **Multi-client multiplexing**: HTTP port handles many clients at once (FastAPI async); library shape required one worker per request historically (PLAN.md: "can only do one thing at a time").
4. **Shared visibility**: All providers' health, rate-limit state, and penalties visible via daemon's own state files or logs; no per-process silos.
5. **Zero-copy wire proxying**: No need to parse/reshape request/response bodies; forward bytes unchanged (nginx-level efficiency).

**Advantages independent of shape**:
- Path-prefix routing is simpler to implement than semantic model scoring (lmrelay) vs. tier-based scoring (flexrouter).
- Simpler config (TOML with provider keys + optional allow-lists) than flexrouter's hand-written tier/model/score matrix.

## What it does better than flexrouter

| Feature | lmrelay | Difficulty to adopt |
|---------|---------|-----|
| **Wire-protocol passthrough** | No translation; forwards bytes unchanged. Avoids tool-call reshaping bugs, reasoning token handling, streaming frame corruption. | Medium — flexrouter's architecture assumes OpenAI-shaped internal representation; supporting passthrough requires separate code path for Anthropic, DeepSeek, etc. |
| **Simpler client compatibility** | OpenAI/Anthropic/Ollama clients work by changing only the path prefix; no proxy-awareness needed. | Low — just document the path prefixes. |
| **Per-key model allow-lists** | Via fnmatch globs; enables per-account resource isolation without config changes. | Low — `models` field on each key. |
| **Smaller state surface** | Token list + per-key model allow-lists. No scoring, no tier matrix, no vision flags per model. | Low — simpler to reason about, easier to configure. |
| **Streaming correctness** | Tested explicitly; aware of proxy buffering pitfalls (nginx's `proxy_read_timeout`). | Medium — flexrouter's streaming has tool-call accumulation, reasoning token handling; lmrelay just forwards frames. |
| **Launchd/systemd integration** | First-class support for daemon auto-start. | Low — flexrouter v2 server doesn't expose auto-start yet. |

## What flexrouter does better

| Feature | flexrouter | Notes |
|---------|-----------|-------|
| **Model scoring and tier grouping** | Tiers are semantic groupings; within a tier, models are ranked 1-100. Score-based selection with jitter-within-20% to avoid thundering herd. | lmrelay has no equivalent; selection is path-prefix based, not score-based. |
| **Active health checking and quarantine** | Models penalized for 429/5xx/401/402/404; deleted models quarantined 24h; deleted providers sidelined. Exponential backoff on penalty. | lmrelay uses passive health checking (errors discovered from user-facing requests). Cooldown matrix mentioned but mechanism UNVERIFIED. |
| **Rate-limit awareness before routing** | Models exceeding RPM/TPM are skipped proactively; request sleeps until a slot opens (or raises RouterBusy). No wasted calls. | lmrelay does not filter by rate limits; 429 responses trigger key skip but cost tokens. |
| **Cost tracking and per-provider daily budgets** | audit.csv logs every request cost; daily caps enforced per provider. | lmrelay has no cost model. |
| **Vision capability awareness** | Models tagged with `vision: true` are used only for vision requests; requests can auto-detect images and adjust tier. | lmrelay has no vision flag. |
| **Session stickiness** | `session_id` parameter pins a conversation to one model for consistency. | lmrelay has no session concept. |
| **Structured reasoning, tool_call_delta events** | Stream events for reasoning text, tool calls by ID (not index). Streaming events distinguish reasoning from content. | lmrelay forwards frames unchanged; client sees raw provider streams. |
| **Hot-reload with state preservation** | Rate-limit windows, penalty state, session pins survive config reload. | lmrelay: UNVERIFIED whether state is preserved. |
| **Dashboard with debugging detail** | Live tier/model health, request log, cost accounting, model discovery. | lmrelay: no dashboard mentioned. |
| **Config validation on save** | Dashboard tests each key on save, shows which models are reachable. | lmrelay: no equivalent. |

## Ideas worth stealing for v2

### Ranked by priority and adoption cost

1. **Path-prefix routing as fallback (Medium cost)**
   - Add a `server_endpoints` section to config mapping paths like `/openai/*` to specific provider+model combos
   - Allows simple CLI tools and notebook use cases that do not want to learn tier names
   - Complements tier-based routing; does not replace it
   - Example: `POST /openai/v1/chat/completions` → routes to best OpenAI-compatible provider in `openai_compat` tier

2. **Per-key model allow-lists with fnmatch (Low cost)**
   - Extend `providers[name].api_keys` to include optional `models: [...]` list
   - On each request, check `models` patterns against the intended model ID
   - Enables multi-account resource isolation without config duplication
   - Already sketched in flexrouter config; just needs implementation

3. **Launchd/systemd integration in CLI (Low cost)**
   - `flexrouter daemon install` creates systemd --user unit or launchd plist
   - `flexrouter daemon enable` activates it
   - `flexrouter daemon logs` tails journalctl or launchd logs
   - Makes flexrouter v2 server as easy to deploy as lmrelay

4. **Streaming frame forwarding fallback (Medium cost)**
   - When a provider's response dialect matches the client's requested dialect exactly, forward SSE/streaming frames unchanged
   - Avoids tool-call reshaping bugs, reasoning token corruption
   - Requires client to opt-in via config or header (e.g., `X-Forward-Streams: true`)
   - Useful for "I trust this provider to speak my dialect; just proxy it"

5. **Config auto-discovery of available models (Medium cost)**
   - `flexrouter models discover --provider groq` calls `/v1/models` on the provider and outputs YAML for tiers
   - Not "live catalog sync" (that's v3+); just a one-off discovery tool
   - Helps populate tiers without manual model ID hunting

6. **Request deduplication / "collapse" when multiple clients ask for the same model simultaneously (High cost, high payoff)**
   - If two requests arrive for the same (tier, model) within 10ms, queue the second to wait for the first's result
   - Share tokens, latency, and response
   - Reduces cost when dashboard and app ask the same tier at the same time
   - Requires careful state management and timeout logic; lmrelay's passthrough does not address this, but worth noting for v2

## Traps / things NOT to copy

1. **No wire-protocol translation** — lmrelay's biggest limitation is its strength. Do not copy the assumption that all clients speak the same protocol as their upstream. flexrouter's internal OpenAI-shaped format is correct for a router; the cost is tool-call/reasoning reshaping complexity, which lmrelay avoids by not reshaping.

2. **UNVERIFIED hot-reload state preservation** — lmrelay documentation does not state whether rate limits or penalty state survive reload. Do not copy this without testing. flexrouter's `update_config()` method on RoutingEngine explicitly preserves windows and penalties; be equally explicit.

3. **No cost model** — lmrelay ignores cost entirely. This is fine for "one free tier per provider" use cases but breaks the moment users want to track spend or enforce budgets. flexrouter's audit.csv + daily cap system is worth keeping.

4. **Fnmatch model allow-lists without model discovery** — Clever but requires manual ID entry. If you copy `models: [...]` filtering, provide a `flexrouter models list <provider>` command to inspect what IDs are actually available, else users will misconfigure it.

5. **No session concept** — lmrelay has no sticky sessions. For multi-turn conversations, this is a gap. Do not remove flexrouter's `session_id` stickiness; enhance it if anything.

6. **Do not implement "hotreload via polling" instead of file-watching** — lmrelay's README mentions "reload" command and TOML watching but does not specify mechanism. File-watching (fsnotify or watchfiles) is correct; polling is inefficient. Be explicit in v2 code.

7. **Do not merge credentials into config YAML** — flexrouter's current flexrouter.yaml has hardcoded keys (a security anti-pattern found during research). lmrelay stores tokens separately in state.json (mode 0600). flexrouter v2 should do the same: env-var references in TOML, actual keys in a separate file with restricted permissions.

## Evidence

**Primary sources (read)**:
- [lmrelay PyPI page](https://pypi.org/project/lmrelay/) — package metadata
- [lmrelay GitHub repository](https://github.com/wachawo/lmrelay) — main project page
- [lmrelay README.md](https://raw.githubusercontent.com/wachawo/lmrelay/main/README.md) — full feature description, path-based routing, streaming behavior, rate limits, token CLI
- flexrouter README.md, PLAN.md, engine.py, _router.py, config.py, flexrouter.yaml — current architecture and config

**Secondary sources (web search)**:
- General LLM failover patterns, circuit breaker implementations, streaming correctness (nginx buffering)
- OpenRouter's free-vs-paid model naming (e.g., `llama-3.1-70b:free`)

**Unverified claims**:
- "lmrelay hot-reload preserves rate-limit windows" — documented as "lmrelay reload" command exists, but state preservation mechanism not confirmed in README or code
- "lmrelay uses exponential cooldown backoff" — mentioned in initial research summaries, but not confirmed in README; circuit-breaker patterns vary
- "lmrelay has active health probing" — research showed passive probing (errors from user-facing requests); no scheduled probe endpoint found
- lmrelay's specific behavior on config validation failure during reload (crash vs. ignore)

---

## Comparison summary table

| Aspect | lmrelay | flexrouter v2 (target) |
|--------|---------|--------|
| **Shape** | Server daemon (FastAPI) | Server daemon (FastAPI) |
| **Config** | TOML + tokens file | YAML tiers + env-var keys |
| **Routing** | Path-prefix → provider | Score-based within tier |
| **Protocol handling** | Passthrough unchanged | Normalized to OpenAI shape, reshape on output |
| **Model filtering** | Per-key fnmatch globs | Per-model vision flag, context-window fit |
| **Health model** | Passive (errors only) | Active quarantine + exponential backoff |
| **Rate-limit enforcement** | After-the-fact (429 skip) | Before-the-fact (skip if full) |
| **Cost tracking** | No | Per-request audit.csv |
| **Session support** | No | Yes (session_id stickiness) |
| **Dashboard** | No | Yes (6 tabs: telemetry, chat, logs, account, settings, setup) |
| **Streaming** | Passthrough, tested | Shaped (streaming events, tool_call_delta) |
| **Complexity (user perspective)** | Low (path prefix, list keys) | Medium (understand tiers, scoring) |
| **Complexity (implementation)** | Low | High (state machines, reshaping) |

