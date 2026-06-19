# Model Discovery & Rate-Limit Gauging — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual `flexrouter refresh` that re-discovers models + real rate limits and rewrites `flexrouter.yaml` (with backup + diff), plus a rate-limit gauging layer that captures live headroom from response headers so the router proactively skips models that are guaranteed to 429.

**Architecture:** A new `flexrouter/refresh.py` reuses `onboard.py`'s discovery/scoring/`build_yaml`. `RateLimitStore` grows a headroom layer fed by `client.py` header parsing; the engine gates candidates on provider-reported exhaustion. A shared modality helper in `config.py` is used by both refresh and `validate_config`.

**Tech Stack:** Python 3 stdlib + httpx (already a dep) + click (already a dep). pytest with monkeypatch for discovery mocking. No new dependencies.

**Source spec:** `docs/superpowers/specs/2026-06-19-model-discovery-rate-limits-design.md`.

## Global Constraints

- No new dependencies. Reuse `onboard.discover_models`, `discover_ollama`, `score_with_aa`, `build_yaml`, `_context_window`, and the `PROVIDERS` registry as-is except where a task explicitly extends them.
- API keys are read from the parsed config and re-emitted by `build_yaml` — never logged, never reordered, never dropped.
- A config rewrite ALWAYS backs up the current file first (`state_dir/backups/flexrouter-<UTC>.yaml`). If the rewrite raises, the original file is left intact.
- Header/duration parsing is best-effort: a malformed field degrades to `None` and never raises.
- Timestamps: `datetime.now(timezone.utc).isoformat(timespec="seconds")`.
- **Branch base:** `master`. The spec's "branch off feat/dashboard-backend" risk is resolved — that branch is merged, and `config.py` already holds the patterns as the private `_NON_CHAT_PATTERNS`. Task 1 promotes it to the shared public helper.
- No active probing of providers. Discovery uses `GET /v1/models` only; headroom comes from real traffic headers.

---

## File Structure

- Modify `flexrouter/config.py` — public `NON_CHAT_PATTERNS` + `is_probably_chat_model`; `validate_config` consumes it (Task 1).
- Modify `flexrouter/onboard.py` — `build_yaml` honors per-model `rpm`/`tpm` overrides (Task 2).
- Modify `flexrouter/rate_limits.py` — headroom: `update_headroom`, `is_exhausted`, `available_at` (Task 3).
- Modify `flexrouter/client.py` — parse `remaining`/`reset` headers + `_parse_duration_ms` (Task 4).
- Modify `flexrouter/engine.py` — skip exhausted models in candidate selection (Task 5).
- Create `flexrouter/refresh.py` — `refresh_config` + `RefreshResult` (Task 6).
- Modify `flexrouter/cli.py` — `refresh` command (Task 7).
- Modify `flexrouter/dashboard/api.py` + `server.py` — `GET`/`POST /api/refresh` (Task 8).
- Tests mirror under `tests/`.

---

## Task 1: Shared modality helper (DRY)

**Files:**
- Modify: `flexrouter/config.py`
- Test: `tests/test_config_validate.py` (add a case)

**Interfaces:**
- Produces: public `NON_CHAT_PATTERNS: tuple[str, ...]` and `is_probably_chat_model(model_id: str) -> bool`. `validate_config`'s modality check is re-expressed via `is_probably_chat_model`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_config_validate.py
def test_is_probably_chat_model():
    from flexrouter.config import is_probably_chat_model
    assert is_probably_chat_model("llama-3.1-8b-instant") is True
    assert is_probably_chat_model("whisper-large-v3") is False
    assert is_probably_chat_model("models/gemini-2.5-flash-image") is False
    assert is_probably_chat_model("google/lyria-3-pro-preview") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_validate.py -k is_probably -v`
Expected: FAIL — `cannot import name 'is_probably_chat_model'`.

- [ ] **Step 3: Refactor `config.py`**

Replace the private `_NON_CHAT_PATTERNS` line with a public constant + helper, and update the existing modality warning to use it:

```python
NON_CHAT_PATTERNS = ("whisper", "tts", "orpheus", "image", "lyria", "guard",
                     "native-audio", "-live", "embedding", "rerank")
_LOCAL_HOST_HINTS = ("localhost", "127.0.0.1", "::1")


def is_probably_chat_model(model_id: str) -> bool:
    mid = (model_id or "").lower()
    return not any(p in mid for p in NON_CHAT_PATTERNS)
```

In `validate_config`, change the modality warning condition from
`if model and any(p in str(model).lower() for p in _NON_CHAT_PATTERNS):`
to:

```python
            if model and not is_probably_chat_model(str(model)):
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config_validate.py -v`
Expected: PASS (existing 7 + new is_probably test).

- [ ] **Step 5: Commit**

```bash
git add flexrouter/config.py tests/test_config_validate.py
git commit -m "refactor: shared NON_CHAT_PATTERNS + is_probably_chat_model"
```

---

## Task 2: build_yaml honors per-model rate-limit overrides

**Files:**
- Modify: `flexrouter/onboard.py` (`build_yaml.model_block`, ~line 247)
- Test: `tests/test_onboard.py` (add a case)

**Why:** `build_yaml` currently writes `pdef.default_rpm`/`default_tpm` for every model. Refresh needs to write *real* per-model ceilings (from `RateLimitStore`) when known, falling back to provider defaults.

**Interfaces:**
- Produces: `model_block` uses `m.get("rpm")` / `m.get("tpm")` when present and positive, else `pdef.default_rpm` / `pdef.default_tpm`. Backward compatible (existing callers omit these keys).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_onboard.py
def test_build_yaml_uses_per_model_rpm_override():
    from flexrouter.onboard import build_yaml, PROVIDERS
    free = [{"_provider": "groq", "id": "llama", "score": 50, "rpm": 999, "tpm": 7777}]
    out = build_yaml({"groq": "k"}, free, [], PROVIDERS)
    assert "rpm: 999" in out
    assert "tpm: 7777" in out

def test_build_yaml_falls_back_to_provider_defaults():
    from flexrouter.onboard import build_yaml, PROVIDERS
    free = [{"_provider": "groq", "id": "llama", "score": 50}]
    out = build_yaml({"groq": "k"}, free, [], PROVIDERS)
    assert "rpm: 30" in out  # groq default_rpm
    assert "tpm: 6000" in out  # groq default_tpm
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_onboard.py -k build_yaml_uses_per_model -v`
Expected: FAIL — output contains `rpm: 30`, not `rpm: 999`.

- [ ] **Step 3: Edit `model_block` in `build_yaml`**

```python
    def model_block(m: dict, pdef: ProviderDef) -> list[str]:
        rpm = m["rpm"] if isinstance(m.get("rpm"), int) and m["rpm"] > 0 else pdef.default_rpm
        tpm = m["tpm"] if isinstance(m.get("tpm"), int) and m["tpm"] > 0 else pdef.default_tpm
        return [
            f"    - provider: {m['_provider']}",
            f"      model: {m['id']}",
            f"      score: {m['score']}",
            f"      rpm: {rpm}",
            f"      tpm: {tpm}",
            f"      context_window: {_context_window(m)}",
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_onboard.py -k build_yaml -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add flexrouter/onboard.py tests/test_onboard.py
git commit -m "feat: build_yaml honors per-model rpm/tpm overrides"
```

---

## Task 3: RateLimitStore headroom

**Files:**
- Modify: `flexrouter/rate_limits.py`
- Test: `tests/test_rate_limits.py` (add cases)

**Interfaces:**
- Produces: `update_headroom(provider, model, remaining_requests=None, remaining_tokens=None, reset_requests_at=None, reset_tokens_at=None)` (ignores `None`); `is_exhausted(provider, model) -> bool` (true when a remaining counter `<= 0` and `now < its reset_at`); `available_at(provider, model) -> float | None` (earliest reset among exhausted dimensions). Headroom persists to the same `rate_limits.json`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_rate_limits.py
import time
from flexrouter.rate_limits import RateLimitStore

def test_update_headroom_persists(tmp_path):
    s = RateLimitStore(str(tmp_path))
    s.update_headroom("groq", "llama", remaining_requests=5, reset_requests_at=time.time() + 60)
    s2 = RateLimitStore(str(tmp_path))
    assert s2._data["groq/llama"]["remaining_requests"] == 5

def test_is_exhausted_true_before_reset(tmp_path):
    s = RateLimitStore(str(tmp_path))
    s.update_headroom("groq", "llama", remaining_requests=0, reset_requests_at=time.time() + 60)
    assert s.is_exhausted("groq", "llama") is True

def test_is_exhausted_false_after_reset(tmp_path):
    s = RateLimitStore(str(tmp_path))
    s.update_headroom("groq", "llama", remaining_requests=0, reset_requests_at=time.time() - 1)
    assert s.is_exhausted("groq", "llama") is False

def test_not_exhausted_when_headroom_remains(tmp_path):
    s = RateLimitStore(str(tmp_path))
    s.update_headroom("groq", "llama", remaining_requests=10, reset_requests_at=time.time() + 60)
    assert s.is_exhausted("groq", "llama") is False

def test_available_at_returns_earliest_reset(tmp_path):
    s = RateLimitStore(str(tmp_path))
    now = time.time()
    s.update_headroom("groq", "llama", remaining_requests=0, reset_requests_at=now + 30,
                      remaining_tokens=0, reset_tokens_at=now + 90)
    assert abs(s.available_at("groq", "llama") - (now + 30)) < 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_rate_limits.py -k "headroom or exhausted or available_at" -v`
Expected: FAIL — `AttributeError: 'RateLimitStore' object has no attribute 'update_headroom'`.

- [ ] **Step 3: Extend `rate_limits.py`**

Add `import time` at the top, then these methods to `RateLimitStore`:

```python
    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2))

    def update_headroom(self, provider: str, model: str,
                        remaining_requests: int | None = None,
                        remaining_tokens: int | None = None,
                        reset_requests_at: float | None = None,
                        reset_tokens_at: float | None = None) -> None:
        if self._path is None:
            return
        key = f"{provider}/{model}"
        entry = dict(self._data.get(key, {}))
        for field_name, val in (
            ("remaining_requests", remaining_requests),
            ("remaining_tokens", remaining_tokens),
            ("reset_requests_at", reset_requests_at),
            ("reset_tokens_at", reset_tokens_at),
        ):
            if val is not None:
                entry[field_name] = val
        self._data[key] = entry
        self._persist()

    def _exhausted_resets(self, provider: str, model: str) -> list[float]:
        entry = self._data.get(f"{provider}/{model}", {})
        now = time.time()
        resets: list[float] = []
        for rem_key, reset_key in (("remaining_requests", "reset_requests_at"),
                                   ("remaining_tokens", "reset_tokens_at")):
            rem = entry.get(rem_key)
            reset = entry.get(reset_key)
            if rem is not None and rem <= 0 and reset is not None and now < reset:
                resets.append(reset)
        return resets

    def is_exhausted(self, provider: str, model: str) -> bool:
        return len(self._exhausted_resets(provider, model)) > 0

    def available_at(self, provider: str, model: str) -> float | None:
        resets = self._exhausted_resets(provider, model)
        return min(resets) if resets else None
```

Also change the existing `update` method's inline write to call `self._persist()` (replace the `self._path.parent.mkdir(...)` + `self._path.write_text(...)` lines with `self._persist()`), to keep persistence DRY.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_rate_limits.py -v`
Expected: PASS (existing + 5 new).

- [ ] **Step 5: Commit**

```bash
git add flexrouter/rate_limits.py tests/test_rate_limits.py
git commit -m "feat: RateLimitStore live headroom (remaining/reset, is_exhausted)"
```

---

## Task 4: client.py header parsing + duration helper

**Files:**
- Modify: `flexrouter/client.py`
- Test: `tests/test_client.py` (add cases)

**Interfaces:**
- Produces: `_parse_duration_ms(s: str) -> int | None` (handles `"45s"`, `"1m30s"`, `"12ms"`, plain seconds int/float, garbage→`None`); after a successful response, parse remaining/reset headers and call `store.update_headroom(...)` with `reset_*_at = now + ms/1000`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_client.py
import pytest
from flexrouter.client import _parse_duration_ms

@pytest.mark.parametrize("s,expected", [
    ("45s", 45000),
    ("1m30s", 90000),
    ("12ms", 12),
    ("2", 2000),       # bare number = seconds
    ("", None),
    ("garbage", None),
])
def test_parse_duration_ms(s, expected):
    assert _parse_duration_ms(s) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_client.py -k parse_duration -v`
Expected: FAIL — `cannot import name '_parse_duration_ms'`.

- [ ] **Step 3: Implement the helper + capture in `client.py`**

Add near `_parse_int_header`:

```python
import re as _re

def _parse_duration_ms(s):
    if s is None:
        return None
    text = str(s).strip()
    if not text:
        return None
    try:
        return int(float(text) * 1000)  # bare number = seconds
    except ValueError:
        pass
    m = _re.fullmatch(r"(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?(?:(\d+)ms)?", text)
    if not m or not any(m.groups()):
        return None
    ms = 0.0
    if m.group(1): ms += int(m.group(1)) * 60_000
    if m.group(2): ms += float(m.group(2)) * 1000
    if m.group(3): ms += int(m.group(3))
    return int(ms) if ms else None
```

In `chat`, extend the existing rate-limit capture block (after the `self._rate_limit_store.update(...)` call):

```python
        if self._rate_limit_store is not None:
            import time as _time
            rem_r = _parse_int_header(resp.headers, "x-ratelimit-remaining-requests")
            rem_t = _parse_int_header(resp.headers, "x-ratelimit-remaining-tokens")
            reset_r = _parse_duration_ms(resp.headers.get("x-ratelimit-reset-requests"))
            reset_t = _parse_duration_ms(resp.headers.get("x-ratelimit-reset-tokens"))
            now = _time.time()
            self._rate_limit_store.update_headroom(
                route.provider, route.model,
                remaining_requests=rem_r, remaining_tokens=rem_t,
                reset_requests_at=(now + reset_r / 1000) if reset_r is not None else None,
                reset_tokens_at=(now + reset_t / 1000) if reset_t is not None else None,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_client.py -v`
Expected: PASS (existing + parametrized duration cases).

- [ ] **Step 5: Commit**

```bash
git add flexrouter/client.py tests/test_client.py
git commit -m "feat: parse rate-limit remaining/reset headers into headroom store"
```

---

## Task 5: Engine skips exhausted models

**Files:**
- Modify: `flexrouter/engine.py` (`_score_candidates` ~line 138, `_model_available` ~line 197, `seconds_until_available` ~line 95)
- Test: `tests/test_engine.py` (add cases)

**Interfaces:**
- Consumes: `RateLimitStore.is_exhausted` / `available_at` (Task 3).
- Produces: a candidate is skipped when `rate_limit_store.is_exhausted(provider, model)`; `seconds_until_available` accounts for `available_at`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_engine.py
def test_exhausted_model_skipped(tmp_path):
    import time
    from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
    from flexrouter.engine import RoutingEngine
    from flexrouter.rate_limits import RateLimitStore
    store = RateLimitStore(str(tmp_path))
    store.update_headroom("groq", "llama", remaining_requests=0, reset_requests_at=time.time() + 60)
    cfg = FlexConfig(
        tiers={"default": [ModelConfig("groq", "llama", 50, 30, 6000)]},
        providers={"groq": ProviderConfig(base_url="http://x", api_keys=["k"])},
    )
    eng = RoutingEngine(cfg, rate_limit_store=store)
    assert eng.select("default", 10, False) is None  # only model is exhausted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine.py -k exhausted -v`
Expected: FAIL — model is still selected (returns a RouteResult).

- [ ] **Step 3: Add the gate in `engine.py`**

In `_score_candidates`, after the penalty check (`if self._penalties.is_penalized(...): continue`), add:

```python
            if self._rate_limit_store is not None and self._rate_limit_store.is_exhausted(m.provider, m.model):
                continue
```

In `_model_available`, after its penalty check (`if self._penalties.is_penalized(...): return False`), add:

```python
        if self._rate_limit_store is not None and self._rate_limit_store.is_exhausted(m.provider, m.model):
            return False
```

In `seconds_until_available`, within the loop, factor in exhaustion. Replace the `else:` window branch with:

```python
            elif self._rate_limit_store is not None and self._rate_limit_store.is_exhausted(m.provider, m.model):
                avail = self._rate_limit_store.available_at(m.provider, m.model)
                if avail is not None:
                    min_wait = min(min_wait, avail - time.time())
            else:
                w = self._windows.get(f"{m.provider}/{m.model}")
                if w:
                    secs = w.seconds_until_available(self._model_rpm(m), self._model_tpm(m))
                    min_wait = min(min_wait, secs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_engine.py -v`
Expected: PASS (existing + exhausted test).

- [ ] **Step 5: Commit**

```bash
git add flexrouter/engine.py tests/test_engine.py
git commit -m "feat: engine skips provider-reported exhausted models"
```

---

## Task 6: refresh_config

**Files:**
- Create: `flexrouter/refresh.py`
- Test: `tests/test_refresh.py`

**Interfaces:**
- Consumes: `onboard.PROVIDERS`, `discover_models`, `score_with_aa`, `build_yaml`; `config.is_probably_chat_model`; `RateLimitStore` (for real ceilings).
- Produces:
  ```python
  @dataclass
  class RefreshResult:
      timestamp: str
      added: list[str]
      removed: list[str]
      changed: list[dict]          # {"model","field","old","new"}
      backup_path: str
      provider_errors: list[dict]  # {"provider","error"}
  def refresh_config(config_path: str, state_dir: str, aa_key: str | None = None) -> RefreshResult
  ```

**Behavior:** load current yaml → for each PROVIDERS entry that has a key in the current config, discover models (collecting failures into `provider_errors`), inject `_provider`, drop non-chat via `is_probably_chat_model`, inject real `rpm`/`tpm` from `RateLimitStore` when known → score via `score_with_aa` → split free/paid by `pdef.free` → backup current file → diff old vs new → `build_yaml` → write → write `last_refresh.json`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_refresh.py
import json
from pathlib import Path
import yaml
import flexrouter.refresh as refresh
from flexrouter.refresh import refresh_config

OLD = """
providers:
  groq:
    base_url: https://api.groq.com/openai/v1
    api_keys: [test-key]
tiers:
  default:
    - {provider: groq, model: old-model, score: 50, rpm: 30, tpm: 6000, context_window: 131072}
settings:
  state_dir: STATE
"""

def _setup(tmp_path, monkeypatch, discovered):
    state = tmp_path / "st"
    cfg = tmp_path / "flexrouter.yaml"
    cfg.write_text(OLD.replace("STATE", str(state).replace("\\\\", "/")))
    async def fake_discover(provider, api_key):
        return discovered.get(provider.name, [])
    async def fake_score(models, aa_key):
        return [{**m, "score": 50} for m in models]
    monkeypatch.setattr(refresh, "discover_models", fake_discover)
    monkeypatch.setattr(refresh, "score_with_aa", fake_score)
    return str(cfg), str(state)

def test_diff_added_and_removed(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [{"id": "new-model", "context_length": 8192}]})
    result = refresh_config(cfg, state)
    assert "groq/new-model" in result.added
    assert "groq/old-model" in result.removed

def test_modality_filter_drops_junk(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [
        {"id": "good-chat"}, {"id": "whisper-large-v3"}]})
    result = refresh_config(cfg, state)
    written = yaml.safe_load(Path(cfg).read_text())
    models = [m["model"] for m in written["tiers"]["default"]]
    assert "good-chat" in models
    assert "whisper-large-v3" not in models

def test_keys_preserved(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [{"id": "x"}]})
    refresh_config(cfg, state)
    written = yaml.safe_load(Path(cfg).read_text())
    assert written["providers"]["groq"]["api_keys"] == [{"key": "test-key"}]

def test_backup_created_and_last_refresh_written(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [{"id": "x"}]})
    result = refresh_config(cfg, state)
    assert Path(result.backup_path).exists()
    assert (Path(state) / "last_refresh.json").exists()
    saved = json.loads((Path(state) / "last_refresh.json").read_text())
    assert saved["timestamp"] == result.timestamp

def test_discovery_failure_collected(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {})
    async def boom(provider, api_key):
        raise RuntimeError("network down")
    monkeypatch.setattr(refresh, "discover_models", boom)
    result = refresh_config(cfg, state)
    assert any(e["provider"] == "groq" for e in result.provider_errors)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_refresh.py -v`
Expected: FAIL — `No module named 'flexrouter.refresh'`.

- [ ] **Step 3: Implement `refresh.py`**

```python
# flexrouter/refresh.py
from __future__ import annotations
import asyncio
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from flexrouter.config import is_probably_chat_model
from flexrouter.onboard import PROVIDERS, discover_models, score_with_aa, build_yaml, _context_window
from flexrouter.rate_limits import RateLimitStore


@dataclass
class RefreshResult:
    timestamp: str
    added: list
    removed: list
    changed: list
    backup_path: str
    provider_errors: list


def _existing(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    return yaml.safe_load(config_path.read_text()) or {}


def _provider_keys(raw: dict) -> dict:
    out = {}
    for name, pcfg in (raw.get("providers") or {}).items():
        keys = (pcfg or {}).get("api_keys", [])
        for k in keys:
            val = k.get("key") if isinstance(k, dict) else k
            if val:
                out[name] = val
                break
    return out


def _old_models(raw: dict) -> dict:
    out = {}
    for models in (raw.get("tiers") or {}).values():
        for m in models or []:
            out[f"{m['provider']}/{m['model']}"] = {
                "rpm": m.get("rpm"), "tpm": m.get("tpm"), "context_window": m.get("context_window"),
            }
    return out


async def _discover_all(provider_keys: dict, store: RateLimitStore):
    pmap = {p.name: p for p in PROVIDERS}
    free, paid, errors = [], [], []
    for name, key in provider_keys.items():
        pdef = pmap.get(name)
        if pdef is None:
            continue
        try:
            models = await discover_models(pdef, key)
        except Exception as exc:  # collect, never raise
            errors.append({"provider": name, "error": str(exc)})
            continue
        for m in models:
            mid = m.get("id", "")
            if not mid or not is_probably_chat_model(mid):
                continue
            m = {**m, "_provider": name}
            known_rpm = store.get_rpm(name, mid, 0)
            known_tpm = store.get_tpm(name, mid, 0)
            if known_rpm > 0:
                m["rpm"] = known_rpm
            if known_tpm > 0:
                m["tpm"] = known_tpm
            (free if pdef.free else paid).append(m)
    return free, paid, errors


def refresh_config(config_path: str, state_dir: str, aa_key: str | None = None) -> RefreshResult:
    cfg_path = Path(config_path)
    raw = _existing(cfg_path)
    provider_keys = _provider_keys(raw)
    old = _old_models(raw)
    store = RateLimitStore(state_dir)

    free, paid, errors = asyncio.run(_discover_all(provider_keys, store))
    free = asyncio.run(score_with_aa(free, aa_key))
    paid = asyncio.run(score_with_aa(paid, aa_key))

    new = {}
    for m in free + paid:
        new[f"{m['_provider']}/{m['id']}"] = {
            "rpm": m.get("rpm"), "tpm": m.get("tpm"), "context_window": _context_window(m),
        }

    added = sorted(k for k in new if k not in old)
    removed = sorted(k for k in old if k not in new)
    changed = []
    for k in sorted(set(old) & set(new)):
        for field_name in ("rpm", "tpm", "context_window"):
            ov, nv = old[k].get(field_name), new[k].get(field_name)
            if ov is not None and nv is not None and ov != nv:
                changed.append({"model": k, "field": field_name, "old": ov, "new": nv})

    # Backup BEFORE writing.
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    backups = Path(state_dir) / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup_path = backups / f"flexrouter-{ts.replace(':', '').replace('-', '')}.yaml"
    if cfg_path.exists():
        backup_path.write_text(cfg_path.read_text())

    new_yaml = build_yaml(provider_keys, free, paid, PROVIDERS)
    cfg_path.write_text(new_yaml)

    result = RefreshResult(
        timestamp=ts, added=added, removed=removed, changed=changed,
        backup_path=str(backup_path), provider_errors=errors,
    )
    tmp = Path(state_dir) / "last_refresh.json.tmp"
    tmp.write_text(json.dumps(asdict(result), indent=2))
    os.replace(tmp, Path(state_dir) / "last_refresh.json")
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_refresh.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add flexrouter/refresh.py tests/test_refresh.py
git commit -m "feat: refresh_config rewrites config from discovery with backup + diff"
```

---

## Task 7: CLI refresh command

**Files:**
- Modify: `flexrouter/cli.py`
- Test: `tests/test_cli_refresh.py`

**Interfaces:**
- Consumes: `refresh_config`, `discover_config`, `load_config`.
- Produces: `flexrouter refresh` — runs `refresh_config`, prints `+N / -M / K changed`, the backup path, and any provider errors. Exits non-zero only if `refresh_config` raised.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_refresh.py
from click.testing import CliRunner
import flexrouter.cli as cli
from flexrouter.refresh import RefreshResult

def test_refresh_prints_summary(monkeypatch, tmp_path):
    cfg = tmp_path / "flexrouter.yaml"
    cfg.write_text("providers: {}\ntiers: {}\nsettings:\n  state_dir: .flexrouter\n")
    monkeypatch.setattr(cli, "discover_config", lambda: cfg)
    fake = RefreshResult(timestamp="2026-06-19T12:00:00+00:00", added=["groq/a"], removed=[],
                         changed=[], backup_path="/b.yaml", provider_errors=[{"provider": "x", "error": "boom"}])
    monkeypatch.setattr(cli, "refresh_config", lambda *a, **k: fake, raising=False)
    result = CliRunner().invoke(cli.cli, ["refresh"])
    assert result.exit_code == 0
    assert "+1" in result.output
    assert "boom" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_refresh.py -v`
Expected: FAIL — no `refresh` command / `refresh_config` not importable from cli.

- [ ] **Step 3: Add the command to `cli.py`**

Add the import near the top:

```python
from flexrouter.refresh import refresh_config
```

Add the command:

```python
@cli.command()
def refresh():
    """Re-discover models + rate limits and rewrite flexrouter.yaml (with backup)."""
    path = discover_config()
    if not path:
        click.echo("No flexrouter.yaml found.", err=True)
        raise SystemExit(1)
    cfg = load_config(path)
    aa_key = os.environ.get("AA_API_KEY")
    result = refresh_config(str(path), cfg.state_dir, aa_key=aa_key)
    click.echo(f"Refreshed: +{len(result.added)} added, "
               f"-{len(result.removed)} removed, {len(result.changed)} changed")
    click.echo(f"Backup: {result.backup_path}")
    for err in result.provider_errors:
        click.echo(f"  ! {err['provider']}: {err['error']}", err=True)
```

Add `import os` at the top of `cli.py` if not present.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli_refresh.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add flexrouter/cli.py tests/test_cli_refresh.py
git commit -m "feat: flexrouter refresh CLI command"
```

---

## Task 8: Dashboard refresh endpoints

**Files:**
- Modify: `flexrouter/dashboard/api.py`
- Modify: `flexrouter/dashboard/server.py`
- Test: `tests/test_dashboard_refresh.py`

**Interfaces:**
- Produces: `api.get_last_refresh(state_dir) -> dict` (contents of `last_refresh.json` or `{"timestamp": None}`); `api.run_refresh(state_dir) -> dict` (calls `refresh_config(discover_config(), state_dir)`, returns the result dict). Routes: `GET /api/refresh` → `get_last_refresh`; `POST /api/refresh` → `run_refresh`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_refresh.py
import json
from flexrouter.dashboard import api

def test_get_last_refresh_default(tmp_path):
    assert api.get_last_refresh(str(tmp_path)) == {"timestamp": None}

def test_get_last_refresh_reads_file(tmp_path):
    (tmp_path / "last_refresh.json").write_text(json.dumps({"timestamp": "t", "added": ["a"]}))
    out = api.get_last_refresh(str(tmp_path))
    assert out["added"] == ["a"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dashboard_refresh.py -v`
Expected: FAIL — `module 'flexrouter.dashboard.api' has no attribute 'get_last_refresh'`.

- [ ] **Step 3: Add functions to `api.py`**

```python
def get_last_refresh(state_dir: str) -> dict:
    path = Path(state_dir) / "last_refresh.json"
    if not path.exists():
        return {"timestamp": None}
    return json.loads(path.read_text())


def run_refresh(state_dir: str) -> dict:
    from dataclasses import asdict
    from flexrouter.refresh import refresh_config
    path = discover_config() or Path("flexrouter.yaml")
    return asdict(refresh_config(str(path), state_dir))
```

- [ ] **Step 4: Add routes in `server.py`**

In `_handle_api_get`, add:

```python
            elif self.path == "/api/refresh":
                self._send_json(get_last_refresh(state))
```

In `do_POST`, add a branch alongside `/api/config` (compute `state` via `_state_dir()`):

```python
            elif self.path == "/api/refresh":
                self._send_json(run_refresh(_state_dir()))
```

Add `get_last_refresh, run_refresh` to the `from flexrouter.dashboard.api import (...)` line.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_dashboard_refresh.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (all green).

- [ ] **Step 7: Commit**

```bash
git add flexrouter/dashboard/api.py flexrouter/dashboard/server.py tests/test_dashboard_refresh.py
git commit -m "feat: GET/POST /api/refresh endpoints"
```

---

## Self-Review Notes

- **Spec coverage:** shared modality helper (Task 1); `build_yaml` real-limit overrides (Task 2); headroom store with `is_exhausted`/`available_at` (Task 3); header + duration parsing (Task 4); engine exhaustion gate (Task 5); `refresh_config` with full-regenerate, modality filter, key preservation, backup, diff, `last_refresh.json` (Task 6); CLI `refresh` (Task 7); `GET`/`POST /api/refresh` (Task 8). Frontend refresh button/diff panel correctly deferred (spec scope).
- **Type consistency:** `RefreshResult` fields are identical across `refresh.py`, the CLI, and the dashboard (`asdict`). `update_headroom` / `is_exhausted` / `available_at` signatures match between `RateLimitStore` (Task 3), `client.py` (Task 4), and `engine.py` (Task 5). `is_probably_chat_model` is the single modality gate used by both `validate_config` and `refresh._discover_all`.
- **Stale-guidance resolution:** the spec's branch-dependency risk is closed — base on `master`; Task 1 promotes the already-merged `_NON_CHAT_PATTERNS` to the public shared helper.
- **Discovery realism:** `discover_models` returns `[]` on HTTP error (swallows), so `_discover_all`'s try/except mainly catches monkeypatched/unexpected failures; real per-provider HTTP failures yield empty lists (models become `removed`), which the diff surfaces honestly.
```
