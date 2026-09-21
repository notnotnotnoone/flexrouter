# flexrouter v2 — Stage 4: Per-Key State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. The owner explicitly asked for subagent-driven development and explicitly asked not to be consulted for input through the end of Stage 7 — rule on every ambiguity yourself and record it, exactly as Stage 3 did.

**Goal:** Replace blind `counter % len(keys)` key rotation with real per-key state (`state/key_state.json`, survives restart): a rejected key benches only itself, not the whole provider; a rate-limited key cools down instead of re-entering rotation immediately; four selectable scheduling strategies (`most_headroom`, `round_robin`, `fastest`, `weighted`) pick between a provider's live keys.

**Architecture:** `engine.py`'s model-level selection is not touched at all — not even an additive field on `RouteResult`. `LocalRouter` calls `engine.select()` exactly as before, then runs the returned `RouteResult` through a new key-selection layer (`flexrouter/key_state.py` + `flexrouter/scheduler.py`) that overrides `route.api_key` via `dataclasses.replace()` on the field that already exists, and tracks the chosen key's id as a separate loop-local variable (mirroring how Stage 3 tracked `attempts`/`skipped` outside of anything `engine.py` knows about). When every key for a provider is currently unavailable, the key layer signals that back to `engine.py` through primitives it already reads — `PenaltyBox.penalize_short()` for a cooling-dominated wait, `PenaltyBox.quarantine_provider()` for the case every key is benched — the same "feed it through an interface it already consults" pattern Stage 2 used for pinning and Stage 3 used for `skipped`.

**Tech Stack:** Python 3.11+, pytest, `dataclasses`, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-18-flexrouter-v2-design.md` (§5, "Per-key state")

**Roadmap:** `docs/superpowers/plans/2026-09-18-v2-roadmap.md` (Stage 4)

**Previous stage:** `docs/superpowers/plans/2026-09-19-v2-stage3-request-trace.md`

## Global Constraints

- Python floor is `>=3.11`. Do not raise it.
- **Nothing in this codebase may write `config.yaml`.** This stage writes only to `state/key_state.json`.
- **Secrets are never returned by any HTTP endpoint, printed by any CLI command, or written to any file besides `keys.json` itself.** `state/key_state.json` stores `provider:key_id` and behavioral counters only — never a secret value. `key_id`, once it starts appearing in `state/traces.jsonl` (Stage 3 already reserved the field, always `None` until now), is an identifier, not a credential — confirm this by construction: `KeyRecord.id` is never the same string as `KeyRecord.secret` anywhere in this codebase (`flexrouter/keys.py`).
- `FLEXROUTER_HOME` overrides the home root everywhere, with no exceptions.
- **Do not touch `engine.py`, `recovery.py`, `window.py`, `quota.py`, `rate_limits.py`, `errors.py`, `client.py`.** Every one of this stage's requirements is achievable without editing any of them — see the Rulings below for exactly how. If a task seems to need an edit to one of them, stop and say so rather than editing it.
- **`RouteResult` (defined in `engine.py`) gains no new field.** Key identity is carried as a separate value alongside `route`, never attached to it. `dataclasses.replace(route, api_key=...)` is the only mutation ever applied to a `RouteResult`, and it only ever touches the `api_key` field that already exists.
- **`audit.csv` is retained unchanged.** Do not remove or alter any `self._audit.log(...)` call.
- Tests: `pytest`, `asyncio_mode = "auto"` already set. **Do not use `respx` together with FastAPI's `TestClient`** — they collide in this repo. Endpoint tests monkeypatch `flexrouter.client.AsyncClient.chat` / `.stream_chat` instead.
- **Every `ModelConfig` fixture needs `rpm=60, tpm=60000, context_window=<something big>` explicitly** — no defaults exist for `rpm`/`tpm`.
- **Selection tests that need a specific model chosen must use widely separated scores (99 vs 40).**
- **Run pytest synchronously.** Bash tool, `run_in_background` unset (never `true`), `timeout: 600000`. Never background pytest and wait for a notification — this has stalled multiple prior implementers on this project.
- `tests/conftest.py` has an autouse fixture pointing `FLEXROUTER_HOME` at a temp directory. Do not remove it.
- **README.md currently holds an unsaved rewrite that is not from any agent.** Do not stage it, do not revert it, do not touch it. Never `git add -A` or `git add .` — name files explicitly.
- **If you ever see a file you're editing change content you didn't just write yourself, stop and report it immediately, do not assume your own edit didn't take.** A prior stage on this project had an abandoned, unstopped background agent mutate files concurrently with active work — always confirm no other agent should be touching your files.
- The suite must finish at **645 passed, 1 skipped or better** with no ignore flags: `python -m pytest -q`.

## Rulings made while writing this plan

Record these as ADR 0011 in the final task. Do not ask the owner about any of these — rule, record, and continue, exactly as every prior stage did.

1. **Key identity is never attached to `RouteResult`.** `engine.py`'s `_make_result` already builds a `RouteResult` with *some* `api_key` string (via its own unmodified, now-vestigial `counter % len()` rotation over `provider_cfg.api_keys`). `LocalRouter` immediately overrides `route.api_key` with `dataclasses.replace(route, api_key=chosen.secret)` after running the real key-selection layer, exactly the same technique `_build_pin_engine` already uses on `FlexConfig`. The chosen key's id travels as a plain local variable (`key_id`) through the retry loop, the same way `attempts`/`skipped` already do. **Cost:** engine.py's own key rotation still runs on every call, picking a value that is immediately discarded and overwritten. This is accepted rather than disabling it, because disabling it would mean editing `_make_result`, and the wasted `counter % len()` computation has no observable cost.
2. **`bad_key` (401/403) now benches the specific key, not the whole provider — with a documented fallback.** `_handle_auth_failure` changes from always calling `self._penalties.quarantine_provider(...)` to: bench the one key that was actually used (`state/key_state.json`, status `benched`, no auto-expiry — matches spec: "do not retry"); only call `quarantine_provider` when, after benching, **every** enabled key configured for that provider is now `benched` or `disabled`. This matches the spec's own wording exactly ("Provider-wide quarantine is reserved for needs_payment, and for the case where every key at a provider is benched") and is the whole point of this stage — it is not a deviation, it is the requirement.
3. **A provider with no keys configured at all (`ProviderConfig.keys == []`, e.g. local Ollama) skips the key layer entirely.** `route.api_key` (already `""` from `engine.py`) passes through untouched, `key_id` stays `None` throughout — identical to today's behavior for keyless providers.
4. **When every key for a provider is momentarily unavailable (some/all cooling, none benched), the key layer calls `self._penalties.penalize_short(route.provider, route.model, remaining_seconds)`** — the exact remaining time until the earliest key's cooldown ends, floored at 30s per spec — so `engine.py`'s own, unmodified `_skip_reason`/`seconds_until_available` (already consulted by the retry loop) correctly skip this model or sleep the right amount, entirely through interfaces `engine.py` already reads. **When every key is `benched`/`disabled` (none cooling, none live), the key layer calls `self._penalties.quarantine_provider(...)` instead** — there's no recovery time to wait out, matching ruling 2's fallback condition exactly (they're the same check, reached from two different call sites: the request path when it discovers total exhaustion mid-flow, and `_handle_auth_failure` when a bench is what caused it).
5. **`most_headroom` (the default strategy) is defined as: fewest `active_requests`, tied-broken by lowest `tokens_today`.** The spec names this strategy but gives no formula, and no per-key rpm/tpm cap exists anywhere in the schema (`KeyRecord` has no rate limit fields — those live on `ModelConfig`, which is per-model, not per-key) to compute a literal "headroom" fraction against. This is the most defensible reading of "the key with the most room left" available from the fields the schema actually has.
6. **`fastest` uses an exponential moving average of `KeyState.ema_latency_ms`** (new field, not in the spec's worked example — the spec names the strategy without specifying its storage), updated on every completed attempt (success or failure alike — a slow failure is still evidence the key is slow right now) with `alpha=0.3`. A key with no recorded latency yet (`ema_latency_ms is None`) sorts as if it had the best possible latency, so a brand-new key gets tried at least once rather than being permanently passed over by keys with an established fast track record.
7. **`requests_today` / `tokens_today` / `failures_24h` reset on UTC calendar-day rollover, not a true rolling 24-hour window.** This is the same simplification `flexrouter/budget.py`'s `DailyBudget` already makes elsewhere in this codebase (compare `_maybe_reset`), just persisted instead of in-memory. A true rolling window needs its own `SlidingWindow`-shaped structure (`flexrouter/window.py`, frozen) for no benefit this stage's own logic needs — every field this ruling covers is informational bookkeeping for a future dashboard (Stage 8), not something Stage 4's own selection or backoff logic reads.
8. **No per-key budget/quota enforcement in this stage.** `requests_today`/`tokens_today` are recorded, never checked against a cap — the spec's worked example shows these fields but never says a request should be *refused* because of them, and adding enforcement the spec doesn't ask for would be scope creep. If a future stage wants this, `KeyStateStore` already has the numbers.
9. **`disabled` is accepted in the status schema but never written by any of this stage's own code.** A key the owner has switched off in `keys.json` (`KeyRecord.enabled = False`) is already filtered out before it ever reaches `ProviderConfig.keys` (`config.py`'s `resolve_keys`), so it never reaches the key-selection layer at all — there is nothing for `disabled` to represent yet. The state is reserved for a future dashboard action ("disable this key at runtime without touching `keys.json`"), and `KeyStateStore` and the `Scheduler` protocol both already treat it identically to `benched` (unavailable, no auto-recovery) so adding a writer for it later needs no further change here.
10. **Concurrency cap gets a new setting, `settings.key_concurrency_cap`, default `4`.** The spec says "saturated keys (`active_requests` ≥ cap) are skipped" without naming the cap. `4` is a conservative default matching typical free-tier concurrent-request limits, configurable per the owner's actual provider limits once he has them.
11. **`active_requests` measures concurrency up to the first response or failure, not full stream duration.** For a streamed answer, incrementing at selection and decrementing only after the whole stream finishes would require threading key-state bookkeeping through the carefully-finished post-commit code from Stage 3's Task 4 (`_events_for`, the tool-call assembly, the empty-completion branches) for a benefit — capping *sustained* per-key concurrency — the spec's own wording ("saturated keys are skipped") doesn't ask for. A provider's actual concurrency limit is almost always enforced at connection/first-byte time, which this scope already covers. `begin_request`/`end_request` bracket only the `try`/`except .../finally` around `self._client.chat(...)` (non-streaming) and the `try`/`except .../finally` around `first_chunk = await stream.__anext__()` (streaming) — never the code after a stream commits.
12. **Rate-limit (429) now also cools the specific key that hit it**, in addition to the existing, unchanged model-level `self._engine.penalize(...)` call. The cooldown duration reuses `self._penalties.penalty_seconds(route.provider, route.model)` (already computed for the existing model-level penalty) as a stand-in for "the provider's stated reset time" — the decision layer that would give a real one (spec §4a's `too_fast` verdict, with the provider's actual reset header) is Stage 5, not built yet. This is a documented approximation, not a permanent design: Stage 5 should replace this stand-in with the real reset time once the decision layer exists.

---

## File Structure

| File | Responsibility |
|---|---|
| `flexrouter/key_state.py` | **new** — `KeyState` dataclass, `KeyStateStore`: load/save `state/key_state.json`, status transitions, daily rollover, `active_requests` bookkeeping. |
| `flexrouter/scheduler.py` | **new** — `Scheduler` protocol and the four strategy implementations; `allow_models` filtering and the concurrency-cap skip rule live here too, since every strategy needs them. |
| `flexrouter/config.py` | **modify** — `ProviderConfig.key_strategy: str = "most_headroom"`, `FlexConfig.key_concurrency_cap: int = 4`, parsed from settings. |
| `flexrouter/_router.py` | **modify** — `_pick_key` helper (shared by both request paths), `_handle_auth_failure` rewritten for key-level granularity, key-level cooling on rate-limit, `key_id` threaded into `attempts`/`answered_by`/trace in both `agenerate` and `agenerate_stream`. |

---

### Task 1: `KeyState` and `KeyStateStore`

**Files:**
- Create: `flexrouter/key_state.py`
- Test: `tests/test_key_state.py`

**Interfaces:**
- Consumes: `flexrouter.store.read_json`, `flexrouter.store.write_json`, `flexrouter.store.harden` (all pre-existing, unchanged).
- Produces:
  - `@dataclass class KeyState`: `status: str = "live"` (`"live" | "cooling" | "benched" | "disabled"`), `until: Optional[float] = None`, `reason: str = ""`, `consecutive_failures: int = 0`, `failures_24h: int = 0`, `requests_today: int = 0`, `tokens_today: int = 0`, `last_used_at: Optional[float] = None`, `active_requests: int = 0`, `ema_latency_ms: Optional[float] = None`, `day: str = ""` (ISO date the daily counters were last reset on — internal bookkeeping, still serialized so it survives restart).
  - `class KeyStateStore(state_dir: str)`:
    - `.get(provider: str, key_id: str) -> KeyState` — returns a fresh default `KeyState` (status `"live"`) if never seen before; does not persist a read-only default.
    - `.is_available(provider: str, key_id: str, now: Optional[float] = None) -> bool` — `True` for `"live"`; for `"cooling"`, `True` once `until` has passed (and transitions the stored state to `"live"` as a side effect, mirroring `PenaltyBox.is_penalized`'s auto-recovery pattern); `False` for `"benched"`/`"disabled"`.
    - `.seconds_until_available(provider: str, key_id: str, now: Optional[float] = None) -> float` — `0.0` if already available; for `"cooling"`, `max(0.0, until - now)`; for `"benched"`/`"disabled"`, `float("inf")` (never recovers on its own).
    - `.mark_success(provider: str, key_id: str, tokens: int, latency_ms: int, now: Optional[float] = None) -> None` — resets `consecutive_failures` to 0, sets `status="live"`, updates `requests_today`/`tokens_today` (after `_maybe_reset_day`), `last_used_at=now`, and folds `latency_ms` into `ema_latency_ms` (`0.3 * latency_ms + 0.7 * previous` if a previous value exists, else `latency_ms`).
    - `.mark_cooling(provider: str, key_id: str, seconds: float, reason: str, now: Optional[float] = None) -> None` — `status="cooling"`, `until=now + max(seconds, 30.0)`, `reason=reason`, increments `consecutive_failures` and `failures_24h` (after `_maybe_reset_day`).
    - `.mark_benched(provider: str, key_id: str, reason: str, now: Optional[float] = None) -> None` — `status="benched"`, `until=None`, `reason=reason`, increments `consecutive_failures` and `failures_24h`.
    - `.all_unavailable(provider: str, key_ids: list[str], now: Optional[float] = None) -> bool` — `True` only if `key_ids` is non-empty and every one of them is currently unavailable per `.is_available`.
    - `.all_benched_or_disabled(provider: str, key_ids: list[str], now: Optional[float] = None) -> bool` — `True` only if `key_ids` is non-empty and every one of them has status `"benched"` or `"disabled"` (none `"cooling"` — that distinguishes "wait it out" from "nothing will fix this on its own").
    - `.min_seconds_until_available(provider: str, key_ids: list[str], now: Optional[float] = None) -> float` — the smallest `.seconds_until_available` across `key_ids`; `float("inf")` if `key_ids` is empty or all are `benched`/`disabled`.
    - `.begin_request(provider: str, key_id: str) -> None` / `.end_request(provider: str, key_id: str) -> None` — `active_requests += 1` / `-= 1` (floored at 0 — `end_request` on a key whose count is already 0 is a no-op, not a negative count).
  - All mutating methods persist immediately via `store.write_json` (atomic replace, same guarantee every other state file in this codebase already has), keyed by `f"{provider}:{key_id}"` at the top level of `state/key_state.json`.
  - A private `_maybe_reset_day(state: KeyState, now: float) -> None` helper: if `state.day` differs from today's UTC date (`datetime.now(timezone.utc).date().isoformat()`), zero `requests_today`, `tokens_today`, `failures_24h` and set `state.day` to today — called at the top of every method that reads or writes those three fields, mirroring `flexrouter/budget.py`'s `DailyBudget._maybe_reset` pattern exactly, just against a persisted field instead of an in-memory one.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_key_state.py
import json
import time
from datetime import datetime, timedelta, timezone

from flexrouter.key_state import KeyState, KeyStateStore


def test_an_unseen_key_defaults_to_live(tmp_path):
    store = KeyStateStore(str(tmp_path))
    s = store.get("openrouter", "or-main")
    assert s.status == "live"
    assert store.is_available("openrouter", "or-main")


def test_mark_success_resets_failures_and_updates_counters(tmp_path):
    store = KeyStateStore(str(tmp_path))
    store.mark_cooling("openrouter", "or-main", 60, "too_fast")
    store.mark_success("openrouter", "or-main", tokens=120, latency_ms=340)
    s = store.get("openrouter", "or-main")
    assert s.status == "live"
    assert s.consecutive_failures == 0
    assert s.tokens_today == 120
    assert s.requests_today == 1
    assert s.ema_latency_ms == 340


def test_mark_cooling_floors_at_thirty_seconds(tmp_path):
    store = KeyStateStore(str(tmp_path))
    now = time.time()
    store.mark_cooling("openrouter", "or-main", 5, "too_fast", now=now)
    s = store.get("openrouter", "or-main")
    assert s.status == "cooling"
    assert s.until >= now + 30


def test_cooling_key_becomes_available_after_its_window(tmp_path):
    store = KeyStateStore(str(tmp_path))
    now = time.time()
    store.mark_cooling("openrouter", "or-main", 30, "too_fast", now=now)
    assert store.is_available("openrouter", "or-main", now=now) is False
    assert store.is_available("openrouter", "or-main", now=now + 31) is True
    s = store.get("openrouter", "or-main")
    assert s.status == "live"  # auto-recovered


def test_benched_key_never_recovers_on_its_own(tmp_path):
    store = KeyStateStore(str(tmp_path))
    now = time.time()
    store.mark_benched("openrouter", "or-main", "bad_key", now=now)
    assert store.is_available("openrouter", "or-main", now=now + 1_000_000) is False
    assert store.seconds_until_available("openrouter", "or-main", now=now) == float("inf")


def test_ema_latency_blends_toward_new_samples(tmp_path):
    store = KeyStateStore(str(tmp_path))
    store.mark_success("openrouter", "or-main", tokens=1, latency_ms=100)
    store.mark_success("openrouter", "or-main", tokens=1, latency_ms=1100)
    s = store.get("openrouter", "or-main")
    # 0.3*1100 + 0.7*100 = 400
    assert s.ema_latency_ms == 400


def test_daily_counters_reset_on_a_new_utc_day(tmp_path):
    store = KeyStateStore(str(tmp_path))
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    store.mark_success("openrouter", "or-main", tokens=500, latency_ms=10, now=yesterday.timestamp())
    s = store.get("openrouter", "or-main")
    assert s.tokens_today == 500

    store.mark_success("openrouter", "or-main", tokens=7, latency_ms=10)  # today, real time
    s = store.get("openrouter", "or-main")
    assert s.tokens_today == 7  # not 507 — yesterday's count did not carry over


def test_all_unavailable_true_only_when_every_key_is_down(tmp_path):
    store = KeyStateStore(str(tmp_path))
    now = time.time()
    store.mark_cooling("openrouter", "k1", 60, "too_fast", now=now)
    assert store.all_unavailable("openrouter", ["k1", "k2"], now=now) is False  # k2 unseen -> live
    store.mark_benched("openrouter", "k2", "bad_key", now=now)
    assert store.all_unavailable("openrouter", ["k1", "k2"], now=now) is True


def test_all_benched_or_disabled_is_false_if_any_key_is_only_cooling(tmp_path):
    store = KeyStateStore(str(tmp_path))
    now = time.time()
    store.mark_cooling("openrouter", "k1", 60, "too_fast", now=now)
    store.mark_benched("openrouter", "k2", "bad_key", now=now)
    assert store.all_benched_or_disabled("openrouter", ["k1", "k2"], now=now) is False
    assert store.all_unavailable("openrouter", ["k1", "k2"], now=now) is True


def test_min_seconds_until_available_picks_the_soonest(tmp_path):
    store = KeyStateStore(str(tmp_path))
    now = time.time()
    store.mark_cooling("openrouter", "k1", 90, "too_fast", now=now)
    store.mark_cooling("openrouter", "k2", 30, "too_fast", now=now)
    secs = store.min_seconds_until_available("openrouter", ["k1", "k2"], now=now)
    assert 29 <= secs <= 31


def test_begin_and_end_request_track_active_requests(tmp_path):
    store = KeyStateStore(str(tmp_path))
    store.begin_request("openrouter", "k1")
    store.begin_request("openrouter", "k1")
    assert store.get("openrouter", "k1").active_requests == 2
    store.end_request("openrouter", "k1")
    assert store.get("openrouter", "k1").active_requests == 1


def test_end_request_never_goes_negative(tmp_path):
    store = KeyStateStore(str(tmp_path))
    store.end_request("openrouter", "k1")
    assert store.get("openrouter", "k1").active_requests == 0


def test_state_survives_a_new_store_instance(tmp_path):
    store = KeyStateStore(str(tmp_path))
    store.mark_benched("openrouter", "k1", "bad_key")
    reloaded = KeyStateStore(str(tmp_path))
    assert reloaded.get("openrouter", "k1").status == "benched"


def test_no_secret_ever_appears_in_the_state_file(tmp_path):
    store = KeyStateStore(str(tmp_path))
    store.mark_benched("openrouter", "sk-should-not-appear-here", "bad_key")
    on_disk = (tmp_path / "key_state.json").read_text(encoding="utf-8")
    # The key_id itself is expected to appear (it's an identifier, not a
    # secret) — this test guards against a future change accidentally
    # writing a `secret` field into the state file.
    assert "secret" not in on_disk
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_key_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flexrouter.key_state'`

- [ ] **Step 3: Write the implementation**

```python
# flexrouter/key_state.py
"""Per-key runtime state: state/key_state.json, keyed by "provider:key_id".

Replaces blind counter % len(keys) rotation (fault 5 in the v2 design doc)
with real per-key memory that survives a restart: a rejected key benches
only itself, a rate-limited key cools down instead of re-entering rotation
immediately, and every key's recent behaviour (failures, throughput,
latency) is available to a Scheduler (flexrouter/scheduler.py) choosing
between a provider's live keys.

engine.py is not touched anywhere in this stage. This module and
flexrouter/scheduler.py are a layer LocalRouter runs *after* engine.py has
already picked a model, overriding only the api_key field a RouteResult
already has (see ADR 0011).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flexrouter.store import harden, read_json, write_json


@dataclass
class KeyState:
    status: str = "live"  # live | cooling | benched | disabled
    until: Optional[float] = None
    reason: str = ""
    consecutive_failures: int = 0
    failures_24h: int = 0
    requests_today: int = 0
    tokens_today: int = 0
    last_used_at: Optional[float] = None
    active_requests: int = 0
    ema_latency_ms: Optional[float] = None
    day: str = ""


def _today(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).date().isoformat()


class KeyStateStore:
    def __init__(self, state_dir: str) -> None:
        self._path = Path(state_dir) / "key_state.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._states: dict[str, KeyState] = {}
        for k, v in read_json(self._path, default={}).items():
            self._states[k] = KeyState(**v)

    def _key(self, provider: str, key_id: str) -> str:
        return f"{provider}:{key_id}"

    def get(self, provider: str, key_id: str) -> KeyState:
        return self._states.get(self._key(provider, key_id), KeyState())

    def _maybe_reset_day(self, state: KeyState, now: float) -> None:
        today = _today(now)
        if state.day != today:
            state.requests_today = 0
            state.tokens_today = 0
            state.failures_24h = 0
            state.day = today

    def _save(self) -> None:
        write_json(self._path, {k: asdict(v) for k, v in self._states.items()})
        harden(self._path)

    def is_available(self, provider: str, key_id: str, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        k = self._key(provider, key_id)
        state = self._states.get(k)
        if state is None or state.status == "live":
            return True
        if state.status == "cooling":
            if state.until is not None and now >= state.until:
                state.status = "live"
                state.until = None
                self._states[k] = state
                self._save()
                return True
            return False
        return False  # benched | disabled

    def seconds_until_available(self, provider: str, key_id: str,
                                now: Optional[float] = None) -> float:
        now = now if now is not None else time.time()
        if self.is_available(provider, key_id, now=now):
            return 0.0
        state = self.get(provider, key_id)
        if state.status == "cooling" and state.until is not None:
            return max(0.0, state.until - now)
        return float("inf")

    def mark_success(self, provider: str, key_id: str, tokens: int, latency_ms: int,
                     now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        k = self._key(provider, key_id)
        state = self._states.get(k, KeyState())
        self._maybe_reset_day(state, now)
        state.status = "live"
        state.until = None
        state.consecutive_failures = 0
        state.requests_today += 1
        state.tokens_today += tokens
        state.last_used_at = now
        state.ema_latency_ms = (
            latency_ms if state.ema_latency_ms is None
            else 0.3 * latency_ms + 0.7 * state.ema_latency_ms)
        self._states[k] = state
        self._save()

    def mark_cooling(self, provider: str, key_id: str, seconds: float, reason: str,
                     now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        k = self._key(provider, key_id)
        state = self._states.get(k, KeyState())
        self._maybe_reset_day(state, now)
        state.status = "cooling"
        state.until = now + max(seconds, 30.0)
        state.reason = reason
        state.consecutive_failures += 1
        state.failures_24h += 1
        self._states[k] = state
        self._save()

    def mark_benched(self, provider: str, key_id: str, reason: str,
                     now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        k = self._key(provider, key_id)
        state = self._states.get(k, KeyState())
        self._maybe_reset_day(state, now)
        state.status = "benched"
        state.until = None
        state.reason = reason
        state.consecutive_failures += 1
        state.failures_24h += 1
        self._states[k] = state
        self._save()

    def all_unavailable(self, provider: str, key_ids: list[str],
                        now: Optional[float] = None) -> bool:
        if not key_ids:
            return False
        now = now if now is not None else time.time()
        return all(not self.is_available(provider, kid, now=now) for kid in key_ids)

    def all_benched_or_disabled(self, provider: str, key_ids: list[str],
                                now: Optional[float] = None) -> bool:
        if not key_ids:
            return False
        return all(self.get(provider, kid).status in ("benched", "disabled")
                  for kid in key_ids)

    def min_seconds_until_available(self, provider: str, key_ids: list[str],
                                    now: Optional[float] = None) -> float:
        if not key_ids:
            return float("inf")
        now = now if now is not None else time.time()
        return min(self.seconds_until_available(provider, kid, now=now) for kid in key_ids)

    def begin_request(self, provider: str, key_id: str) -> None:
        k = self._key(provider, key_id)
        state = self._states.get(k, KeyState())
        state.active_requests += 1
        self._states[k] = state
        self._save()

    def end_request(self, provider: str, key_id: str) -> None:
        k = self._key(provider, key_id)
        state = self._states.get(k, KeyState())
        state.active_requests = max(0, state.active_requests - 1)
        self._states[k] = state
        self._save()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_key_state.py -q`
Expected: PASS, 14 tests.

- [ ] **Step 5: Commit**

```bash
git add flexrouter/key_state.py tests/test_key_state.py
git commit -m "feat(keys): a per-key state store that survives restart"
```

---

### Task 2: The `Scheduler` protocol and its four strategies

**Files:**
- Create: `flexrouter/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `flexrouter.key_state.KeyStateStore` (Task 1), `flexrouter.keys.KeyRecord`, `flexrouter.keys.allows` (both pre-existing, unchanged).
- Produces:
  - `pick_key(candidates: list[KeyRecord], provider: str, model_id: str, strategy: str, states: KeyStateStore, cap: int, now: Optional[float] = None) -> Optional[KeyRecord]` — the one function every caller needs. Internally: filters `candidates` to those `allows(record, model_id)`, then to those `states.is_available(provider, record.id, now=now)` and `states.get(provider, record.id).active_requests < cap`; if nothing survives, returns `None`; otherwise dispatches to the named strategy. An unknown `strategy` string falls back to `most_headroom` (never raises — a typo in settings should degrade gracefully, not break every request).
  - The four strategies as private functions, each `(live: list[KeyRecord], provider: str, states: KeyStateStore) -> KeyRecord`, never called with an empty list (the caller in `pick_key` already handled that): `_most_headroom`, `_round_robin`, `_fastest`, `_weighted`.
  - `class RoundRobinCounters`: a tiny `dict[str, int]`-backed helper (`.next(provider: str, n: int) -> int`) that `_round_robin` needs and that must be threaded in from outside (a module-level default instance is NOT used — see Interfaces below), because round-robin position has to survive across calls within one `LocalRouter` instance but must not leak between independent `RoutingEngine`/router instances in tests. `pick_key` takes an optional `counters: Optional[RoundRobinCounters] = None` parameter; when the strategy is `round_robin` and `counters` is `None`, raise `ValueError("round_robin strategy requires a RoundRobinCounters instance")` rather than silently using a shared global — a caller that forgot to pass one should find out immediately, not get subtly-wrong behavior under concurrent requests.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scheduler.py
import pytest

from flexrouter.key_state import KeyStateStore
from flexrouter.keys import KeyRecord
from flexrouter.scheduler import RoundRobinCounters, pick_key


def _keys(*ids, weight=1, allow=("*",)):
    return [KeyRecord(id=i, secret=f"secret-{i}", weight=weight, allow_models=list(allow))
            for i in ids]


def test_returns_none_when_no_candidates():
    store = KeyStateStore.__new__(KeyStateStore)  # not exercised, unused here
    assert pick_key([], "openrouter", "openrouter/m", "most_headroom",
                    KeyStateStoreStub(), cap=4) is None


class KeyStateStoreStub:
    """A minimal stand-in that satisfies pick_key's interface without touching disk."""
    def __init__(self):
        self._active = {}
        self._available = {}

    def is_available(self, provider, key_id, now=None):
        return self._available.get(key_id, True)

    def get(self, provider, key_id):
        from flexrouter.key_state import KeyState
        s = KeyState()
        s.active_requests = self._active.get(key_id, 0)
        return s


def test_unavailable_keys_are_filtered_out():
    store = KeyStateStoreStub()
    store._available["k1"] = False
    chosen = pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "most_headroom", store, cap=4)
    assert chosen.id == "k2"


def test_saturated_keys_are_filtered_out():
    store = KeyStateStoreStub()
    store._active["k1"] = 10
    chosen = pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "most_headroom", store, cap=4)
    assert chosen.id == "k2"


def test_allow_models_filters_before_scheduling():
    store = KeyStateStoreStub()
    keys = _keys("k1", allow=["other/*"]) + _keys("k2", allow=["*"])
    chosen = pick_key(keys, "openrouter", "openrouter/m", "most_headroom", store, cap=4)
    assert chosen.id == "k2"


def test_nothing_survives_filtering_returns_none():
    store = KeyStateStoreStub()
    store._available["k1"] = False
    chosen = pick_key(_keys("k1"), "openrouter", "openrouter/m", "most_headroom", store, cap=4)
    assert chosen is None


def test_most_headroom_prefers_fewer_active_requests():
    store = KeyStateStoreStub()
    store._active["k1"] = 3
    store._active["k2"] = 0
    chosen = pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "most_headroom", store, cap=4)
    assert chosen.id == "k2"


def test_round_robin_cycles_and_requires_counters():
    store = KeyStateStoreStub()
    with pytest.raises(ValueError):
        pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "round_robin", store, cap=4)

    counters = RoundRobinCounters()
    seen = [pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "round_robin",
                     store, cap=4, counters=counters).id for _ in range(4)]
    assert seen == ["k1", "k2", "k1", "k2"]


def test_fastest_prefers_lower_latency(tmp_path):
    store = KeyStateStoreStub()
    from flexrouter.key_state import KeyState
    fast, slow = KeyState(), KeyState()
    fast.ema_latency_ms, slow.ema_latency_ms = 100.0, 900.0

    class Store2(KeyStateStoreStub):
        def get(self, provider, key_id):
            return fast if key_id == "k1" else slow

    chosen = pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "fastest", Store2(), cap=4)
    assert chosen.id == "k1"


def test_fastest_treats_unmeasured_keys_as_best():
    store = KeyStateStoreStub()

    class Store2(KeyStateStoreStub):
        def get(self, provider, key_id):
            from flexrouter.key_state import KeyState
            s = KeyState()
            if key_id == "k2":
                s.ema_latency_ms = 50.0
            return s  # k1 has no measurement yet

    chosen = pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "fastest", Store2(), cap=4)
    assert chosen.id == "k1"


def test_weighted_never_picks_a_zero_weight_key_when_a_positive_one_exists():
    store = KeyStateStoreStub()
    keys = _keys("k1", weight=0) + _keys("k2", weight=5)
    for _ in range(20):
        chosen = pick_key(keys, "openrouter", "openrouter/m", "weighted", store, cap=4)
        assert chosen.id == "k2"


def test_unknown_strategy_falls_back_to_most_headroom():
    store = KeyStateStoreStub()
    store._active["k1"] = 3
    store._active["k2"] = 0
    chosen = pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "not_a_real_strategy", store, cap=4)
    assert chosen.id == "k2"
```

Implementer note: `KeyStateStoreStub` above only implements the two methods
`pick_key` actually calls (`is_available`, `get`) — check `flexrouter/key_state.py`'s
real `KeyStateStore` (Task 1) to confirm those are exactly the methods `pick_key`
needs and no others, before writing the implementation.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_scheduler.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flexrouter.scheduler'`

- [ ] **Step 3: Write the implementation**

```python
# flexrouter/scheduler.py
"""Choosing between a provider's live keys.

engine.py already chose *which model*; this module chooses *which key* to
use for it, given real per-key state (flexrouter/key_state.py) instead of
the blind counter % len(keys) rotation it replaces (spec §5, fault 5 in the
motivation section). Nothing here is called from engine.py — LocalRouter
calls pick_key() itself, after engine.select() has already returned
(ADR 0011).
"""
from __future__ import annotations

import random
from typing import Optional

from flexrouter.key_state import KeyStateStore
from flexrouter.keys import KeyRecord, allows


class RoundRobinCounters:
    """Per-provider cursor for the round_robin strategy.

    A plain dict would work too, but a caller reading `pick_key`'s signature
    should not be able to accidentally share position across two unrelated
    LocalRouter instances (e.g. two routers in the same test process) by
    forgetting to pass one in — see the ValueError this forces in pick_key.
    """
    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def next(self, provider: str, n: int) -> int:
        i = self._counters.get(provider, 0)
        self._counters[provider] = i + 1
        return i % n


def _most_headroom(live: list[KeyRecord], provider: str, states: KeyStateStore) -> KeyRecord:
    def sort_key(r: KeyRecord):
        s = states.get(provider, r.id)
        return (s.active_requests, s.tokens_today)
    return min(live, key=sort_key)


def _round_robin(live: list[KeyRecord], provider: str, states: KeyStateStore,
                 counters: RoundRobinCounters) -> KeyRecord:
    return live[counters.next(provider, len(live))]


def _fastest(live: list[KeyRecord], provider: str, states: KeyStateStore) -> KeyRecord:
    def sort_key(r: KeyRecord):
        s = states.get(provider, r.id)
        # No measurement yet sorts as the best possible latency, so a new
        # key gets tried at least once instead of being permanently passed
        # over by keys with an established fast track record.
        return s.ema_latency_ms if s.ema_latency_ms is not None else -1.0
    return min(live, key=sort_key)


def _weighted(live: list[KeyRecord], provider: str, states: KeyStateStore) -> KeyRecord:
    weights = [max(r.weight, 0) for r in live]
    if sum(weights) == 0:
        return random.choice(live)
    return random.choices(live, weights=weights, k=1)[0]


def pick_key(
    candidates: list[KeyRecord],
    provider: str,
    model_id: str,
    strategy: str,
    states: KeyStateStore,
    cap: int,
    now: Optional[float] = None,
    counters: Optional[RoundRobinCounters] = None,
) -> Optional[KeyRecord]:
    live = [
        r for r in candidates
        if allows(r, model_id)
        and states.is_available(provider, r.id, now=now)
        and states.get(provider, r.id).active_requests < cap
    ]
    if not live:
        return None
    if strategy == "round_robin":
        if counters is None:
            raise ValueError("round_robin strategy requires a RoundRobinCounters instance")
        return _round_robin(live, provider, states, counters)
    if strategy == "fastest":
        return _fastest(live, provider, states)
    if strategy == "weighted":
        return _weighted(live, provider, states)
    return _most_headroom(live, provider, states)  # default, and unknown-strategy fallback
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_scheduler.py -q`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add flexrouter/scheduler.py tests/test_scheduler.py
git commit -m "feat(keys): four scheduling strategies for choosing between live keys"
```

---

### Task 3: Configuration — per-provider strategy, global concurrency cap

**Files:**
- Modify: `flexrouter/config.py` (`ProviderConfig`, `FlexConfig`, `load_config`)
- Test: `tests/test_config.py` (add to the existing file)

**Interfaces:**
- Consumes: nothing new.
- Produces: `ProviderConfig.key_strategy: str = "most_headroom"`, `FlexConfig.key_concurrency_cap: int = 4`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py` (check the existing file's fixture helpers first —
reuse whatever pattern already builds a minimal on-disk config for `load_config`
tests there rather than inventing a new one):

```python
def test_provider_key_strategy_defaults_to_most_headroom(config_file):
    cfg = load_config(config_file)
    assert cfg.providers["groq"].key_strategy == "most_headroom"


def test_provider_key_strategy_is_read_from_settings(tmp_path, monkeypatch):
    import yaml
    from tests.conftest import MINIMAL_CONFIG
    cfg = dict(MINIMAL_CONFIG)
    cfg["settings"] = dict(cfg["settings"])
    cfg["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    cfg["providers"] = dict(cfg["providers"])
    cfg["providers"]["groq"] = dict(cfg["providers"]["groq"])
    cfg["providers"]["groq"]["key_strategy"] = "round_robin"
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    loaded = load_config(p)
    assert loaded.providers["groq"].key_strategy == "round_robin"


def test_key_concurrency_cap_defaults_to_four(config_file):
    cfg = load_config(config_file)
    assert cfg.key_concurrency_cap == 4


def test_key_concurrency_cap_is_read_from_settings(tmp_path, monkeypatch):
    import yaml
    from tests.conftest import MINIMAL_CONFIG
    cfg = dict(MINIMAL_CONFIG)
    cfg["settings"] = dict(cfg["settings"])
    cfg["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    cfg["settings"]["key_concurrency_cap"] = 8
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    loaded = load_config(p)
    assert loaded.key_concurrency_cap == 8
```

Implementer note: `config_file` is an existing fixture in `tests/conftest.py`
(check it before writing these — it already builds a minimal on-disk config
with a `groq` provider and a `GROQ_API_KEY` environment variable set). Use it
for the defaults tests; build a config inline (as shown above) only for the
tests that need a non-default value.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_config.py -k key_strategy -q` and
`python -m pytest tests/test_config.py -k key_concurrency -q`
Expected: FAIL — `AttributeError: 'ProviderConfig' object has no attribute 'key_strategy'` /
`'FlexConfig' object has no attribute 'key_concurrency_cap'`

- [ ] **Step 3: Write the implementation**

In `flexrouter/config.py`, add one field to each dataclass:

```python
@dataclass
class ProviderConfig:
    base_url: str
    api_keys: list[str]  # resolved secret strings, in selection order
    header_parser: str = "openai_compatible"
    keys: list[KeyRecord] = field(default_factory=list)
    key_strategy: str = "most_headroom"
```

```python
    key_concurrency_cap: int = 4
```

(add this line to `FlexConfig`, next to the other plain settings like
`sample_interval_seconds` — check the current field order in the file before
placing it, and put it in a sensible spot near the other numeric settings
rather than at the very end.)

In `load_config`'s provider-building loop, where `ProviderConfig(...)` is
currently constructed (find the exact call — it is right after
`records = resolve_keys(name, praw, vault, source)`), add the new field:

```python
        providers[name] = ProviderConfig(
            base_url=praw["base_url"],
            api_keys=[r.secret for r in records],
            header_parser=praw.get("header_parser", "openai_compatible"),
            keys=records,
            key_strategy=praw.get("key_strategy", "most_headroom"),
        )
```

And where `FlexConfig(...)` is constructed at the end of `load_config` (find
the line building `sample_interval_seconds=_number(settings, ...)` and add a
sibling line right after it):

```python
        key_concurrency_cap=_number(settings, "key_concurrency_cap", 4, int),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: 645+ passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/config.py tests/test_config.py
git commit -m "feat(config): per-provider key strategy and a global concurrency cap"
```

---

### Task 4: Wire key selection into `agenerate` (non-streaming)

This is the task that actually fixes fault 5. Read `flexrouter/_router.py`'s
current `agenerate` and `_handle_auth_failure` in full before starting — the
exact current content matters for this diff, more than any other task in
this plan.

**Files:**
- Modify: `flexrouter/_router.py` (`__init__`, `reload`, `_handle_auth_failure`,
  a new `_pick_key` method, `agenerate`)
- Test: `tests/test_key_selection_agenerate.py`

**Interfaces:**
- Consumes: `flexrouter.key_state.KeyStateStore` (Task 1), `flexrouter.scheduler.pick_key`,
  `flexrouter.scheduler.RoundRobinCounters` (Task 2), `ProviderConfig.key_strategy`,
  `FlexConfig.key_concurrency_cap` (Task 3).
- Produces: `LocalRouter._key_states: KeyStateStore`, `LocalRouter._round_robin: RoundRobinCounters`
  (both new attributes), `LocalRouter._pick_key(route: RouteResult) -> tuple[Optional[RouteResult], Optional[str]]`
  (new method — the second element is the chosen key's id, or `None` for a
  keyless provider or when nothing is available). `_handle_auth_failure`'s
  signature changes from `(self, route, exc)` to `(self, route, exc, key_id)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_key_selection_agenerate.py
import json

from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.exceptions import RouterError
from flexrouter.keys import KeyRecord


def _cfg(tmp_path, key_strategy="round_robin"):
    keys = [
        KeyRecord(id="k1", secret="secret-1", weight=1),
        KeyRecord(id="k2", secret="secret-2", weight=1),
    ]
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(
            base_url="https://alpha.test/v1", api_keys=[r.secret for r in keys],
            keys=keys, key_strategy=key_strategy)},
        state_dir=str(tmp_path / "state"),
        key_concurrency_cap=4,
    )


def _router(tmp_path, monkeypatch, key_strategy="round_robin"):
    monkeypatch.setattr("flexrouter._router.load_config",
                        lambda _p: _cfg(tmp_path, key_strategy))
    return LocalRouter(str(tmp_path / "config.yaml"))


def test_round_robin_alternates_keys_across_calls(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch, "round_robin")
    used_keys = []

    async def fake_chat(self, route, messages, **kwargs):
        used_keys.append(route.api_key)
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    for _ in range(4):
        router.generate([{"role": "user", "content": "hi"}], "smart")
    assert used_keys == ["secret-1", "secret-2", "secret-1", "secret-2"]


def test_an_auth_failure_benches_only_the_used_key(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch, "round_robin")
    calls = {"n": 0}

    async def fake_chat(self, route, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RouterError("Auth failure for provider 'alpha': 401")
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    router.generate([{"role": "user", "content": "hi"}], "smart")

    assert router._key_states.get("alpha", "k1").status == "benched"
    assert router._key_states.get("alpha", "k2").status == "live"
    # The provider itself must NOT be quarantined — k2 still works.
    assert not router._penalties.is_quarantined("alpha", "big")


def test_when_every_key_is_benched_the_provider_is_quarantined(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch, "round_robin")

    async def always_bad_key(self, route, messages, **kwargs):
        raise RouterError("Auth failure for provider 'alpha': 401")

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_bad_key)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    assert router._key_states.get("alpha", "k1").status == "benched"
    assert router._key_states.get("alpha", "k2").status == "benched"
    assert router._penalties.is_quarantined("alpha", "big")


def test_a_cooling_key_is_skipped_but_the_other_key_still_works(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch, "round_robin")
    router._key_states.mark_cooling("alpha", "k1", 9999, "too_fast")
    used_keys = []

    async def fake_chat(self, route, messages, **kwargs):
        used_keys.append(route.api_key)
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    for _ in range(3):
        router.generate([{"role": "user", "content": "hi"}], "smart")
    assert set(used_keys) == {"secret-2"}


def test_key_id_appears_in_the_trace(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch, "round_robin")

    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    router.generate([{"role": "user", "content": "hi"}], "smart")

    trace_path = tmp_path / "state" / "traces.jsonl"
    entry = json.loads(trace_path.read_text(encoding="utf-8").strip())
    assert entry["answered_by"]["key_id"] == "k1"


def test_mark_success_is_called_with_real_usage(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch, "round_robin")

    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 42}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    router.generate([{"role": "user", "content": "hi"}], "smart")
    assert router._key_states.get("alpha", "k1").tokens_today == 42
    assert router._key_states.get("alpha", "k1").requests_today == 1


def test_a_keyless_provider_is_unaffected(tmp_path, monkeypatch):
    cfg = FlexConfig(
        tiers={"smart": [ModelConfig(provider="local", model="m", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"local": ProviderConfig(base_url="http://localhost:11434/v1",
                                           api_keys=[], keys=[])},
        state_dir=str(tmp_path / "state"),
    )
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: cfg)
    router = LocalRouter(str(tmp_path / "config.yaml"))

    async def fake_chat(self, route, messages, **kwargs):
        assert route.api_key == ""
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    router.generate([{"role": "user", "content": "hi"}], "smart")  # must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_key_selection_agenerate.py -q`
Expected: FAIL — `route.api_key` is still engine.py's own blind rotation, not
key-state-aware; `router._key_states` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

In `flexrouter/_router.py`, add the imports:

```python
from flexrouter.engine import RouteResult
from flexrouter.key_state import KeyStateStore
from flexrouter.scheduler import RoundRobinCounters, pick_key
```

(`RouteResult` is a plain dataclass — importing its name for a type hint
reads it, it does not modify `engine.py` in any way.)

In `LocalRouter.__init__`, after the line that builds `self._traces`, add:

```python
        self._key_states = KeyStateStore(self._cfg.state_dir)
        self._round_robin = RoundRobinCounters()
```

In `LocalRouter.reload`, after the line that rebuilds `self._traces`, add:

```python
        self._key_states = KeyStateStore(self._cfg.state_dir)
```

(leave `self._round_robin` alone on reload — round-robin position is a live
scheduling cursor, not configuration, and resetting it on every hot reload
would make the strategy re-favor the first key every time a file changes.)

Add a new method, right after `_engine_for`:

```python
    def _pick_key(self, route: RouteResult) -> tuple[Optional[RouteResult], Optional[str]]:
        """Override a RouteResult's api_key with a real, stateful choice.

        engine.py already picked *which model* — this picks *which key*,
        from real per-key state instead of blind rotation (ADR 0011).
        Returns (None, None) when every configured key for this provider is
        currently unavailable, signalling that back to engine.py through
        PenaltyBox primitives it already reads, never by teaching it
        anything new.
        """
        provider_cfg = self._cfg.providers.get(route.provider)
        candidates = provider_cfg.keys if provider_cfg else []
        if not candidates:
            return route, None  # keyless provider (e.g. local Ollama) — unchanged

        model_id = f"{route.provider}/{route.model}"
        chosen = pick_key(
            candidates, route.provider, model_id, provider_cfg.key_strategy,
            self._key_states, self._cfg.key_concurrency_cap,
            counters=self._round_robin if provider_cfg.key_strategy == "round_robin" else None,
        )
        if chosen is None:
            key_ids = [r.id for r in candidates]
            if self._key_states.all_benched_or_disabled(route.provider, key_ids):
                self._penalties.quarantine_provider(
                    route.provider, "every configured key is benched")
            else:
                wait = self._key_states.min_seconds_until_available(route.provider, key_ids)
                if wait != float("inf"):
                    self._penalties.penalize_short(
                        route.provider, route.model, int(max(wait, 30.0)))
            return None, None

        return dataclasses.replace(route, api_key=chosen.secret), chosen.id
```

Rewrite `_handle_auth_failure` to bench the specific key rather than always
quarantining the whole provider:

```python
    def _handle_auth_failure(self, route, exc, key_id: Optional[str]) -> None:
        """Sideline the key that was rejected, not the whole provider.

        Stage 4's whole point (spec fault 5): a rejected key used to abort
        every route through its provider, even routes using a different,
        perfectly good key. Now only that one key is benched. The provider
        itself is only quarantined when every configured key for it has
        become benched or disabled (ruling 2/4) — a fact about the account,
        not about one credential.

        The reason is scrubbed here, at the write site, for the same reason
        it always has been: this text is persisted and later served
        unauthenticated by /api/* and /v1/models.
        """
        reason = scrub(str(exc))
        provider_cfg = self._cfg.providers.get(route.provider)
        if key_id is None or not provider_cfg or not provider_cfg.keys:
            # No key identity to bench (keyless provider, or the failure
            # happened before a key was ever chosen) — fall back to the
            # pre-Stage-4 behaviour rather than silently doing nothing.
            self._penalties.quarantine_provider(route.provider, reason)
            self._events.record(
                route.provider, route.model, "server_error",
                detail=f"provider quarantined (auth): {reason}")
            return
        self._key_states.mark_benched(route.provider, key_id, reason)
        key_ids = [r.id for r in provider_cfg.keys]
        if self._key_states.all_benched_or_disabled(route.provider, key_ids):
            self._penalties.quarantine_provider(route.provider, reason)
            self._events.record(
                route.provider, route.model, "server_error",
                detail=f"provider quarantined (auth, every key benched): {reason}")
        else:
            self._events.record(
                route.provider, route.model, "server_error",
                detail=f"key benched (auth): {reason}")
```

In `agenerate`'s retry loop, change the route-selection block from:

```python
            route = self._engine_for(tier).select(tier, estimated_tokens, vision, session_id)

            if route is None:
```

to:

```python
            route = self._engine_for(tier).select(tier, estimated_tokens, vision, session_id)
            key_id: Optional[str] = None
            if route is not None:
                route, key_id = self._pick_key(route)

            if route is None:
```

Update every `attempts.append({...})` call site in `agenerate` to use the
real `key_id` instead of the hardcoded `None` — there are three (in the
`RateLimitError`, `RouterError`, and `ProviderError` branches). Change
`"key_id": None` to `"key_id": key_id` in each.

Update the `RouterError` branch's call to `_handle_auth_failure`:

```python
            except RouterError as exc:
                self._handle_auth_failure(route, exc, key_id)
```

Add key-level cooling to the `RateLimitError` branch — right after the
existing `self._engine.penalize(route.provider, route.model)` line inside
that `except`, add:

```python
                if key_id is not None:
                    self._key_states.mark_cooling(
                        route.provider, key_id,
                        self._penalties.penalty_seconds(route.provider, route.model),
                        "too_fast")
```

(this goes before the existing `self._events.record(...)` call in that
branch, so the cooling state is already in place by the time anything reads
it.)

Bracket the outbound call with `begin_request`/`end_request` and record
success. Change:

```python
            start = time.monotonic()
            try:
                result = await self._client.chat(route, messages, **kwargs)
            except RateLimitError as exc:
```

to:

```python
            start = time.monotonic()
            if key_id is not None:
                self._key_states.begin_request(route.provider, key_id)
            try:
                result = await self._client.chat(route, messages, **kwargs)
            except RateLimitError as exc:
```

and, after the last `except ProviderError as exc:` branch's body (i.e.
immediately before the blank line that precedes `latency_ms = int(...)` —
the start of the success path), add a `finally` to the try block. Since a
`finally` must be attached to the same `try`, restructure the try/except
chain's closing to add one: after the final `except ProviderError as exc:`
branch's existing `continue` statement, add a `finally:` clause immediately
before the (already-existing, unindented) `latency_ms = int(...)` line, and
indent nothing else — Python allows `finally` after the last `except` in a
chain without an `else`:

```python
            except ProviderError as exc:
                self._handle_provider_error(route, exc)
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="error",
                )
                self._history.record(self._engine.health_snapshot())
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": exc.status_code,
                                 "provider_message": str(exc), "key_id": key_id,
                                 "ms": int((time.monotonic() - start) * 1000)})
                if attempt == retries:
                    _write_trace(ok=False)
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue
            finally:
                if key_id is not None:
                    self._key_states.end_request(route.provider, key_id)

            latency_ms = int((time.monotonic() - start) * 1000)
```

(This `finally` fires on every path out of the `try` — the three `except`
branches' `continue`/`raise` paths and the plain success fallthrough alike —
so `active_requests` is decremented exactly once per attempt regardless of
outcome.)

In the success path, replace the two `"key_id": None` occurrences
(`answered_by={"provider": route.provider, "model": route.model, "key_id": None}`
in the `_write_trace(ok=True, ...)` call) with `"key_id": key_id`, and add
the `mark_success` call right before `self._audit.log(...)` in the success
path:

```python
            self._engine.record_request(route.provider, route.model, total_tokens)
            self._quota_tracker.record(route.provider, route.model)
            if key_id is not None:
                self._key_states.mark_success(route.provider, key_id, total_tokens, latency_ms)
            self._audit.log(
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_key_selection_agenerate.py -q`
Expected: PASS, 7 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: 645+ passed, 1 skipped. `_handle_auth_failure`'s signature changed —
if any existing test calls it directly (rather than through `agenerate`),
update the call site to pass a `key_id` (use `None` if the test has no key
context, matching the keyless-provider fallback path).

- [ ] **Step 6: Commit**

```bash
git add flexrouter/_router.py tests/test_key_selection_agenerate.py
git commit -m "feat(keys): real per-key selection replaces blind rotation in agenerate"
```

---

### Task 5: Wire key selection into `agenerate_stream`

Mirrors Task 4. `_pick_key` and the rewritten `_handle_auth_failure` already
exist after Task 4 — this task only changes `agenerate_stream`'s own call
sites to use them.

**Files:**
- Modify: `flexrouter/_router.py` (`agenerate_stream` only)
- Test: `tests/test_key_selection_agenerate_stream.py`

**Interfaces:**
- Consumes: `_pick_key`, `_handle_auth_failure(route, exc, key_id)`,
  `self._key_states`, `self._round_robin` (all from Task 4).
- Produces: no new names.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_key_selection_agenerate_stream.py
import json

from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError, StreamChunk
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.exceptions import RouterError
from flexrouter.keys import KeyRecord


def _cfg(tmp_path):
    keys = [KeyRecord(id="k1", secret="secret-1"), KeyRecord(id="k2", secret="secret-2")]
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(
            base_url="https://alpha.test/v1", api_keys=[r.secret for r in keys],
            keys=keys, key_strategy="round_robin")},
        state_dir=str(tmp_path / "state"),
        key_concurrency_cap=4,
    )


def _router(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


async def _drain(router, **kwargs):
    events = []
    async for ev in router.agenerate_stream([{"role": "user", "content": "hi"}], "smart", **kwargs):
        events.append(ev)
    return events


async def test_round_robin_alternates_keys_in_the_streaming_path(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)
    used_keys = []

    async def fake_stream(self, route, messages, **kwargs):
        used_keys.append(route.api_key)
        yield StreamChunk(content="hi")
        yield StreamChunk(usage={"total_tokens": 1})

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    for _ in range(4):
        await _drain(router)
    assert used_keys == ["secret-1", "secret-2", "secret-1", "secret-2"]


async def test_an_auth_failure_benches_only_the_used_key_in_streaming(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)
    calls = {"n": 0}

    async def fake_stream(self, route, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RouterError("Auth failure for provider 'alpha': 401")
        yield StreamChunk(content="hi")
        yield StreamChunk(usage={"total_tokens": 1})

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    await _drain(router)

    assert router._key_states.get("alpha", "k1").status == "benched"
    assert router._key_states.get("alpha", "k2").status == "live"


async def test_key_id_appears_in_the_streaming_trace(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_stream(self, route, messages, **kwargs):
        yield StreamChunk(content="hi")
        yield StreamChunk(usage={"total_tokens": 1})

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    await _drain(router)

    trace_path = tmp_path / "state" / "traces.jsonl"
    entry = json.loads(trace_path.read_text(encoding="utf-8").strip())
    assert entry["answered_by"]["key_id"] == "k1"


async def test_active_requests_is_decremented_after_a_mid_stream_failure(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_stream(self, route, messages, **kwargs):
        yield StreamChunk(content="par")
        raise ProviderError("alpha/big: dropped", status_code=500)

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    try:
        await _drain(router)
    except Exception:
        pass
    assert router._key_states.get("alpha", "k1").active_requests == 0
```

Implementer note: check `tests/test_agenerate_stream.py` or
`tests/test_trace_agenerate_stream.py` for this repo's existing convention
for driving an async generator test (`asyncio_mode = "auto"` in
`pyproject.toml` most likely means a plain `async def test_...` function
works directly) before finalizing these — simplify the boilerplate to match
whatever's already established rather than introducing a new pattern.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_key_selection_agenerate_stream.py -q`
Expected: FAIL — `agenerate_stream` still uses engine.py's blind key rotation.

- [ ] **Step 3: Write the implementation**

In `agenerate_stream`'s retry loop, apply the identical route-selection
change Task 4 made to `agenerate`:

```python
            route = self._engine_for(tier).select(tier, estimated_tokens, vision, session_id)
            key_id: Optional[str] = None
            if route is not None:
                route, key_id = self._pick_key(route)

            if route is None:
```

Update the `RouterError` (auth) branch's call:

```python
            except RouterError as exc:
                self._handle_auth_failure(route, exc, key_id)
```

Add key-level cooling to the `RateLimitError` branch, right after its
existing `self._engine.penalize(route.provider, route.model)` line:

```python
                if key_id is not None:
                    self._key_states.mark_cooling(
                        route.provider, key_id,
                        self._penalties.penalty_seconds(route.provider, route.model),
                        "too_fast")
```

Update every `"key_id": None` in this method's `attempts.append({...})`
calls to `"key_id": key_id` — there are five (empty-stream, `RateLimitError`,
`RouterError`, `ProviderError`, and the `except BaseException` post-commit
handler).

Bracket `stream.__anext__()`'s first-chunk pull with `begin_request`/
`end_request`, per ruling 11 (active_requests covers up to the first
response, not the whole stream). Change:

```python
            start = time.monotonic()
            stream = self._client.stream_chat(route, messages, **kwargs)

            try:
                first_chunk = await stream.__anext__()
            except StopAsyncIteration:
```

to:

```python
            start = time.monotonic()
            stream = self._client.stream_chat(route, messages, **kwargs)

            if key_id is not None:
                self._key_states.begin_request(route.provider, key_id)
            try:
                first_chunk = await stream.__anext__()
            except StopAsyncIteration:
```

and add a `finally` after the chain's last `except ProviderError as exc:`
branch (immediately before the `# Committed:` comment that follows it),
exactly mirroring Task 4's placement:

```python
            except ProviderError as exc:
                self._handle_provider_error(route, exc)
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="error",
                )
                self._history.record(self._engine.health_snapshot())
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": exc.status_code,
                                 "provider_message": str(exc), "key_id": key_id,
                                 "ms": int((time.monotonic() - start) * 1000)})
                yield AttemptFailedEvent(
                    attempt=attempt + 1, max_attempts=max_attempts,
                    provider=route.provider, model=route.model, reason="provider_error",
                    detail=str(exc),
                )
                if attempt == retries:
                    _write_trace(ok=False)
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue
            finally:
                if key_id is not None:
                    self._key_states.end_request(route.provider, key_id)

            # Committed: a delta (or a clean empty stream) arrived with no
```

(Ruling 11 means `end_request` fires here, at the first-chunk boundary, on
every path — including the success path that falls through this `try`
without hitting any `except` — and is never called again later for this
attempt, even if a post-commit failure happens afterward. That is the
intended scope, not an oversight: post-commit activity is no longer
"pending a first response," which is what the concurrency cap is measuring.)

In the two `answered_by={"provider": route.provider, "model": route.model, "key_id": None}`
occurrences in this method (the success `_write_trace(ok=True, ...)` call),
change `"key_id": None` to `"key_id": key_id`.

Add `mark_success` to the success path, right before
`self._audit.log(tier=tier, provider=route.provider, ...)` near the end of
the method:

```python
            self._engine.record_request(route.provider, route.model, total_tokens)
            self._quota_tracker.record(route.provider, route.model)
            if key_id is not None:
                self._key_states.mark_success(route.provider, key_id, total_tokens, latency_ms)
            self._audit.log(
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_key_selection_agenerate_stream.py -q`
Expected: PASS, 4 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: 645+ passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/_router.py tests/test_key_selection_agenerate_stream.py
git commit -m "feat(keys): real per-key selection replaces blind rotation in agenerate_stream"
```

---

### Task 6: End-to-end proof, docs, and the ADR

**Files:**
- Test: `tests/test_key_selection_e2e.py`
- Modify: `CONTEXT.md`
- Create: `docs/adr/0011-per-key-state-lives-outside-engine-py.md`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: no new names.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_key_selection_e2e.py
import json

from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.exceptions import RouterError
from flexrouter.keys import KeyRecord


def _cfg(tmp_path):
    keys = [KeyRecord(id="k1", secret="secret-1"), KeyRecord(id="k2", secret="secret-2")]
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(
            base_url="https://alpha.test/v1", api_keys=[r.secret for r in keys],
            keys=keys, key_strategy="round_robin")},
        state_dir=str(tmp_path / "state"),
    )


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def test_a_real_401_through_the_http_surface_benches_one_key_and_the_next_request_still_works(
        tmp_path, monkeypatch):
    calls = {"n": 0}

    async def fake_chat(self, route, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RouterError("Auth failure for provider 'alpha': 401")
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    client = _client(tmp_path, monkeypatch)

    r1 = client.post("/v1/chat/completions",
                     json={"model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    assert r1.status_code == 200

    key_state_path = tmp_path / "state" / "key_state.json"
    on_disk = json.loads(key_state_path.read_text(encoding="utf-8"))
    statuses = {k: v["status"] for k, v in on_disk.items()}
    assert statuses.get("alpha:k1") == "benched"
    assert statuses.get("alpha:k2", "live") == "live"
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `python -m pytest tests/test_key_selection_e2e.py -q`
Expected: PASS already, if Tasks 1–5 are correct — this is a proof, not new
behaviour.

- [ ] **Step 3: Update `CONTEXT.md`**

Add a glossary entry after "Key" and before "The home" (matching the
existing alphabetical-ish grouping — check the current file's order and
place it where your own read makes clear is right):

```markdown
- **Per-key state** — `state/key_state.json`, keyed by `provider:key_id`, survives restart (`flexrouter/key_state.py`: `KeyStateStore`). Replaces the old blind `counter % len(keys)` rotation: a rejected key is benched by itself (`status: benched`), not the whole provider — the provider is only quarantined once every configured key for it is benched or disabled. A rate-limited key cools down (`status: cooling`, floored at 30s) instead of re-entering rotation immediately. Which live key answers a request is chosen by a `Scheduler` (`flexrouter/scheduler.py`: `pick_key`) — `most_headroom` (default), `round_robin`, `fastest`, or `weighted`, configurable per provider (`ProviderConfig.key_strategy`). None of this touches `engine.py`: key selection happens in `LocalRouter`, after `engine.select()` has already picked a model, by overriding the `api_key` field a `RouteResult` already has (ADR 0011).
```

Update the closing "Not yet built" paragraph:

```markdown
The v2 design (`docs/superpowers/specs/2026-09-18-flexrouter-v2-design.md`) describes nine stages. Stages 1 through 4 are implemented: the shared home, the OpenAI-shaped surface, the per-request trace, and real per-key state replacing blind key rotation. Do not treat later-stage concepts (anything not covered above) as present in the code.
```

- [ ] **Step 4: Write the ADR**

```markdown
# 0011. Per-key state lives outside engine.py

Date: 2026-09-21
Status: Accepted

## Context

Spec section 5 asks for blind key rotation (`counter % len(provider_cfg.api_keys)`)
to be replaced with real per-key state: a rejected key benches only itself,
a rate-limited key cools down instead of re-entering rotation immediately,
and a configurable strategy picks between a provider's live keys. The code
that currently does the blind rotation lives inside `engine.py::_make_result`,
which is on the "reused unchanged" list — three independent reviews judged
its design sound, and Stage 3 already established the pattern of routing
around it rather than editing it (`explain_unavailable()` for `skipped`,
`_build_pin_engine` for pinning).

`ProviderConfig` already carries `keys: list[KeyRecord]` — real key
identity, resolved from `keys.json`/environment/inline secrets by
`config.py::resolve_keys` — alongside the flattened `api_keys: list[str]`
that `engine.py` actually rotates through. This meant the real fix did not
need any new plumbing from settings down to the router; it only needed a
place to *use* the identity that was already there.

## Decision

**`engine.py` is not touched.** `RoutingEngine.select()` still returns a
`RouteResult` with *some* `api_key` value, chosen by its own unmodified
blind rotation. `LocalRouter` immediately overrides it:
`dataclasses.replace(route, api_key=chosen.secret)` — the same technique
`_build_pin_engine` already uses on `FlexConfig`, applied here to the
`api_key` field `RouteResult` already has. The chosen key's id is never
attached to `RouteResult` (that would need editing the dataclass in
`engine.py`); it travels as a plain local variable through the retry loop,
the same way Stage 3's `attempts`/`skipped` do.

*Cost:* `engine.py`'s own key rotation still runs on every call, computing a
value that is immediately discarded. Accepted because the alternative is
editing a frozen file for a computation whose result is thrown away either
way — the waste is in CPU cycles nobody will ever measure, not in behaviour.

**When every key for a provider is currently unavailable, the key layer
signals it to `engine.py` through primitives `engine.py` already reads.** A
cooling-dominated exhaustion calls `PenaltyBox.penalize_short(provider,
model, remaining_seconds)`; an all-benched exhaustion calls
`PenaltyBox.quarantine_provider(provider, reason)`. Both already exist,
already unmodified, and `engine.py`'s `_skip_reason` and
`seconds_until_available` already consult them. This is the third time this
codebase has used "teach `engine.py` something by feeding it through an
interface it already reads" instead of editing it — Stage 2 did it for
pinning, Stage 3 did it for `skipped`.

**A rejected key (`bad_key`) benches only that key, not the provider** —
this is the fix the whole stage exists for. The provider itself is
quarantined only once every configured key for it is `benched` or
`disabled`, matching the spec's own wording exactly.

**`most_headroom`, `fastest`, `requests_today`/`tokens_today`/`failures_24h`,
and the concurrency cap all needed a concrete definition the spec's worked
example doesn't give** — `most_headroom` is fewest `active_requests` then
lowest `tokens_today` (no per-key rate limit exists anywhere in the schema
to compute a literal headroom fraction against); `fastest` is an EMA of
completed-attempt latency with unmeasured keys sorting best (so a new key
gets tried at least once); the three daily counters reset on UTC
calendar-day rollover, the same simplification `budget.py`'s `DailyBudget`
already makes elsewhere, just persisted; the concurrency cap is a new
setting, `key_concurrency_cap`, default `4`.

*Cost of the daily-rollover simplification:* `failures_24h` is really
"failures today," which can under- or over-count a true trailing 24 hours
depending on what time of day a key's streak happens to fall. Accepted
because every field this covers is informational bookkeeping for a future
dashboard, not something this stage's own selection or backoff logic reads.

**`active_requests` is bracketed around "time until the first response,"
not the whole stream.** For non-streaming this is the whole call; for
streaming it is `begin_request`/`end_request` around
`stream.__anext__()`'s first pull only — a post-commit failure does not
re-decrement or otherwise touch it, because it was already released the
moment the first chunk (or a pre-commit failure) resolved. Extending this
through the whole stream would mean threading key-state calls through
Stage 3's carefully-finished post-commit code for a form of sustained
concurrency-capping the spec never asked for.

**Rate-limit cooldown duration reuses the existing model-level penalty
duration** (`PenaltyBox.penalty_seconds`) as a stand-in for "the provider's
stated reset time," because the decision layer that would supply a real one
(spec §4a, Stage 5) does not exist yet. This is a known, temporary
approximation — Stage 5 should replace it.

## Consequences

- A dashboard (Stage 8) reading `state/key_state.json` can show, per key:
  is it live/cooling/benched, when it'll recover, how much it's been used
  today, its recent latency — everything the spec's worked example asked
  for, all without engine.py knowing any of it exists.
- `key_id` in `state/traces.jsonl` (reserved as always-`null` by Stage 3,
  ADR 0010 ruling 4) is now populated with a real value on every trace
  where a key was actually used, closing the one deferred field that stage
  explicitly left for later.
- The next time a "route around engine.py, don't edit it" case comes up
  (Stage 5's error brain will likely need one, since verdicts feed back
  into routing decisions engine.py currently makes alone), this ADR and its
  two predecessors are the established playbook: find the interface
  `engine.py` already reads, and feed it through that.
```

- [ ] **Step 5: Run the whole suite one last time**

Run: `python -m pytest -q`
Expected: 645 + (this stage's new test count) passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add tests/test_key_selection_e2e.py CONTEXT.md docs/adr/0011-per-key-state-lives-outside-engine-py.md
git commit -m "docs(keys): prove per-key state end to end, and record the design as ADR 0011"
```

---

## After all six tasks

- Run the full suite once more and record the final passed/skipped count.
- File anything discovered but out of scope under `.scratch/v2-stage4-followups/issues/`.
- There is no git remote. The owner explicitly asked not to be consulted
  through the end of Stage 7 — merge this stage's branch into `master`
  yourself once its final whole-branch review is clean, the same way Stage 3
  did, and record that as a ruling rather than a question.
