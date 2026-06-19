# Model Discovery & Rate-Limit Gauging (Backend)

**Date:** 2026-06-19
**Status:** Approved design, ready for implementation planning
**Scope:** Backend only. Frontend UI (refresh button + diff panel) is deferred to
the in-progress dashboard overhaul; this spec delivers the endpoints and data it
will consume.

## Summary

Stop hand-maintaining volatile provider data in `flexrouter.yaml`. Add a manual
**model refresh** that re-discovers models and real rate limits from each
provider and rewrites the config (with backup + a visible diff), and a
**rate-limit gauging** layer that captures live headroom from response headers so
the router proactively avoids models that are guaranteed to 429.

## Motivation

Providers constantly change their available models and limits. Today the model
list, `rpm`, `tpm`, and `context_window` are hand-typed in `flexrouter.yaml` —
they rot, and bad entries (wrong-modality models, wrong context windows) cause
opaque runtime failures. Two existing assets are underused:

- `onboard.py` already has `discover_models`, `discover_ollama`,
  `_context_window`, `score_with_aa`, and `build_yaml` — but only wired into
  one-time interactive onboarding.
- `client.py` already captures `x-ratelimit-limit-requests`/`-tokens` into
  `RateLimitStore` — but only the ceiling, not real-time headroom, and the engine
  doesn't use provider-reported exhaustion.

## Decisions (locked during brainstorming)

1. **Config storage:** refresh **rewrites `flexrouter.yaml` in place** (not a
   separate runtime registry). Rewrites must be obvious on the dashboard.
2. **Trigger:** **manual only** — `flexrouter refresh` CLI command and a
   dashboard button (button is frontend, deferred). No background/scheduled
   rewrites.
3. **Rate-limit gauging:** capture **ceiling + live headroom**. Ceiling
   (`limit`) is written to config on refresh; headroom (`remaining`, `reset`)
   stays in the runtime store and drives proactive skipping. No active probing.
4. **Preservation:** **full regenerate** like `onboard` — reuse `build_yaml`,
   free models → `default` tier, paid → `paid` tier, AA re-score (or 50). API
   keys preserved exactly.

## Non-Goals

- No active probing of providers to measure limits (burns free-tier quota).
- No background/scheduled refresh.
- No React/frontend code. Endpoints + persisted data only.
- No change to how AA scoring works (reuse `score_with_aa` as-is).

## Architecture Overview

```
                manual trigger (CLI / future dashboard button)
                                │
                                ▼
flexrouter/refresh.py  ──►  discover models (onboard.discover_*)
  refresh_config()          + modality filter
                            ──►  score_with_aa
                            ──►  backup flexrouter.yaml
                            ──►  diff old vs new
                            ──►  build_yaml → write flexrouter.yaml
                            ──►  state_dir/last_refresh.json

per request:
AsyncClient.chat ──► parse limit + remaining + reset headers
                 ──► RateLimitStore.update / update_headroom (runtime)
RoutingEngine ──► RateLimitStore.is_exhausted() gates candidates
```

## Component 1: Model Refresh

### New module `flexrouter/refresh.py`

```python
@dataclass
class RefreshResult:
    timestamp: str
    added: list[str]            # "provider/model"
    removed: list[str]
    changed: list[dict]         # {"model","field","old","new"}
    backup_path: str
    provider_errors: list[dict] # {"provider","error"}

def refresh_config(config_path: str, state_dir: str) -> RefreshResult: ...
```

Flow:
1. Load current `flexrouter.yaml`; extract provider keys (preserved verbatim) and
   the set of currently-configured `provider/model` keys + their rpm/tpm/context.
2. For each configured provider with a key, run `onboard.discover_models`
   (which applies `free_filter`) **then a new modality filter** that drops
   non-chat models by name pattern. Collect per-provider failures into
   `provider_errors` instead of raising.
3. Re-score via `score_with_aa` (uses `AA_API_KEY`; falls back to score 50).
4. Backup the existing file to `state_dir/backups/flexrouter-<UTC-timestamp>.yaml`
   (created before any write).
5. Compute the diff: `added` (new keys), `removed` (gone keys), `changed`
   (rpm/tpm/context_window differences for surviving keys).
6. Rewrite `flexrouter.yaml` via `onboard.build_yaml` with preserved keys.
7. Write `state_dir/last_refresh.json` = serialized `RefreshResult`.

### Modality filter (shared, DRY)

Extract the non-chat name patterns into a single shared constant reused by both
`config.validate_config` (dashboard work) and refresh:

```python
# config.py
NON_CHAT_PATTERNS = ("whisper", "tts", "orpheus", "image", "lyria", "guard",
                     "native-audio", "-live", "embedding", "rerank")

def is_probably_chat_model(model_id: str) -> bool:
    mid = (model_id or "").lower()
    return not any(p in mid for p in NON_CHAT_PATTERNS)
```

`refresh.py` filters discovered models through `is_probably_chat_model`.

### CLI

Add `refresh` to `cli.py`: calls `refresh_config`, prints a summary
(`+N added, -M removed, K changed`, the backup path, and any provider errors).
Exit non-zero only if the rewrite itself failed (discovery errors are warnings).

### Backup safety

The backup is taken and flushed before the new config is written. If
`build_yaml`/write raises, the original `flexrouter.yaml` is untouched and the
backup remains. Keys are read from the parsed config and re-emitted by
`build_yaml`, never logged.

## Component 2: Rate-Limit Gauging

### `RateLimitStore` extension

Per-model entry grows to:

```
{ "rpm": int, "tpm": int,                       # ceiling (existing)
  "remaining_requests": int, "remaining_tokens": int,
  "reset_requests_at": float, "reset_tokens_at": float }  # epoch seconds
```

New methods:
- `update_headroom(provider, model, remaining_requests, remaining_tokens, reset_requests_at, reset_tokens_at)` — best-effort, ignores `None` fields.
- `is_exhausted(provider, model) -> bool` — true when a remaining counter is
  `<= 0` and `now < corresponding reset_at`.
- `available_at(provider, model) -> float | None` — earliest reset among
  exhausted dimensions, else `None`.

Existing `update`, `get_rpm`, `get_tpm` unchanged. Persistence remains
`state_dir/rate_limits.json`.

### `client.py` extension

After a successful response, in addition to the existing `limit` capture:
- Parse `x-ratelimit-remaining-requests`, `x-ratelimit-remaining-tokens`.
- Parse `x-ratelimit-reset-requests`, `x-ratelimit-reset-tokens` via a new
  `_parse_duration_ms(str) -> int | None` (ports modelrelay's `parseDurationMs`:
  handles plain seconds, `"45s"`, `"1m30s"`, `"12ms"`), converting to an epoch
  `reset_at = now + ms/1000`.
- Call `store.update_headroom(...)`. All parsing is best-effort; a malformed
  header degrades that field to `None` and never raises.

### Engine integration

`RoutingEngine._score_candidates` and `_model_available` gain one gate: if
`self._rate_limit_store and self._rate_limit_store.is_exhausted(provider, model)`,
the model is skipped. `seconds_until_available` includes
`available_at(provider, model) - now` for exhausted models so `wait=True` sleeps
the right amount.

## Dashboard Endpoints (data only; UI deferred)

In `dashboard/api.py` + `dashboard/server.py`:
- `GET /api/refresh` → contents of `state_dir/last_refresh.json` (or
  `{"timestamp": null}` if never run).
- `POST /api/refresh` → runs `refresh_config(discover_config(), state_dir)`,
  returns the `RefreshResult`. (The future dashboard button calls this.)

## Error Handling Philosophy

Consistent with the dashboard overhaul's "never hide errors": discovery failures
surface in `provider_errors` (CLI output + `/api/refresh`), header-parse failures
degrade per-field, and the config backup guarantees a recoverable state.

## Testing Strategy

- **`refresh.py`** (httpx mocked via monkeypatch on `onboard.discover_models`):
  diff computation (added/removed/changed), key preservation, modality filter
  excludes junk, AA fallback to 50, backup file created, `provider_errors`
  populated on discovery failure, `last_refresh.json` written.
- **`RateLimitStore`**: `update_headroom` round-trip + persistence;
  `is_exhausted` true when remaining 0 before reset, false after reset;
  `available_at` returns earliest reset.
- **`client.py`**: `_parse_duration_ms` cases (`"45s"`, `"1m30s"`, `"12ms"`,
  plain int, garbage→None); `update_headroom` invoked with parsed values.
- **`engine.py`**: exhausted model excluded from candidates;
  `seconds_until_available` reflects reset.
- **`cli.py`**: `refresh` command smoke test (mocked `refresh_config`) prints
  summary and exits 0.

## Open Risks

- **Branch dependency:** the shared `NON_CHAT_PATTERNS`/`is_probably_chat_model`
  helper lives in `config.py`, which the dashboard-backend branch also edits
  (`validate_config`). Implement on a branch based off `feat/dashboard-backend`
  to avoid a merge conflict, and have `validate_config` consume the shared
  constant.
- **Header coverage:** Google's OpenAI-compat shim omits rate-limit headers, so
  gauging is a no-op there — the sliding-window estimate remains the fallback.
  Acceptable and expected.
