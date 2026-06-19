# Dashboard Overhaul — Plan 1: Backend Persistence & Endpoints

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable persistence layer (health time-series, incident ledger, persisted penalties), server-side stats/uptime aggregation, and clear config validation, exposed via new read-only HTTP endpoints.

**Architecture:** The engine process owns all writes into `state_dir`; the dashboard server reads those files. New modules (`events.py`, `health_history.py`, `dashboard/stats.py`, `dashboard/uptime.py`) each have one responsibility. A passive sampler thread records in-memory engine state on an interval — no network calls, no free-tier quota cost. Penalties move from monotonic to persisted wall-clock so they survive restart and feed the incident timeline.

**Tech Stack:** Python 3 stdlib only (csv, json, threading, statistics, datetime, pathlib). No new backend dependencies. pytest for tests, matching existing `tests/` style.

## Global Constraints

- No new third-party backend dependencies — stdlib only.
- All file writes into `state_dir` are atomic where a reader could observe a partial write: write to `*.tmp`, then `os.replace`. Append-only CSV/JSONL files use a single `open(..., "a")` write per record (atomic for small lines on local FS).
- All timestamps persisted as ISO-8601 UTC with `Z` suffix, seconds precision: `datetime.now(timezone.utc).isoformat(timespec="seconds")`.
- Event types are exactly: `penalized`, `recovered`, `rate_limited`, `timeout`, `server_error`.
- Statuses in `audit.csv` are exactly: `ok`, `rate_limited`, `error`.
- Default config values: `sample_interval_seconds=60`, `health_history_days=30`.
- Never log secrets (API keys) into any state file.

---

## File Structure

- Create `flexrouter/events.py` — `EventLogger`: append-only `events.csv` incident ledger.
- Create `flexrouter/health_history.py` — `HealthHistory`: append/read/compact `health_history.jsonl`.
- Create `flexrouter/sampler.py` — `PassiveSampler`: daemon thread sampling engine state on an interval.
- Modify `flexrouter/recovery.py` — `PenaltyBox`: wall-clock expiry, persistence, event emission.
- Modify `flexrouter/engine.py` — add `health_snapshot()`.
- Modify `flexrouter/config.py` — new settings fields + `validate_config()`.
- Modify `flexrouter/_router.py` — wire EventLogger, HealthHistory, PassiveSampler; emit events; write samples.
- Create `flexrouter/dashboard/stats.py` — `compute_stats(state_dir)` aggregating `audit.csv`.
- Create `flexrouter/dashboard/uptime.py` — `compute_uptime(state_dir)` from history + events.
- Modify `flexrouter/dashboard/api.py` + `flexrouter/dashboard/server.py` — new endpoints.
- Tests mirror under `tests/`.

---

## Task 1: EventLogger (incident ledger)

**Files:**
- Create: `flexrouter/events.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Produces: `EventLogger(state_dir: str)`; method `record(provider: str, model: str, event_type: str, detail: str = "", penalty_seconds: int = 0) -> None`; class attr `HEADERS: list[str]`; `recent(n: int = 100) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_events.py
import csv
from flexrouter.events import EventLogger

def test_creates_csv_with_headers(tmp_path):
    log = EventLogger(str(tmp_path))
    log.record("groq", "llama", "penalized", detail="429", penalty_seconds=30)
    with (tmp_path / "events.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["event_type"] == "penalized"
    assert rows[0]["provider"] == "groq"
    assert rows[0]["penalty_seconds"] == "30"

def test_appends_rows(tmp_path):
    log = EventLogger(str(tmp_path))
    log.record("groq", "llama", "penalized")
    log.record("groq", "llama", "recovered")
    assert len(log.recent()) == 2
    assert log.recent()[-1]["event_type"] == "recovered"

def test_rejects_unknown_event_type(tmp_path):
    log = EventLogger(str(tmp_path))
    try:
        log.record("groq", "llama", "exploded")
        assert False, "should have raised"
    except ValueError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flexrouter.events'`

- [ ] **Step 3: Write minimal implementation**

```python
# flexrouter/events.py
from __future__ import annotations
import csv
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

_EVENT_TYPES = {"penalized", "recovered", "rate_limited", "timeout", "server_error"}


class EventLogger:
    HEADERS = ["timestamp", "provider", "model", "event_type", "detail", "penalty_seconds"]

    def __init__(self, state_dir: str) -> None:
        self._dir = Path(state_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "events.csv"
        self._recent: deque[dict] = deque(maxlen=500)
        if not self._path.exists():
            with self._path.open("w", newline="") as f:
                csv.DictWriter(f, fieldnames=self.HEADERS).writeheader()

    def record(self, provider: str, model: str, event_type: str,
               detail: str = "", penalty_seconds: int = 0) -> None:
        if event_type not in _EVENT_TYPES:
            raise ValueError(f"Unknown event_type {event_type!r}; expected one of {sorted(_EVENT_TYPES)}")
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": provider,
            "model": model,
            "event_type": event_type,
            "detail": detail,
            "penalty_seconds": penalty_seconds,
        }
        with self._path.open("a", newline="") as f:
            csv.DictWriter(f, fieldnames=self.HEADERS).writerow(row)
        self._recent.append(row)

    def recent(self, n: int = 100) -> list[dict]:
        return list(self._recent)[-n:]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_events.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add flexrouter/events.py tests/test_events.py
git commit -m "feat: EventLogger incident ledger (events.csv)"
```

---

## Task 2: PenaltyBox persistence + event emission

**Files:**
- Modify: `flexrouter/recovery.py`
- Modify: `tests/test_recovery.py`
- Test: `tests/test_recovery_persistence.py`

**Interfaces:**
- Consumes: nothing new (event sink is an optional callback to stay decoupled from Task 1).
- Produces: `PenaltyBox(base_seconds, max_seconds, state_dir: str | None = None, on_event=None)`. `on_event` is `Callable[[str, str, str, int], None]` called as `on_event(provider, model, event_type, penalty_seconds)` with `event_type` in `{"penalized", "recovered"}`. Expiry/`is_penalized`/`penalty_seconds`/`penalty_until` now use wall-clock `time.time()`. New: `active_penalties() -> dict[str, dict]` returning `{ "provider/model": {"until": float, "count": int} }` for live state.

> **Migration note:** `penalty_until()` now returns a wall-clock epoch (`time.time()` based) instead of monotonic. The existing `tests/test_recovery.py::test_penalty_until_returns_timestamp` asserts against `time.monotonic()` and MUST be updated in Step 1.

- [ ] **Step 1: Update the existing monotonic assertion to wall-clock**

In `tests/test_recovery.py`, replace `test_penalty_until_returns_timestamp` with:

```python
def test_penalty_until_returns_timestamp():
    import time as _t
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.penalize("groq", "llama")
    until = pb.penalty_until("groq", "llama")
    assert until is not None
    assert until > _t.time()  # wall-clock epoch, survives restart
```

- [ ] **Step 2: Write the failing persistence + events test**

```python
# tests/test_recovery_persistence.py
from flexrouter.recovery import PenaltyBox

def test_persists_across_instances(tmp_path):
    pb = PenaltyBox(30, 1800, state_dir=str(tmp_path))
    pb.penalize("groq", "llama")
    # New instance simulates a process restart
    pb2 = PenaltyBox(30, 1800, state_dir=str(tmp_path))
    assert pb2.is_penalized("groq", "llama") is True

def test_emits_penalized_event():
    events = []
    pb = PenaltyBox(30, 1800, on_event=lambda p, m, t, s: events.append((p, m, t, s)))
    pb.penalize("groq", "llama")
    assert events[0][0:3] == ("groq", "llama", "penalized")
    assert events[0][3] > 0

def test_emits_recovered_event_on_clear():
    events = []
    pb = PenaltyBox(30, 1800, on_event=lambda p, m, t, s: events.append((p, m, t)))
    pb.penalize("groq", "llama")
    pb.clear("groq", "llama")
    assert ("groq", "llama", "recovered") in events

def test_expired_penalty_not_loaded(tmp_path):
    pb = PenaltyBox(1, 10, state_dir=str(tmp_path))
    pb.penalize_short("groq", "llama", seconds=0)  # already expired
    pb2 = PenaltyBox(1, 10, state_dir=str(tmp_path))
    assert pb2.is_penalized("groq", "llama") is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_recovery_persistence.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'state_dir'`

- [ ] **Step 4: Rewrite `recovery.py`**

```python
# flexrouter/recovery.py
from __future__ import annotations
import json
import os
import time
from pathlib import Path
from typing import Callable, Optional


class PenaltyBox:
    """Tracks per-(provider, model) exponential backoff penalties.

    Uses wall-clock time so penalties survive process restart when a
    state_dir is given. Emits 'penalized'/'recovered' events via on_event.
    """

    def __init__(self, base_seconds: int, max_seconds: int,
                 state_dir: Optional[str] = None,
                 on_event: Optional[Callable[[str, str, str, int], None]] = None) -> None:
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds
        self._on_event = on_event
        self._path = Path(state_dir) / "penalties.json" if state_dir else None
        # key -> (until: epoch float, count: int)
        self._state: dict[str, tuple[float, int]] = {}
        self._load()

    def _key(self, provider: str, model: str) -> str:
        return f"{provider}/{model}"

    def _split(self, key: str) -> tuple[str, str]:
        provider, _, model = key.partition("/")
        return provider, model

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except Exception:
            return
        now = time.time()
        for k, v in raw.items():
            until, count = float(v["until"]), int(v["count"])
            if until > now:  # drop already-expired penalties
                self._state[k] = (until, count)

    def _save(self) -> None:
        if not self._path:
            return
        data = {k: {"until": u, "count": c} for k, (u, c) in self._state.items()}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self._path)

    def _emit(self, provider: str, model: str, event_type: str, secs: int) -> None:
        if self._on_event:
            self._on_event(provider, model, event_type, secs)

    def penalize(self, provider: str, model: str) -> None:
        k = self._key(provider, model)
        _, count = self._state.get(k, (0.0, 0))
        count += 1
        secs = min(self.base_seconds * (2 ** (count - 1)), self.max_seconds)
        self._state[k] = (time.time() + secs, count)
        self._save()
        self._emit(provider, model, "penalized", int(secs))

    def penalize_short(self, provider: str, model: str, seconds: int) -> None:
        k = self._key(provider, model)
        _, count = self._state.get(k, (0.0, 0))
        self._state[k] = (time.time() + seconds, count)
        self._save()
        self._emit(provider, model, "penalized", int(seconds))

    def clear(self, provider: str, model: str) -> None:
        if self._state.pop(self._key(provider, model), None) is not None:
            self._save()
            self._emit(provider, model, "recovered", 0)

    def is_penalized(self, provider: str, model: str) -> bool:
        k = self._key(provider, model)
        if k not in self._state:
            return False
        until, _ = self._state[k]
        if time.time() >= until:
            del self._state[k]
            self._save()
            self._emit(*self._split(k), "recovered", 0)
            return False
        return True

    def penalty_seconds(self, provider: str, model: str) -> int:
        k = self._key(provider, model)
        if k not in self._state:
            return 0
        until, count = self._state[k]
        if time.time() >= until:
            return 0
        return int(min(self.base_seconds * (2 ** (count - 1)), self.max_seconds))

    def penalty_until(self, provider: str, model: str) -> Optional[float]:
        k = self._key(provider, model)
        if k not in self._state:
            return None
        until, _ = self._state[k]
        return until if time.time() < until else None

    def active_penalties(self) -> dict[str, dict]:
        now = time.time()
        return {k: {"until": u, "count": c}
                for k, (u, c) in self._state.items() if u > now}
```

- [ ] **Step 5: Run the full recovery suite**

Run: `python -m pytest tests/test_recovery.py tests/test_recovery_persistence.py -v`
Expected: PASS (all). Note `test_penalty_expires` still works — `time.time()` honors `sleep`.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/recovery.py tests/test_recovery.py tests/test_recovery_persistence.py
git commit -m "feat: persist penalties (wall-clock) and emit events"
```

---

## Task 3: HealthHistory writer

**Files:**
- Create: `flexrouter/health_history.py`
- Test: `tests/test_health_history.py`

**Interfaces:**
- Produces: `HealthHistory(state_dir: str, retention_days: int = 30)`; `record(sample: dict) -> None` (appends one JSONL line, injecting `timestamp` if absent); `read(since_iso: str | None = None) -> list[dict]`; `compact(now: datetime | None = None) -> None` (drops entries older than retention, downsamples >24h-old to one per 5 min).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_health_history.py
import json
from datetime import datetime, timezone, timedelta
from flexrouter.health_history import HealthHistory

def test_record_appends_jsonl(tmp_path):
    hh = HealthHistory(str(tmp_path))
    hh.record({"models": {"groq/llama": {"status": "up"}}})
    lines = (tmp_path / "health_history.jsonl").read_text().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["models"]["groq/llama"]["status"] == "up"
    assert "timestamp" in obj  # injected automatically

def test_read_filters_since(tmp_path):
    hh = HealthHistory(str(tmp_path))
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    new = datetime.now(timezone.utc).isoformat(timespec="seconds")
    hh.record({"timestamp": old, "models": {}})
    hh.record({"timestamp": new, "models": {}})
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    assert len(hh.read(since_iso=cutoff)) == 1

def test_compact_drops_beyond_retention(tmp_path):
    hh = HealthHistory(str(tmp_path), retention_days=7)
    ancient = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds")
    fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
    hh.record({"timestamp": ancient, "models": {}})
    hh.record({"timestamp": fresh, "models": {}})
    hh.compact()
    assert len(hh.read()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_health_history.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flexrouter.health_history'`

- [ ] **Step 3: Write minimal implementation**

```python
# flexrouter/health_history.py
from __future__ import annotations
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class HealthHistory:
    def __init__(self, state_dir: str, retention_days: int = 30) -> None:
        self._dir = Path(state_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "health_history.jsonl"
        self._retention_days = retention_days

    def record(self, sample: dict) -> None:
        if "timestamp" not in sample:
            sample = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), **sample}
        with self._path.open("a") as f:
            f.write(json.dumps(sample) + "\n")

    def read(self, since_iso: Optional[str] = None) -> list[dict]:
        if not self._path.exists():
            return []
        cutoff = _parse(since_iso) if since_iso else None
        out = []
        for line in self._path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # never crash on a torn line; skip it
            if cutoff and _parse(obj["timestamp"]) < cutoff:
                continue
            out.append(obj)
        return out

    def compact(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        retention_cutoff = now - timedelta(days=self._retention_days)
        downsample_cutoff = now - timedelta(hours=24)
        kept: list[dict] = []
        last_bucket: Optional[int] = None
        for obj in self.read():
            ts = _parse(obj["timestamp"])
            if ts < retention_cutoff:
                continue
            if ts < downsample_cutoff:
                bucket = int(ts.timestamp()) // 300  # one per 5 min
                if bucket == last_bucket:
                    continue
                last_bucket = bucket
            kept.append(obj)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(o) + "\n" for o in kept))
        os.replace(tmp, self._path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_health_history.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add flexrouter/health_history.py tests/test_health_history.py
git commit -m "feat: HealthHistory time-series writer with retention/compaction"
```

---

## Task 4: Engine health snapshot

**Files:**
- Modify: `flexrouter/engine.py` (add method after `seconds_until_available`, ~line 108)
- Test: `tests/test_engine_snapshot.py`

**Interfaces:**
- Consumes: existing `RoutingEngine`, `SlidingWindow.count`/`token_count` (verify names in `window.py`; if absent use the public counters that exist).
- Produces: `RoutingEngine.health_snapshot() -> dict` shaped `{"models": {"provider/model": {"status","rpm","tpm","penalized","penalty_until","latency_ewma_ms"}}, "providers": {"provider": {"models_up": int, "models_total": int}}}`. `latency_ewma_ms` is `None` here (filled by the router which sees latencies); snapshot reports routing-availability state only.

- [ ] **Step 1: Confirm SlidingWindow's public counters**

Run: `python -c "import inspect; from flexrouter.window import SlidingWindow; print([m for m in dir(SlidingWindow) if not m.startswith('__')])"`
Expected: prints method/attr names. Use whichever expose current request/token counts (e.g. `current_rpm`, `current_tpm`, or compute from internals). If only internals exist, add read-only `current_rpm()` / `current_tpm()` to `window.py` in this task.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_engine_snapshot.py
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.engine import RoutingEngine

def _cfg():
    return FlexConfig(
        tiers={"default": [ModelConfig("groq", "llama", 50, 30, 6000)]},
        providers={"groq": ProviderConfig(base_url="http://x", api_keys=["k"])},
    )

def test_snapshot_lists_all_models():
    eng = RoutingEngine(_cfg())
    snap = eng.health_snapshot()
    assert "groq/llama" in snap["models"]
    assert snap["models"]["groq/llama"]["status"] == "up"
    assert snap["providers"]["groq"]["models_total"] == 1

def test_snapshot_marks_penalized():
    eng = RoutingEngine(_cfg())
    eng.penalize("groq", "llama")
    snap = eng.health_snapshot()
    assert snap["models"]["groq/llama"]["penalized"] is True
    assert snap["models"]["groq/llama"]["status"] == "penalized"
    assert snap["providers"]["groq"]["models_up"] == 0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_engine_snapshot.py -v`
Expected: FAIL with `AttributeError: 'RoutingEngine' object has no attribute 'health_snapshot'`

- [ ] **Step 4: Add the method to `engine.py`**

```python
    def health_snapshot(self) -> dict:
        models: dict[str, dict] = {}
        providers: dict[str, dict] = {}
        seen: set[str] = set()
        for tier_models in self._cfg.tiers.values():
            for m in tier_models:
                key = f"{m.provider}/{m.model}"
                if key in seen:
                    continue
                seen.add(key)
                penalized = self._penalties.is_penalized(m.provider, m.model)
                until = self._penalties.penalty_until(m.provider, m.model)
                w = self._windows.get(key)
                rpm = w.current_rpm() if w else 0
                tpm = w.current_tpm() if w else 0
                models[key] = {
                    "status": "penalized" if penalized else "up",
                    "rpm": rpm,
                    "tpm": tpm,
                    "penalized": penalized,
                    "penalty_until": until,
                    "latency_ewma_ms": None,
                }
                pv = providers.setdefault(m.provider, {"models_up": 0, "models_total": 0})
                pv["models_total"] += 1
                if not penalized:
                    pv["models_up"] += 1
        return {"models": models, "providers": providers}
```

If Step 1 showed `window.py` lacks `current_rpm`/`current_tpm`, add to `SlidingWindow`:

```python
    def current_rpm(self) -> int:
        self._prune(time.monotonic())
        return len(self._requests)

    def current_tpm(self) -> int:
        self._prune(time.monotonic())
        return sum(self._tokens)
```

(Match the actual internal attribute names found in Step 1; `_prune` is illustrative — reuse the existing prune/cutoff logic.)

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_engine_snapshot.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add flexrouter/engine.py flexrouter/window.py tests/test_engine_snapshot.py
git commit -m "feat: RoutingEngine.health_snapshot for time-series sampling"
```

---

## Task 5: Config — new settings fields

**Files:**
- Modify: `flexrouter/config.py` (`FlexConfig` dataclass ~line 36; `load_config` return ~line 107)
- Test: `tests/test_config.py` (add cases)

**Interfaces:**
- Produces: `FlexConfig.sample_interval_seconds: int = 60`, `FlexConfig.health_history_days: int = 30`, parsed from `settings`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_config.py
def test_sampler_settings_defaults(tmp_path):
    from flexrouter.config import load_config
    p = tmp_path / "f.yaml"
    p.write_text(
        "providers:\n  groq:\n    base_url: http://x\n    api_keys: [k]\n"
        "tiers:\n  default:\n    - {provider: groq, model: m, score: 50, rpm: 1, tpm: 1}\n"
    )
    cfg = load_config(p)
    assert cfg.sample_interval_seconds == 60
    assert cfg.health_history_days == 30

def test_sampler_settings_override(tmp_path):
    from flexrouter.config import load_config
    p = tmp_path / "f.yaml"
    p.write_text(
        "providers:\n  groq:\n    base_url: http://x\n    api_keys: [k]\n"
        "tiers:\n  default:\n    - {provider: groq, model: m, score: 50, rpm: 1, tpm: 1}\n"
        "settings:\n  sample_interval_seconds: 15\n  health_history_days: 7\n"
    )
    cfg = load_config(p)
    assert cfg.sample_interval_seconds == 15
    assert cfg.health_history_days == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -k sampler -v`
Expected: FAIL with `AttributeError: 'FlexConfig' object has no attribute 'sample_interval_seconds'`

- [ ] **Step 3: Add fields to `FlexConfig` and parse them**

In the `FlexConfig` dataclass add (after `dashboard_port`):

```python
    sample_interval_seconds: int = 60
    health_history_days: int = 30
```

In `load_config`'s `return FlexConfig(...)` add:

```python
        sample_interval_seconds=int(settings.get("sample_interval_seconds", 60)),
        health_history_days=int(settings.get("health_history_days", 30)),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -k sampler -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add flexrouter/config.py tests/test_config.py
git commit -m "feat: sampler/retention config settings"
```

---

## Task 6: Config validation (collect-all + modality heuristics)

**Files:**
- Modify: `flexrouter/config.py` (add `validate_config`)
- Test: `tests/test_config_validate.py`

**Interfaces:**
- Produces: `validate_config(raw: dict) -> dict` returning `{"errors": list[str], "warnings": list[str]}`. Pure function over the parsed YAML dict (no file IO), so the endpoint and tests can call it directly. Each message names tier + index + field.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_validate.py
from flexrouter.config import validate_config

def _base():
    return {
        "providers": {"groq": {"base_url": "https://api.groq.com/openai/v1", "api_keys": ["k"]}},
        "tiers": {"default": [{"provider": "groq", "model": "llama-3.1-8b-instant",
                               "score": 50, "rpm": 30, "tpm": 6000, "context_window": 131072}]},
    }

def test_clean_config_has_no_errors():
    r = validate_config(_base())
    assert r["errors"] == []

def test_unknown_provider_reference_is_error():
    raw = _base()
    raw["tiers"]["default"][0]["provider"] = "nope"
    r = validate_config(raw)
    assert any("nope" in e and "default[0]" in e for e in r["errors"])

def test_non_positive_rpm_is_error():
    raw = _base()
    raw["tiers"]["default"][0]["rpm"] = 0
    r = validate_config(raw)
    assert any("rpm" in e and "default[0]" in e for e in r["errors"])

def test_low_context_window_warns_modality():
    raw = _base()
    raw["tiers"]["default"][0]["context_window"] = 448
    r = validate_config(raw)
    assert any("context_window" in w for w in r["warnings"])

def test_non_chat_name_warns_modality():
    raw = _base()
    raw["tiers"]["default"][0]["model"] = "whisper-large-v3"
    r = validate_config(raw)
    assert any("whisper-large-v3" in w for w in r["warnings"])

def test_empty_keys_non_local_warns():
    raw = _base()
    raw["providers"]["groq"]["api_keys"] = []
    r = validate_config(raw)
    assert any("groq" in w and "api_keys" in w for w in r["warnings"])

def test_collects_multiple_errors():
    raw = _base()
    raw["tiers"]["default"][0]["provider"] = "nope"
    raw["tiers"]["default"][0]["tpm"] = -5
    r = validate_config(raw)
    assert len(r["errors"]) >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_validate.py -v`
Expected: FAIL with `ImportError: cannot import name 'validate_config'`

- [ ] **Step 3: Implement `validate_config` in `config.py`**

```python
_NON_CHAT_PATTERNS = ("whisper", "tts", "orpheus", "image", "lyria", "guard",
                      "native-audio", "-live", "embedding", "rerank")
_LOCAL_HOST_HINTS = ("localhost", "127.0.0.1", "::1")


def validate_config(raw: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    providers = raw.get("providers") or {}
    if not providers:
        errors.append("providers: no providers defined")

    for name, praw in providers.items():
        if not isinstance(praw, dict) or not praw.get("base_url"):
            errors.append(f"providers.{name}: missing base_url")
            continue
        base_url = str(praw.get("base_url", ""))
        if not base_url.startswith(("http://", "https://")):
            errors.append(f"providers.{name}.base_url: must start with http:// or https://")
        keys = praw.get("api_keys", [])
        is_local = any(h in base_url for h in _LOCAL_HOST_HINTS)
        if not keys and not is_local:
            warnings.append(f"providers.{name}.api_keys: empty for a non-local provider")

    tiers = raw.get("tiers") or {}
    if not tiers:
        errors.append("tiers: no tiers defined")

    for tier_name, models in tiers.items():
        for i, m in enumerate(models or []):
            where = f"tiers.{tier_name}[{i}]"
            if not isinstance(m, dict):
                errors.append(f"{where}: not a mapping")
                continue
            provider = m.get("provider")
            model = m.get("model")
            if not provider:
                errors.append(f"{where}.provider: missing")
            elif provider not in providers:
                errors.append(f"{where}.provider: {provider!r} not defined in providers")
            if not model:
                errors.append(f"{where}.model: missing")
            for field_name in ("score", "rpm", "tpm"):
                val = m.get(field_name)
                if val is None:
                    errors.append(f"{where}.{field_name}: missing")
                elif not isinstance(val, (int, float)) or val <= 0:
                    errors.append(f"{where}.{field_name}: must be a positive number, got {val!r}")
            ctx = m.get("context_window")
            if isinstance(ctx, (int, float)) and ctx < 1000:
                warnings.append(
                    f"{where}.context_window: {ctx} is suspiciously low — "
                    f"{model!r} may not be a chat model")
            if model and any(p in str(model).lower() for p in _NON_CHAT_PATTERNS):
                warnings.append(
                    f"{where}: {model!r} matches a non-chat name pattern — "
                    f"likely not a chat-completions model")

    return {"errors": errors, "warnings": warnings}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config_validate.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add flexrouter/config.py tests/test_config_validate.py
git commit -m "feat: validate_config collects all errors + modality warnings"
```

---

## Task 7: PassiveSampler thread

**Files:**
- Create: `flexrouter/sampler.py`
- Test: `tests/test_sampler.py`

**Interfaces:**
- Consumes: a `snapshot_fn: Callable[[], dict]` (the engine's `health_snapshot`) and a `sink_fn: Callable[[dict], None]` (HealthHistory's `record`). Decoupled from concrete classes for testability.
- Produces: `PassiveSampler(snapshot_fn, sink_fn, interval_seconds: int)`; methods `start()`, `stop()`, `sample_once()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sampler.py
import time
from flexrouter.sampler import PassiveSampler

def test_sample_once_pipes_snapshot_to_sink():
    sunk = []
    s = PassiveSampler(lambda: {"models": {"a": 1}}, sunk.append, interval_seconds=60)
    s.sample_once()
    assert sunk == [{"models": {"a": 1}}]

def test_start_samples_then_stop_halts():
    sunk = []
    s = PassiveSampler(lambda: {"n": len(sunk)}, sunk.append, interval_seconds=1)
    # interval of 0.05s via override for a fast test
    s._interval = 0.05
    s.start()
    time.sleep(0.17)
    s.stop()
    count_at_stop = len(sunk)
    assert count_at_stop >= 2
    time.sleep(0.12)
    assert len(sunk) == count_at_stop  # no more samples after stop

def test_sink_exception_does_not_kill_thread():
    calls = []
    def bad_sink(_):
        calls.append(1)
        raise RuntimeError("disk full")
    s = PassiveSampler(lambda: {}, bad_sink, interval_seconds=1)
    s._interval = 0.05
    s.start()
    time.sleep(0.17)
    s.stop()
    assert len(calls) >= 2  # kept going despite exceptions
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sampler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flexrouter.sampler'`

- [ ] **Step 3: Write minimal implementation**

```python
# flexrouter/sampler.py
from __future__ import annotations
import threading
from typing import Callable


class PassiveSampler:
    """Periodically samples in-memory engine state into a sink. No network IO."""

    def __init__(self, snapshot_fn: Callable[[], dict], sink_fn: Callable[[dict], None],
                 interval_seconds: int) -> None:
        self._snapshot = snapshot_fn
        self._sink = sink_fn
        self._interval = float(interval_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sample_once(self) -> None:
        self._sink(self._snapshot())

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception:
                pass  # a sampler must never die on a transient error
            self._stop.wait(self._interval)

    def start(self) -> None:
        if self._interval <= 0 or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="flexrouter-sampler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sampler.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add flexrouter/sampler.py tests/test_sampler.py
git commit -m "feat: PassiveSampler daemon (no-network health sampling)"
```

---

## Task 8: Wire persistence into FlexRouter

**Files:**
- Modify: `flexrouter/_router.py`
- Test: `tests/test_router_wiring.py`

**Interfaces:**
- Consumes: `EventLogger` (Task 1), persisted `PenaltyBox` (Task 2), `HealthHistory` (Task 3), `health_snapshot` (Task 4), config fields (Task 5), `PassiveSampler` (Task 7).
- Produces: a `FlexRouter` that (a) constructs `PenaltyBox` with `state_dir` + `on_event` wired to `EventLogger.record`, (b) writes a health sample after each request, (c) records `rate_limited`/`server_error` events at failure sites, (d) starts a `PassiveSampler` and stops it in `close()`.

> The engine currently builds its own `PenaltyBox` in `RoutingEngine.__init__`. To wire persistence + events without breaking the engine's encapsulation, accept an optional injected box: add `penalties: PenaltyBox | None = None` to `RoutingEngine.__init__` and use it when provided, else build the default. Update Task 4's engine accordingly.

- [ ] **Step 1: Add optional penalty injection to RoutingEngine**

In `engine.py` `__init__`, change the penalty line to:

```python
        self._penalties = penalties if penalties is not None else PenaltyBox(
            cfg.penalty_base_seconds, cfg.penalty_max_seconds)
```

and add `penalties: Optional["PenaltyBox"] = None` to the signature.

- [ ] **Step 2: Write the failing wiring test**

```python
# tests/test_router_wiring.py
import json
from pathlib import Path
import pytest
from flexrouter._router import FlexRouter

CONFIG = """
providers:
  groq:
    base_url: https://api.groq.com/openai/v1
    api_keys: [test-key]
tiers:
  default:
    - {provider: groq, model: llama-3.1-8b-instant, score: 50, rpm: 30, tpm: 6000, context_window: 131072}
settings:
  state_dir: STATE
  sample_interval_seconds: 0
"""

def _make(tmp_path) -> FlexRouter:
    state = tmp_path / "st"
    cfg = tmp_path / "flexrouter.yaml"
    cfg.write_text(CONFIG.replace("STATE", str(state).replace("\\\\", "/")))
    return FlexRouter(config_path=str(cfg))

def test_router_constructs_persistence_files(tmp_path):
    r = _make(tmp_path)
    try:
        state = Path(r._cfg.state_dir)
        assert (state / "events.csv").exists()
    finally:
        r.close()

def test_failed_request_writes_event_and_sample(tmp_path, monkeypatch):
    from flexrouter.client import RateLimitError
    r = _make(tmp_path)
    async def boom(*a, **k):
        raise RateLimitError("429")
    monkeypatch.setattr(r._client, "chat", boom)
    try:
        with pytest.raises(Exception):
            r.generate([{"role": "user", "content": "hi"}], tier="default", wait=False)
        state = Path(r._cfg.state_dir)
        events = (state / "events.csv").read_text()
        assert "rate_limited" in events
        assert (state / "health_history.jsonl").exists()
    finally:
        r.close()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_router_wiring.py -v`
Expected: FAIL (no `events.csv`, or AttributeError on missing wiring).

- [ ] **Step 4: Wire it in `_router.py`**

Add imports:

```python
from flexrouter.events import EventLogger
from flexrouter.health_history import HealthHistory
from flexrouter.recovery import PenaltyBox
from flexrouter.sampler import PassiveSampler
```

In `__init__`, replace the engine/audit construction block with:

```python
        self._events = EventLogger(self._cfg.state_dir)
        self._penalties = PenaltyBox(
            self._cfg.penalty_base_seconds, self._cfg.penalty_max_seconds,
            state_dir=self._cfg.state_dir, on_event=self._events.record,
        )
        self._engine = RoutingEngine(
            self._cfg, rate_limit_store=self._rate_limit_store, penalties=self._penalties)
        self._audit = AuditLogger(self._cfg.state_dir)
        self._history = HealthHistory(self._cfg.state_dir, self._cfg.health_history_days)
        self._sampler = PassiveSampler(
            self._engine.health_snapshot, self._history.record,
            self._cfg.sample_interval_seconds)
        self._sampler.start()
```

In `agenerate`, in the `except RateLimitError` block, after `self._engine.penalize(...)` add:

```python
                self._events.record(route.provider, route.model, "rate_limited",
                                    penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
                self._history.record(self._engine.health_snapshot())
```

In the `except ProviderError` block, after `self._engine.penalize(...)` add:

```python
                self._events.record(route.provider, route.model, "server_error",
                                    penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
                self._history.record(self._engine.health_snapshot())
```

After the success `self._audit.log(... status="ok")` and before `return result` add:

```python
            self._history.record(self._engine.health_snapshot())
```

In `close()` add as the first line:

```python
        self._sampler.stop()
```

In `reload()`, rebuild history retention (penalties/events keep their instances):

```python
        self._history = HealthHistory(self._cfg.state_dir, self._cfg.health_history_days)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_router_wiring.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Run the full suite (no regressions)**

Run: `python -m pytest -q`
Expected: PASS (all existing + new tests green)

- [ ] **Step 7: Commit**

```bash
git add flexrouter/_router.py flexrouter/engine.py tests/test_router_wiring.py
git commit -m "feat: wire events, health history, and passive sampler into FlexRouter"
```

---

## Task 9: Stats aggregation

**Files:**
- Create: `flexrouter/dashboard/stats.py`
- Test: `tests/test_stats.py`

**Interfaces:**
- Produces: `compute_stats(state_dir: str) -> dict` reading `audit.csv`, returning:
  `{"totals": {"requests","this_hour","peak_rpm"}, "hourly": [{"hour": iso, "requests": int}], "latency": {"per_model": [{"model","p50","p95","count"}], "histogram": [{"bucket_ms","count"}]}, "distribution": {"by_provider": [{"provider","requests"}], "top_models": [{"model","requests"}], "diversity": float}, "errors": {"rate_by_hour": [{"hour","total","errors","rate"}], "by_type": [{"status","count"}], "per_model": [{"model","requests","errors","rate"}]}, "tiers": {"by_tier": [{"tier","requests"}]}}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stats.py
import csv
from flexrouter.audit import AuditLogger
from flexrouter.dashboard.stats import compute_stats

def _seed(tmp_path):
    log = AuditLogger(str(tmp_path))
    log.log("default", "groq", "llama", 100, 50, 0.0, 200, "ok")
    log.log("default", "groq", "llama", 100, 50, 0.0, 400, "ok")
    log.log("default", "openrouter", "kimi", 100, 50, 0.0, 600, "ok")
    log.log("default", "groq", "llama", 0, 0, 0.0, 100, "rate_limited")
    return tmp_path

def test_totals(tmp_path):
    s = compute_stats(str(_seed(tmp_path)))
    assert s["totals"]["requests"] == 4

def test_per_model_latency_percentiles(tmp_path):
    s = compute_stats(str(_seed(tmp_path)))
    llama = next(m for m in s["latency"]["per_model"] if m["model"] == "groq/llama")
    assert llama["count"] == 3
    assert llama["p50"] >= 200

def test_error_breakdown(tmp_path):
    s = compute_stats(str(_seed(tmp_path)))
    by_type = {e["status"]: e["count"] for e in s["errors"]["by_type"]}
    assert by_type["rate_limited"] == 1
    assert by_type["ok"] == 3

def test_distribution_by_provider(tmp_path):
    s = compute_stats(str(_seed(tmp_path)))
    by_prov = {d["provider"]: d["requests"] for d in s["distribution"]["by_provider"]}
    assert by_prov["groq"] == 3
    assert by_prov["openrouter"] == 1

def test_empty_state_returns_zeros(tmp_path):
    s = compute_stats(str(tmp_path))
    assert s["totals"]["requests"] == 0
    assert s["latency"]["per_model"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_stats.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flexrouter.dashboard.stats'`

- [ ] **Step 3: Write the implementation**

```python
# flexrouter/dashboard/stats.py
from __future__ import annotations
import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _hour_key(ts: str) -> str:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.replace(minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def compute_stats(state_dir: str) -> dict:
    path = Path(state_dir) / "audit.csv"
    rows: list[dict] = []
    if path.exists():
        with path.open() as f:
            rows = list(csv.DictReader(f))

    empty = {
        "totals": {"requests": 0, "this_hour": 0, "peak_rpm": 0},
        "hourly": [], "latency": {"per_model": [], "histogram": []},
        "distribution": {"by_provider": [], "top_models": [], "diversity": 0.0},
        "errors": {"rate_by_hour": [], "by_type": [], "per_model": []},
        "tiers": {"by_tier": []},
    }
    if not rows:
        return empty

    by_hour = Counter()
    by_hour_err = Counter()
    lat_by_model = defaultdict(list)
    req_by_model = Counter()
    err_by_model = Counter()
    by_provider = Counter()
    by_status = Counter()
    by_tier = Counter()
    all_latencies: list[float] = []

    for r in rows:
        model = f"{r['provider']}/{r['model']}"
        status = r["status"]
        hour = _hour_key(r["timestamp"])
        by_hour[hour] += 1
        by_status[status] += 1
        by_provider[r["provider"]] += 1
        by_tier[r["tier"]] += 1
        req_by_model[model] += 1
        if status != "ok":
            by_hour_err[hour] += 1
            err_by_model[model] += 1
        else:
            lat = float(r["latency_ms"] or 0)
            lat_by_model[model].append(lat)
            all_latencies.append(lat)

    hours_sorted = sorted(by_hour)
    this_hour_key = _hour_key(datetime.now(timezone.utc).isoformat(timespec="seconds"))

    buckets = [(0, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 5000), (5000, 10**9)]
    histogram = []
    for lo, hi in buckets:
        label = f"{lo}-{hi}" if hi < 10**9 else f"{lo}+"
        histogram.append({"bucket_ms": label, "count": sum(1 for v in all_latencies if lo <= v < hi)})

    total = len(rows)
    diversity = 0.0
    if total:
        import math
        for c in by_provider.values():
            p = c / total
            diversity -= p * math.log(p, 2) if p > 0 else 0.0

    return {
        "totals": {
            "requests": total,
            "this_hour": by_hour.get(this_hour_key, 0),
            "peak_rpm": max(by_hour.values()) if by_hour else 0,
        },
        "hourly": [{"hour": h, "requests": by_hour[h]} for h in hours_sorted],
        "latency": {
            "per_model": [
                {"model": m, "p50": round(_percentile(v, 0.5)),
                 "p95": round(_percentile(v, 0.95)), "count": len(v)}
                for m, v in sorted(lat_by_model.items())
            ],
            "histogram": histogram,
        },
        "distribution": {
            "by_provider": [{"provider": p, "requests": c} for p, c in by_provider.most_common()],
            "top_models": [{"model": m, "requests": c} for m, c in req_by_model.most_common(10)],
            "diversity": round(diversity, 3),
        },
        "errors": {
            "rate_by_hour": [
                {"hour": h, "total": by_hour[h], "errors": by_hour_err.get(h, 0),
                 "rate": round(by_hour_err.get(h, 0) / by_hour[h], 3) if by_hour[h] else 0.0}
                for h in hours_sorted
            ],
            "by_type": [{"status": s, "count": c} for s, c in by_status.most_common()],
            "per_model": [
                {"model": m, "requests": req_by_model[m], "errors": err_by_model.get(m, 0),
                 "rate": round(err_by_model.get(m, 0) / req_by_model[m], 3) if req_by_model[m] else 0.0}
                for m in sorted(req_by_model)
            ],
        },
        "tiers": {"by_tier": [{"tier": t, "requests": c} for t, c in by_tier.most_common()]},
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_stats.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add flexrouter/dashboard/stats.py tests/test_stats.py
git commit -m "feat: server-side stats aggregation from audit.csv"
```

---

## Task 10: Uptime aggregation

**Files:**
- Create: `flexrouter/dashboard/uptime.py`
- Test: `tests/test_uptime.py`

**Interfaces:**
- Consumes: `HealthHistory` JSONL + `events.csv`.
- Produces: `compute_uptime(state_dir: str, now: datetime | None = None) -> dict` returning:
  `{"models": [{"model","uptime_24h","uptime_7d","uptime_30d","segments": [{"start","end","state"}]}], "incidents": [{"start","end","provider","model","event_type","duration_seconds"}], "system": {"uptime_24h": float}, "providers": [{"provider","uptime_24h"}]}`. `state` in segments ∈ `{"up","down","nodata"}`. Uptime fractions are 0.0–1.0 computed as up-samples / total-samples in the window (no samples → `None`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_uptime.py
from datetime import datetime, timezone, timedelta
from flexrouter.health_history import HealthHistory
from flexrouter.events import EventLogger
from flexrouter.dashboard.uptime import compute_uptime

def _iso(dt): return dt.isoformat(timespec="seconds")

def test_uptime_fraction_from_samples(tmp_path):
    hh = HealthHistory(str(tmp_path))
    now = datetime.now(timezone.utc)
    for i in range(10):
        state = "up" if i < 8 else "penalized"
        hh.record({"timestamp": _iso(now - timedelta(minutes=i)),
                   "models": {"groq/llama": {"status": state, "penalized": state == "penalized"}}})
    u = compute_uptime(str(tmp_path), now=now)
    model = next(m for m in u["models"] if m["model"] == "groq/llama")
    assert 0.7 <= model["uptime_24h"] <= 0.85

def test_no_samples_is_none(tmp_path):
    u = compute_uptime(str(tmp_path))
    assert u["models"] == []

def test_incidents_from_events(tmp_path):
    ev = EventLogger(str(tmp_path))
    ev.record("groq", "llama", "penalized", penalty_seconds=30)
    ev.record("groq", "llama", "recovered")
    u = compute_uptime(str(tmp_path))
    assert len(u["incidents"]) >= 1
    assert u["incidents"][0]["provider"] == "groq"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_uptime.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flexrouter.dashboard.uptime'`

- [ ] **Step 3: Write the implementation**

```python
# flexrouter/dashboard/uptime.py
from __future__ import annotations
import csv
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flexrouter.health_history import HealthHistory


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _fraction(samples: list[dict], cutoff: datetime):
    total = up = 0
    for ts, state in samples:
        if ts >= cutoff:
            total += 1
            if state == "up":
                up += 1
    return (up / total) if total else None


def compute_uptime(state_dir: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    hh = HealthHistory(state_dir)
    history = hh.read()

    # model -> list[(timestamp, state)] where state in {"up","down"}
    per_model: dict[str, list] = defaultdict(list)
    prov_samples: dict[str, list] = defaultdict(list)
    for sample in history:
        ts = _parse(sample["timestamp"])
        for model, info in (sample.get("models") or {}).items():
            state = "up" if info.get("status") == "up" else "down"
            per_model[model].append((ts, state))
            prov_samples[model.split("/")[0]].append((ts, state))

    c24 = now - timedelta(hours=24)
    c7 = now - timedelta(days=7)
    c30 = now - timedelta(days=30)

    models = []
    for model, samples in sorted(per_model.items()):
        samples.sort(key=lambda x: x[0])
        segments = [{"start": ts.isoformat(timespec="seconds"),
                     "end": ts.isoformat(timespec="seconds"),
                     "state": state if state == "up" else "down"}
                    for ts, state in samples if ts >= c24]
        models.append({
            "model": model,
            "uptime_24h": _fraction(samples, c24),
            "uptime_7d": _fraction(samples, c7),
            "uptime_30d": _fraction(samples, c30),
            "segments": segments,
        })

    providers = [{"provider": p, "uptime_24h": _fraction(s, c24)}
                 for p, s in sorted(prov_samples.items())]

    sys_total = sys_up = 0
    for s in per_model.values():
        for ts, state in s:
            if ts >= c24:
                sys_total += 1
                sys_up += 1 if state == "up" else 0
    system = {"uptime_24h": (sys_up / sys_total) if sys_total else None}

    # Incidents: pair penalized -> next recovered per model
    incidents = []
    epath = Path(state_dir) / "events.csv"
    if epath.exists():
        with epath.open() as f:
            evs = list(csv.DictReader(f))
        open_inc: dict[str, dict] = {}
        for e in evs:
            key = f"{e['provider']}/{e['model']}"
            if e["event_type"] in ("penalized", "rate_limited", "server_error", "timeout"):
                open_inc.setdefault(key, {
                    "start": e["timestamp"], "provider": e["provider"],
                    "model": e["model"], "event_type": e["event_type"]})
            elif e["event_type"] == "recovered" and key in open_inc:
                inc = open_inc.pop(key)
                start, end = _parse(inc["start"]), _parse(e["timestamp"])
                inc["end"] = e["timestamp"]
                inc["duration_seconds"] = int((end - start).total_seconds())
                incidents.append(inc)
        for inc in open_inc.values():  # still-open incidents
            inc["end"] = None
            inc["duration_seconds"] = None
            incidents.append(inc)

    return {"models": models, "incidents": incidents, "system": system, "providers": providers}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_uptime.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add flexrouter/dashboard/uptime.py tests/test_uptime.py
git commit -m "feat: uptime aggregation from health history + events"
```

---

## Task 11: New API endpoints

**Files:**
- Modify: `flexrouter/dashboard/api.py`
- Modify: `flexrouter/dashboard/server.py` (`_handle_api_get`, ~lines 64-74)
- Test: `tests/test_dashboard_api.py`

**Interfaces:**
- Consumes: `compute_stats` (Task 9), `compute_uptime` (Task 10), `validate_config` (Task 6), `get_config` (existing).
- Produces: `api.get_stats(state_dir)`, `api.get_uptime(state_dir)`, `api.get_config_validation()`; routes `/api/stats`, `/api/uptime`, `/api/config/validate`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_api.py
from flexrouter.audit import AuditLogger
from flexrouter.dashboard import api

def test_get_stats_wraps_compute(tmp_path):
    AuditLogger(str(tmp_path)).log("default", "groq", "llama", 1, 1, 0.0, 100, "ok")
    s = api.get_stats(str(tmp_path))
    assert s["totals"]["requests"] == 1

def test_get_uptime_shape(tmp_path):
    u = api.get_uptime(str(tmp_path))
    assert set(u) == {"models", "incidents", "system", "providers"}

def test_get_config_validation_shape(monkeypatch):
    monkeypatch.setattr(api, "get_config", lambda: {
        "providers": {"groq": {"base_url": "https://x.com/v1", "api_keys": ["k"]}},
        "tiers": {"default": [{"provider": "groq", "model": "m", "score": 50, "rpm": 1, "tpm": 1}]},
    })
    v = api.get_config_validation()
    assert "errors" in v and "warnings" in v
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dashboard_api.py -v`
Expected: FAIL with `AttributeError: module 'flexrouter.dashboard.api' has no attribute 'get_stats'`

- [ ] **Step 3: Add functions to `api.py`**

```python
from flexrouter.config import validate_config
from flexrouter.dashboard.stats import compute_stats
from flexrouter.dashboard.uptime import compute_uptime


def get_stats(state_dir: str) -> dict:
    return compute_stats(state_dir)


def get_uptime(state_dir: str) -> dict:
    return compute_uptime(state_dir)


def get_config_validation() -> dict:
    return validate_config(get_config())
```

- [ ] **Step 4: Add routes in `server.py` `_handle_api_get`**

Inside the `if/elif` chain (before the final `else`):

```python
            elif self.path == "/api/stats":
                self._send_json(get_stats(state))
            elif self.path == "/api/uptime":
                self._send_json(get_uptime(state))
            elif self.path == "/api/config/validate":
                self._send_json(get_config_validation())
```

And update the import at the top of `server.py`:

```python
from flexrouter.dashboard.api import (
    get_config, get_config_validation, get_logs, get_stats, get_status, get_uptime, post_config,
)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_dashboard_api.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (all green)

- [ ] **Step 7: Commit**

```bash
git add flexrouter/dashboard/api.py flexrouter/dashboard/server.py tests/test_dashboard_api.py
git commit -m "feat: /api/stats, /api/uptime, /api/config/validate endpoints"
```

---

## Self-Review Notes

- **Spec coverage:** durable health time-series (Task 3), incident ledger (Task 1), persisted penalties (Task 2), passive sampler with no quota cost (Task 7), engine snapshot (Task 4), config settings (Task 5), config validation with modality heuristics (Task 6), wiring (Task 8), stats aggregation = 5 categories' data (Task 9), uptime aggregation (Task 10), endpoints (Task 11). Money-saved intentionally absent (cut). Frontend deferred to Plans 2 & 3.
- **Type consistency:** `EventLogger.record(provider, model, event_type, detail, penalty_seconds)` is the single event signature, consumed by `PenaltyBox.on_event` (4-arg form, no detail) — wiring in Task 8 calls `record` positionally for the 4-arg case, which is valid since `detail` defaults to `""`. `health_snapshot()` shape matches what `compute_uptime` reads (`sample["models"][m]["status"]`).
- **Deferred risk:** `compute_uptime` segments are per-sample points (start==end); Plan 3's frontend renders them as a timeline. Run-length merging of adjacent same-state samples can be added in Plan 3 if the payload proves large.
```
