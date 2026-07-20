# Phase 3 Side 1: flexrouter Feature Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port 3 features from OpenVL's `router/` package into flexrouter — pluggable per-provider header parsing, persistent daily/hourly (`rpd`/`rph`) quota tracking, and structured `reasoning`/`tool_call_delta`/`usage` stream events — so flexrouter becomes a strict superset of OpenVL's router, all additive and backward-compatible.

**Architecture:** Each feature is additive to the existing `flexrouter/` package (no breaking changes to `FlexRouter.generate`/`agenerate`/`agenerate_stream` signatures). Header parsing moves from two hardcoded blocks in `client.py` into a pluggable `flexrouter/headers.py` registry, selected via a new `header_parser` field on `ProviderConfig` (default `"openai_compatible"`, so existing configs are unaffected). Quota tracking is a new `flexrouter/quota.py` module using the same tmp-file+`os.replace` atomic-write pattern as `flexrouter/recovery.py`, wired into `RoutingEngine` the same way `RateLimitStore` already is. Streaming events extend the existing `StreamEvent` union in `flexrouter/_router.py` with two new dataclasses; `client.py`'s `stream_chat` starts yielding a small `StreamChunk` dataclass instead of a bare `str` so reasoning/tool-call/usage data can flow through.

**Tech Stack:** Python 3.11+, `httpx` (async), `pytest` + `pytest-asyncio`-style `async def test_...` (project convention, see `tests/conftest.py`), `PyYAML`.

## Global Constraints

- No breaking changes to any public `FlexRouter` method signature — `stash` (a real consumer) must keep working unmodified against `agenerate`/`generate`.
- `agenerate_stream`'s existing `AttemptEvent`/`AttemptFailedEvent`/`DeltaEvent`/`DoneEvent` semantics (in particular the "no retry after first delta" commit-point invariant documented at `flexrouter/_router.py:226-230`) must not change.
- All new persisted state lives under `state_dir` (default `.flexrouter/`), one file per concern, using the atomic tmp-file + `os.replace` write pattern already established in `flexrouter/recovery.py`'s `PenaltyBox._save()` — not the non-atomic `Path.write_text` pattern in `rate_limits.py`.
- `header_parser` defaults to `"openai_compatible"` for any provider that doesn't set it, so every existing `flexrouter.yaml` in the wild keeps working unchanged.
- Follow existing test conventions: reuse `tests/conftest.py`'s `MINIMAL_CONFIG` / `config_file` fixture pattern; async tests are plain `async def test_...` functions (no explicit `@pytest.mark.asyncio` marker used elsewhere in this repo — check `tests/test_agenerate_stream.py` for the exact pattern before adding new async tests, and follow it).

---

### Task 1: Pluggable per-provider header parsing

**Files:**
- Create: `flexrouter/headers.py`
- Create: `tests/test_headers.py`
- Modify: `flexrouter/config.py:25-28` (`ProviderConfig`), `flexrouter/config.py:91` (`load_config` provider parsing)
- Modify: `flexrouter/client.py` (replace the two duplicated hardcoded header-parsing blocks at lines 84-100 and 144-160)
- Modify: `tests/test_client.py` (existing header tests must keep passing; add one parser-selection test)

**Interfaces:**
- Produces: `flexrouter.headers.ParsedHeaders` (dataclass: `limit_requests: int | None`, `limit_tokens: int | None`, `remaining_requests: int | None`, `remaining_tokens: int | None`, `reset_requests_at: float | None` epoch seconds, `reset_tokens_at: float | None` epoch seconds), `flexrouter.headers.parse_headers(parser_name: str, headers: httpx.Headers | dict) -> ParsedHeaders`, `flexrouter.headers.PARSERS: dict[str, Callable]`.
- Consumes (Task 2/3 don't depend on this, independent): nothing new from other tasks.

- [ ] **Step 1: Write the failing tests for `flexrouter/headers.py`**

Create `tests/test_headers.py`:

```python
from flexrouter.headers import parse_headers, ParsedHeaders, PARSERS


def test_openai_compatible_parses_all_fields():
    headers = {
        "x-ratelimit-limit-requests": "30",
        "x-ratelimit-limit-tokens": "6000",
        "x-ratelimit-remaining-requests": "29",
        "x-ratelimit-remaining-tokens": "5900",
        "x-ratelimit-reset-requests": "1m30s",
        "x-ratelimit-reset-tokens": "500ms",
    }
    result = parse_headers("openai_compatible", headers)
    assert result.limit_requests == 30
    assert result.limit_tokens == 6000
    assert result.remaining_requests == 29
    assert result.remaining_tokens == 5900
    assert result.reset_requests_at is not None
    assert result.reset_tokens_at is not None


def test_openai_compatible_missing_headers_returns_none_fields():
    result = parse_headers("openai_compatible", {})
    assert result == ParsedHeaders()


def test_unknown_parser_name_falls_back_to_openai_compatible():
    headers = {"x-ratelimit-remaining-requests": "5"}
    result = parse_headers("does-not-exist", headers)
    assert result.remaining_requests == 5


def test_cerebras_and_openrouter_are_registered_aliases():
    assert "cerebras" in PARSERS
    assert "openrouter" in PARSERS
    headers = {"x-ratelimit-remaining-requests": "7"}
    assert parse_headers("cerebras", headers).remaining_requests == 7
    assert parse_headers("openrouter", headers).remaining_requests == 7


def test_google_falls_back_to_goog_prefixed_headers():
    headers = {"x-goog-ratelimit-remaining-requests": "3"}
    result = parse_headers("google", headers)
    assert result.remaining_requests == 3


def test_duration_parser_handles_hours_minutes_seconds_ms_and_bare_seconds():
    from flexrouter.headers import _parse_duration_ms
    assert _parse_duration_ms("2h") == 2 * 3_600_000
    assert _parse_duration_ms("1m30s") == 90_000
    assert _parse_duration_ms("500ms") == 500
    assert _parse_duration_ms("14") == 14_000
    assert _parse_duration_ms(None) is None
    assert _parse_duration_ms("garbage") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd C:\projects\router-v3.1-ai-tier-overhaul && python -m pytest tests/test_headers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flexrouter.headers'`

- [ ] **Step 3: Implement `flexrouter/headers.py`**

```python
from __future__ import annotations
import re
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class ParsedHeaders:
    limit_requests: int | None = None
    limit_tokens: int | None = None
    remaining_requests: int | None = None
    remaining_tokens: int | None = None
    reset_requests_at: float | None = None  # epoch seconds
    reset_tokens_at: float | None = None  # epoch seconds


def _parse_duration_ms(s) -> int | None:
    if s is None:
        return None
    text = str(s).strip()
    if not text:
        return None
    try:
        return int(float(text) * 1000)  # bare number = seconds
    except ValueError:
        pass
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?(?:(\d+)ms)?", text)
    if not m or not any(m.groups()):
        return None
    ms = 0.0
    if m.group(1):
        ms += int(m.group(1)) * 3_600_000
    if m.group(2):
        ms += int(m.group(2)) * 60_000
    if m.group(3):
        ms += float(m.group(3)) * 1000
    if m.group(4):
        ms += int(m.group(4))
    return int(ms) if ms else None


def _parse_int(headers, name: str) -> int | None:
    val = headers.get(name)
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _parse_reset_at(headers, name: str) -> float | None:
    ms = _parse_duration_ms(headers.get(name))
    if ms is None:
        return None
    return time.time() + ms / 1000


def _parse_openai_compatible(headers) -> ParsedHeaders:
    norm = {str(k).lower(): v for k, v in dict(headers).items()}
    return ParsedHeaders(
        limit_requests=_parse_int(norm, "x-ratelimit-limit-requests"),
        limit_tokens=_parse_int(norm, "x-ratelimit-limit-tokens"),
        remaining_requests=_parse_int(norm, "x-ratelimit-remaining-requests"),
        remaining_tokens=_parse_int(norm, "x-ratelimit-remaining-tokens"),
        reset_requests_at=_parse_reset_at(norm, "x-ratelimit-reset-requests"),
        reset_tokens_at=_parse_reset_at(norm, "x-ratelimit-reset-tokens"),
    )


def _parse_google(headers) -> ParsedHeaders:
    norm = {str(k).lower(): v for k, v in dict(headers).items()}
    for candidate in ("x-goog-ratelimit-remaining-requests", "x-goog-ratelimit-request-remaining"):
        if candidate in norm and "x-ratelimit-remaining-requests" not in norm:
            norm["x-ratelimit-remaining-requests"] = norm[candidate]
            break
    return _parse_openai_compatible(norm)


PARSERS: dict[str, Callable[[dict], ParsedHeaders]] = {
    "openai_compatible": _parse_openai_compatible,
    "cerebras": _parse_openai_compatible,
    "openrouter": _parse_openai_compatible,
    "siliconflow": _parse_openai_compatible,
    "google": _parse_google,
}


def parse_headers(parser_name: str, headers) -> ParsedHeaders:
    parser = PARSERS.get(parser_name, _parse_openai_compatible)
    return parser(headers)
```

Note: `cerebras`/`openrouter`/`siliconflow` are registered as explicit aliases (not silent fallback) so `PARSERS` stays the single source of truth for "which parser names are known" — confirmed against OpenVL's `router/headers.py` that these providers use identical header conventions to `openai_compatible` today; if a provider is later found to diverge, only its entry in `PARSERS` needs to change.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_headers.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Add `header_parser` to `ProviderConfig` and thread it through `load_config`**

Modify `flexrouter/config.py`. Change the `ProviderConfig` dataclass (currently lines 25-28):

```python
@dataclass
class ProviderConfig:
    base_url: str
    api_keys: list[str]  # resolved values (not env var names)
    header_parser: str = "openai_compatible"
```

Change the provider-building loop inside `load_config` (currently line 91):

```python
        providers[name] = ProviderConfig(
            base_url=praw["base_url"],
            api_keys=resolved,
            header_parser=praw.get("header_parser", "openai_compatible"),
        )
```

- [ ] **Step 6: Write the failing test for config parsing `header_parser`**

Add to `tests/test_config.py`:

```python
def test_provider_header_parser_defaults_to_openai_compatible(tmp_path, monkeypatch):
    import yaml
    from flexrouter.config import load_config
    monkeypatch.setenv("GROQ_API_KEY", "k")
    cfg_dict = {
        "tiers": {"low": [{"provider": "groq", "model": "m", "score": 50, "rpm": 1, "tpm": 1}]},
        "providers": {"groq": {"base_url": "https://x", "api_keys": [{"env": "GROQ_API_KEY"}]}},
    }
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg_dict))
    cfg = load_config(p)
    assert cfg.providers["groq"].header_parser == "openai_compatible"


def test_provider_header_parser_explicit_value_is_read(tmp_path, monkeypatch):
    import yaml
    from flexrouter.config import load_config
    monkeypatch.setenv("CEREBRAS_KEY", "k")
    cfg_dict = {
        "tiers": {"low": [{"provider": "cb", "model": "m", "score": 50, "rpm": 1, "tpm": 1}]},
        "providers": {"cb": {
            "base_url": "https://x", "api_keys": [{"env": "CEREBRAS_KEY"}],
            "header_parser": "cerebras",
        }},
    }
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg_dict))
    cfg = load_config(p)
    assert cfg.providers["cb"].header_parser == "cerebras"
```

- [ ] **Step 7: Run to verify these two tests fail, then pass after Step 5's implementation**

Run: `python -m pytest tests/test_config.py -k header_parser -v`
Expected before Step 5 code exists: FAIL (`AttributeError`/`TypeError`). It already passes now since Step 5 was applied above — run it to confirm: expected PASS (2 tests).

- [ ] **Step 8: Replace the duplicated hardcoded header-parsing blocks in `client.py`**

In `flexrouter/client.py`, delete `_parse_duration_ms` and `_parse_int_header` (lines 17-47 — now superseded by `flexrouter/headers.py`) and add the import:

```python
from flexrouter.headers import parse_headers
```

Replace the block in `chat()` (currently lines 84-100):

```python
        if self._rate_limit_store is not None:
            parsed = parse_headers(route.header_parser, resp.headers)
            if parsed.limit_requests is not None or parsed.limit_tokens is not None:
                self._rate_limit_store.update(route.provider, route.model, parsed.limit_requests, parsed.limit_tokens)
            self._rate_limit_store.update_headroom(
                route.provider, route.model,
                remaining_requests=parsed.remaining_requests, remaining_tokens=parsed.remaining_tokens,
                reset_requests_at=parsed.reset_requests_at, reset_tokens_at=parsed.reset_tokens_at,
            )
```

Replace the identical block in `stream_chat()` (currently lines 144-160) with the same code.

This introduces `route.header_parser` — `RouteResult` (in `flexrouter/engine.py`) does not currently carry it, so it must be added there too (Step 9).

- [ ] **Step 9: Add `header_parser` to `RouteResult` and populate it in `_make_result`**

Modify `flexrouter/engine.py`. `RouteResult` dataclass (currently lines 15-21):

```python
@dataclass
class RouteResult:
    provider: str
    model: str
    api_key: str
    base_url: str
    tier: str
    header_parser: str
```

`RoutingEngine._make_result` (currently lines 238-252), add the field to the constructed `RouteResult`:

```python
        return RouteResult(
            provider=m.provider,
            model=m.model,
            api_key=api_key,
            base_url=provider_cfg.base_url,
            tier=tier,
            header_parser=provider_cfg.header_parser,
        )
```

- [ ] **Step 10: Run the full client + engine + config test suites**

Run: `python -m pytest tests/test_client.py tests/test_engine.py tests/test_config.py tests/test_headers.py -v`
Expected: PASS, all tests (existing `test_client.py` tests like `test_rate_limit_headers_stored_on_success` must still pass unmodified — they exercise the same header names through the new code path).

- [ ] **Step 11: Add one parser-selection integration test to `tests/test_client.py`**

Add a test asserting a provider configured with `header_parser="google"` actually uses the Google fallback headers end-to-end through `AsyncClient.chat()`. Follow the existing `httpx` mock-transport pattern already used in that file (read the top of `tests/test_client.py` for the exact `httpx.MockTransport`/monkeypatch fixture in use, and mirror it — do not invent a different mocking approach).

- [ ] **Step 12: Run full suite and commit**

Run: `python -m pytest -v`
Expected: PASS, no regressions.

```bash
git add flexrouter/headers.py flexrouter/config.py flexrouter/client.py flexrouter/engine.py tests/test_headers.py tests/test_config.py tests/test_client.py
git commit -m "feat: pluggable per-provider header parsing (header_parser field)"
```

---

### Task 2: Persistent daily/hourly (`rpd`/`rph`) quota tracking

**Files:**
- Create: `flexrouter/quota.py`
- Create: `tests/test_quota.py`
- Modify: `flexrouter/config.py:16-23` (`ModelConfig`, add `quotas` field), `flexrouter/config.py:96-107` (`load_config` model parsing)
- Modify: `flexrouter/engine.py` (`RoutingEngine.__init__`, `_score_candidates`, `_model_available`, `seconds_until_available`)
- Modify: `flexrouter/_router.py` (`FlexRouter.__init__`, `agenerate`, `agenerate_stream`, `reload`)
- Test: `tests/test_engine.py` (add quota-exhaustion case)

**Interfaces:**
- Produces: `flexrouter.quota.QuotaTracker` with `__init__(self, state_dir: str) -> None`, `record(self, provider: str, model: str) -> None`, `is_available(self, provider: str, model: str, quotas: dict[str, int]) -> bool`, `seconds_until_available(self, provider: str, model: str, quotas: dict[str, int]) -> float`.
- Consumes: `ModelConfig.quotas: dict[str, int]` (from Task's config change below; independent of Task 1).

- [ ] **Step 1: Write the failing tests for `flexrouter/quota.py`**

Create `tests/test_quota.py`:

```python
import json
import time
from pathlib import Path

from flexrouter.quota import QuotaTracker


def test_no_quotas_configured_means_always_available(tmp_path):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    assert tracker.is_available("groq", "m1", {}) is True


def test_records_persist_to_disk_across_instances(tmp_path):
    state_dir = str(tmp_path / ".flexrouter")
    tracker1 = QuotaTracker(state_dir)
    tracker1.record("groq", "m1")
    tracker1.record("groq", "m1")

    tracker2 = QuotaTracker(state_dir)
    assert tracker2.is_available("groq", "m1", {"rpd": 2}) is False
    assert tracker2.is_available("groq", "m1", {"rpd": 3}) is True


def test_persist_uses_atomic_write(tmp_path):
    state_dir = tmp_path / ".flexrouter"
    tracker = QuotaTracker(str(state_dir))
    tracker.record("groq", "m1")
    assert (state_dir / "quotas.json").exists()
    assert not (state_dir / "quotas.tmp").exists()
    data = json.loads((state_dir / "quotas.json").read_text())
    assert "groq/m1" in data


def test_rph_and_rpd_are_independent_windows(tmp_path):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    for _ in range(5):
        tracker.record("groq", "m1")
    # rpd limit of 5 is exhausted, but rph limit of 100 is not — must check ALL configured windows
    assert tracker.is_available("groq", "m1", {"rpd": 5, "rph": 100}) is False
    assert tracker.is_available("groq", "m1", {"rph": 100}) is True


def test_unknown_quota_key_is_ignored(tmp_path):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    tracker.record("groq", "m1")
    assert tracker.is_available("groq", "m1", {"rpweek": 0}) is True


def test_seconds_until_available_when_exhausted(tmp_path, monkeypatch):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    fake_now = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])
    tracker.record("groq", "m1")
    wait = tracker.seconds_until_available("groq", "m1", {"rph": 1})
    assert 3599 <= wait <= 3600

    fake_now[0] += 3600.1
    assert tracker.is_available("groq", "m1", {"rph": 1}) is True


def test_different_provider_model_keys_are_independent(tmp_path):
    tracker = QuotaTracker(str(tmp_path / ".flexrouter"))
    tracker.record("groq", "m1")
    assert tracker.is_available("groq", "m2", {"rpd": 1}) is True
    assert tracker.is_available("other", "m1", {"rpd": 1}) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_quota.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flexrouter.quota'`

- [ ] **Step 3: Implement `flexrouter/quota.py`**

```python
from __future__ import annotations
import json
import os
import time
from pathlib import Path

_WINDOWS_SECONDS: dict[str, int] = {"rph": 3600, "rpd": 86400}


class QuotaTracker:
    """Tracks per-provider/model request counts against configured rpd/rph
    limits, persisted to <state_dir>/quotas.json. Survives process restarts,
    unlike RoutingEngine's in-memory SlidingWindow (rpm/tpm)."""

    def __init__(self, state_dir: str) -> None:
        self._path = Path(state_dir) / "quotas.json"
        self._state: dict[str, list[float]] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                self._state = {k: list(v) for k, v in raw.items()}
            except (json.JSONDecodeError, OSError, ValueError):
                self._state = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state))
        os.replace(tmp, self._path)

    @staticmethod
    def _key(provider: str, model: str) -> str:
        return f"{provider}/{model}"

    def record(self, provider: str, model: str) -> None:
        key = self._key(provider, model)
        now = time.time()
        max_window = max(_WINDOWS_SECONDS.values())
        cutoff = now - max_window
        timestamps = [t for t in self._state.get(key, []) if t > cutoff]
        timestamps.append(now)
        self._state[key] = timestamps
        self._save()

    def is_available(self, provider: str, model: str, quotas: dict[str, int]) -> bool:
        if not quotas:
            return True
        key = self._key(provider, model)
        now = time.time()
        timestamps = self._state.get(key, [])
        for q_type, limit in quotas.items():
            window = _WINDOWS_SECONDS.get(q_type)
            if window is None:
                continue
            cutoff = now - window
            count = sum(1 for t in timestamps if t > cutoff)
            if count >= limit:
                return False
        return True

    def seconds_until_available(self, provider: str, model: str, quotas: dict[str, int]) -> float:
        if not quotas:
            return 0.0
        key = self._key(provider, model)
        now = time.time()
        timestamps = sorted(self._state.get(key, []))
        wait = 0.0
        for q_type, limit in quotas.items():
            window = _WINDOWS_SECONDS.get(q_type)
            if window is None:
                continue
            cutoff = now - window
            in_window = [t for t in timestamps if t > cutoff]
            if len(in_window) >= limit:
                oldest = in_window[0]
                wait = max(wait, (oldest + window) - now)
        return max(0.0, wait)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_quota.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Add `quotas` field to `ModelConfig` and thread it through `load_config`**

Modify `flexrouter/config.py`. `ModelConfig` dataclass (currently lines 16-23):

```python
@dataclass
class ModelConfig:
    provider: str
    model: str
    score: int
    rpm: int
    tpm: int
    context_window: int = 200000
    vision: bool = False
    quotas: dict[str, int] = field(default_factory=dict)
```

In `load_config`'s tier-parsing loop (currently lines 96-107):

```python
    for tier_name, models in (raw.get("tiers") or {}).items():
        tiers[tier_name] = [
            ModelConfig(
                provider=m["provider"],
                model=m["model"],
                score=m["score"],
                rpm=m["rpm"],
                tpm=m["tpm"],
                context_window=m.get("context_window", 200000),
                vision=m.get("vision", False),
                quotas=m.get("quotas", {}),
            )
            for m in models
        ]
```

- [ ] **Step 6: Write the failing config test for `quotas`**

Add to `tests/test_config.py`:

```python
def test_model_quotas_default_to_empty_dict(tmp_path, monkeypatch):
    import yaml
    from flexrouter.config import load_config
    monkeypatch.setenv("GROQ_API_KEY", "k")
    cfg_dict = {
        "tiers": {"low": [{"provider": "groq", "model": "m", "score": 50, "rpm": 1, "tpm": 1}]},
        "providers": {"groq": {"base_url": "https://x", "api_keys": [{"env": "GROQ_API_KEY"}]}},
    }
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg_dict))
    cfg = load_config(p)
    assert cfg.tiers["low"][0].quotas == {}


def test_model_quotas_explicit_rpd_rph_is_read(tmp_path, monkeypatch):
    import yaml
    from flexrouter.config import load_config
    monkeypatch.setenv("OR_KEY", "k")
    cfg_dict = {
        "tiers": {"low": [{
            "provider": "or", "model": "m", "score": 50, "rpm": 1, "tpm": 1,
            "quotas": {"rpd": 50, "rph": 10},
        }]},
        "providers": {"or": {"base_url": "https://x", "api_keys": [{"env": "OR_KEY"}]}},
    }
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg_dict))
    cfg = load_config(p)
    assert cfg.tiers["low"][0].quotas == {"rpd": 50, "rph": 10}
```

- [ ] **Step 7: Run to verify pass**

Run: `python -m pytest tests/test_config.py -k quota -v`
Expected: PASS (2 tests)

- [ ] **Step 8: Wire `QuotaTracker` into `RoutingEngine`**

Modify `flexrouter/engine.py`:

`__init__` signature (currently lines 25-41), add a `quota_tracker` parameter and store it:

```python
    def __init__(self, cfg: FlexConfig, rate_limit_store=None, penalties: Optional[PenaltyBox] = None,
                 quota_tracker=None) -> None:
        self._cfg = cfg
        self._rate_limit_store = rate_limit_store
        self._quota_tracker = quota_tracker
        self._penalties = penalties if penalties is not None else PenaltyBox(
            cfg.penalty_base_seconds, cfg.penalty_max_seconds)
        self._budget = DailyBudget(cfg.provider_budget)
        self._windows: dict[str, SlidingWindow] = {}
        self._key_counters: dict[str, int] = {}
        self._sessions: dict[str, tuple[str, str, float]] = {}
        self._session_ttl = cfg.session_ttl_minutes * 60

        for tier_models in cfg.tiers.values():
            for m in tier_models:
                k = f"{m.provider}/{m.model}"
                if k not in self._windows:
                    self._windows[k] = SlidingWindow(cfg.window_seconds)
```

Add a quota check to `_score_candidates` (currently lines 198-227), inserted alongside the existing `_rate_limit_store.is_exhausted` check:

```python
            if self._rate_limit_store is not None and self._rate_limit_store.is_exhausted(m.provider, m.model):
                continue
            if self._quota_tracker is not None and m.quotas and not self._quota_tracker.is_available(m.provider, m.model, m.quotas):
                continue
```

Add the same check to `_model_available` (currently lines 264-280):

```python
        if self._rate_limit_store is not None and self._rate_limit_store.is_exhausted(m.provider, m.model):
            return False
        if self._quota_tracker is not None and m.quotas and not self._quota_tracker.is_available(m.provider, m.model, m.quotas):
            return False
```

Add quota wait time to `seconds_until_available` (currently lines 96-113), inside the `else` branch after the rate-limit-store `elif`:

```python
            elif self._rate_limit_store is not None and self._rate_limit_store.is_exhausted(m.provider, m.model):
                avail = self._rate_limit_store.available_at(m.provider, m.model)
                if avail is not None:
                    min_wait = min(min_wait, avail - time.time())
            elif self._quota_tracker is not None and m.quotas and not self._quota_tracker.is_available(m.provider, m.model, m.quotas):
                min_wait = min(min_wait, self._quota_tracker.seconds_until_available(m.provider, m.model, m.quotas))
            else:
```

- [ ] **Step 9: Write the failing engine test for quota exhaustion**

Add to `tests/test_engine.py` (follow the existing `ModelConfig`/`FlexConfig` construction pattern already used in that file — read its top for the exact helper functions/fixtures in use before writing this):

```python
def test_score_candidates_skips_quota_exhausted_model():
    from flexrouter.config import ModelConfig
    from flexrouter.quota import QuotaTracker
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        tracker = QuotaTracker(d)
        tracker.record("groq", "m1")
        m1 = ModelConfig(provider="groq", model="m1", score=90, rpm=100, tpm=100000, quotas={"rpd": 1})
        m2 = ModelConfig(provider="groq", model="m2", score=50, rpm=100, tpm=100000)
        cfg = _make_config({"low": [m1, m2]})  # use this file's existing config-building helper
        engine = RoutingEngine(cfg, quota_tracker=tracker)
        scored = engine._score_candidates([m1, m2], estimated_tokens=0, vision=False)
        chosen_models = [m.model for _, m in scored]
        assert "m1" not in chosen_models
        assert "m2" in chosen_models
```

(If `tests/test_engine.py` has no `_make_config` helper, use whatever `FlexConfig(...)` construction pattern the existing tests in that file already use — do not invent a new one.)

- [ ] **Step 10: Run to verify it fails then passes**

Run: `python -m pytest tests/test_engine.py -k quota -v`
Expected: FAIL before Step 8 (AttributeError on `quotas`/`quota_tracker`), PASS after (Step 8 is already applied above, so run now and expect PASS).

- [ ] **Step 11: Wire `QuotaTracker` into `FlexRouter`**

Modify `flexrouter/_router.py`. Add the import:

```python
from flexrouter.quota import QuotaTracker
```

In `__init__` (currently lines 53-84), construct the tracker and pass it to `RoutingEngine`:

```python
        self._rate_limit_store = RateLimitStore(self._cfg.state_dir)
        self._quota_tracker = QuotaTracker(self._cfg.state_dir)
        self._events = EventLogger(self._cfg.state_dir)
        self._penalties = PenaltyBox(
            self._cfg.penalty_base_seconds, self._cfg.penalty_max_seconds,
            state_dir=self._cfg.state_dir, on_event=self._events.record,
        )
        self._engine = RoutingEngine(
            self._cfg, rate_limit_store=self._rate_limit_store, penalties=self._penalties,
            quota_tracker=self._quota_tracker)
```

In `agenerate` (currently line 174, right after `self._engine.record_request(...)`), record the quota hit:

```python
            self._engine.record_request(route.provider, route.model, total_tokens)
            self._quota_tracker.record(route.provider, route.model)
```

In `agenerate_stream` (currently line 302, same spot), same addition:

```python
            self._engine.record_request(route.provider, route.model, total_tokens)
            self._quota_tracker.record(route.provider, route.model)
```

In `reload()` (currently lines 319-326), recreate the tracker (it's cheap — just reloads its JSON file) and rewire the engine:

```python
    def reload(self) -> None:
        self._cfg = load_config(self._config_path)
        self._rate_limit_store = RateLimitStore(self._cfg.state_dir)
        self._quota_tracker = QuotaTracker(self._cfg.state_dir)
        self._engine.update_config(self._cfg)
        self._engine._rate_limit_store = self._rate_limit_store
        self._engine._quota_tracker = self._quota_tracker
        self._client._rate_limit_store = self._rate_limit_store
        self._audit = AuditLogger(self._cfg.state_dir)
        self._history = HealthHistory(self._cfg.state_dir, self._cfg.health_history_days)
```

- [ ] **Step 12: Write an end-to-end test that a router with an exhausted daily quota raises `RouterBusy`**

Add to `tests/test_agenerate_stream.py` or a new `tests/test_quota_integration.py` — mirror whatever mocking pattern `tests/test_agenerate_stream.py` already uses for `config_file`/`monkeypatch`(check that file's imports/fixtures first). The test should: build a config with one model with `quotas: {rpd: 1}`, pre-populate `QuotaTracker(state_dir).record(provider, model)` once before constructing `FlexRouter`, then call `router.agenerate(...)` (or `router.generate(...)`) and assert it raises `RouterBusy` immediately (no route available) rather than attempting an HTTP call.

- [ ] **Step 13: Run full suite and commit**

Run: `python -m pytest -v`
Expected: PASS, no regressions.

```bash
git add flexrouter/quota.py flexrouter/config.py flexrouter/engine.py flexrouter/_router.py tests/test_quota.py tests/test_config.py tests/test_engine.py
git commit -m "feat: persistent rpd/rph quota tracking via QuotaTracker"
```

---

### Task 3: Structured `reasoning` / `tool_call_delta` / `usage` stream events

**Files:**
- Modify: `flexrouter/client.py` (`stream_chat` — yields `StreamChunk` instead of `str`)
- Modify: `flexrouter/_router.py` (`StreamEvent` union, `agenerate_stream`)
- Modify: `tests/test_client.py`, `tests/test_agenerate_stream.py`

**Interfaces:**
- Produces: `flexrouter.client.StreamChunk` (dataclass: `content: str | None`, `reasoning: str | None`, `tool_call_delta: dict | None`, `usage: dict | None`), new `flexrouter._router.ReasoningDeltaEvent` (`text: str`), `flexrouter._router.ToolCallDeltaEvent` (`index: int`, `id: str | None`, `name: str | None`, `arguments: str | None`), both re-exported from `flexrouter/__init__.py` alongside the existing event types.
- Consumes: none from Task 1/2 — independent, can be done in parallel by a different worker.

**Note:** this task changes `AsyncClient.stream_chat`'s yielded type from `str` to `StreamChunk` — that is a breaking change to `client.py`'s own public surface, but `client.py` is an internal module (`_router.py` is the only consumer, per Task 1's exploration — confirmed via grep that no other file in the repo imports `stream_chat` directly). `agenerate_stream`'s public `StreamEvent` surface stays additive (`DeltaEvent.text` still means the same thing it always did).

- [ ] **Step 1: Write the failing test for `StreamChunk` extraction in `client.py`**

Add to `tests/test_client.py` (mirror the existing SSE-mocking pattern already used for `stream_chat` tests in that file — read it first for the exact `httpx.MockTransport` setup):

```python
async def test_stream_chat_yields_content_reasoning_tool_call_and_usage(monkeypatch):
    # Build a fake SSE stream with 4 chunks: content delta, reasoning delta,
    # tool_call delta, and a final usage-only chunk (empty delta).
    sse_lines = [
        'data: {"choices":[{"delta":{"content":"Hel"}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":"thinking..."}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"get_weather","arguments":"{\\"city\\":"}}]}}]}',
        'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":5}}',
        'data: [DONE]',
    ]
    # ... construct route/client the same way the existing stream_chat tests in
    # this file do, feed sse_lines through the mocked transport, collect all
    # yielded StreamChunk objects into a list called `chunks`.
    assert chunks[0].content == "Hel"
    assert chunks[1].reasoning == "thinking..."
    assert chunks[2].tool_call_delta == {"index": 0, "id": "c1", "function": {"name": "get_weather", "arguments": '{"city":'}}
    assert chunks[3].usage == {"prompt_tokens": 10, "completion_tokens": 5}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_client.py -k reasoning_tool_call -v`
Expected: FAIL (`stream_chat` doesn't exist as `StreamChunk`-yielding yet — either an `AttributeError` on `.content` since it currently yields plain strings, or a collection mismatch).

- [ ] **Step 3: Implement `StreamChunk` and update `stream_chat` in `client.py`**

Add near the top of `flexrouter/client.py` (after the existing exception classes):

```python
@dataclass
class StreamChunk:
    content: str | None = None
    reasoning: str | None = None
    tool_call_delta: dict | None = None
    usage: dict | None = None
```

Add `from dataclasses import dataclass` to the top imports.

Replace the payload construction in `stream_chat` (currently line 129) to request usage in the final SSE chunk:

```python
        payload = {"model": route.model, "messages": messages, "stream": True,
                   "stream_options": {"include_usage": True}, **kwargs}
```

Replace the chunk-parsing loop at the end of `stream_chat` (currently lines 162-186):

```python
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[len("data: "):].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = _json.loads(data_str)
                    except _json.JSONDecodeError as exc:
                        raise ProviderError(
                            f"{route.provider}/{route.model}: malformed SSE chunk: {exc}"
                        ) from exc

                    usage = chunk.get("usage")
                    choices = chunk.get("choices") or []
                    delta = choices[0].get("delta") if choices else None
                    if delta is None and usage is None:
                        continue

                    content = delta.get("content") if isinstance(delta, dict) else None
                    reasoning = delta.get("reasoning_content") if isinstance(delta, dict) else None
                    tool_calls = delta.get("tool_calls") if isinstance(delta, dict) else None

                    if tool_calls:
                        for tc in tool_calls:
                            yield StreamChunk(tool_call_delta=tc)
                        continue

                    if content or reasoning or usage:
                        yield StreamChunk(content=content or None, reasoning=reasoning or None, usage=usage)
```

Note this drops the old strict `KeyError`-on-missing-`delta` validation (lines 177-182 previously) because a usage-only final chunk legitimately has no `delta.content` to require — `delta is None and usage is None: continue` is the new "skip nothing-here" guard, and a present-but-empty `delta` (`{}`) with no `usage` also produces no yield (all three of `content`/`reasoning`/`usage` falsy), matching prior behavior of silently skipping empty deltas.

- [ ] **Step 4: Run test from Step 1, then the full `test_client.py` suite**

Run: `python -m pytest tests/test_client.py -v`
Expected: PASS, including the new reasoning/tool_call/usage test and all prior `stream_chat` tests (update any prior test in this file that asserted `stream_chat` yields bare strings — change assertions to `chunk.content == "..."`).

- [ ] **Step 5: Add `ReasoningDeltaEvent`/`ToolCallDeltaEvent` and update `agenerate_stream` in `_router.py`**

Extend the event dataclasses (currently lines 22-49):

```python
@dataclass
class ReasoningDeltaEvent:
    text: str


@dataclass
class ToolCallDeltaEvent:
    index: int
    id: Optional[str]
    name: Optional[str]
    arguments: Optional[str]


StreamEvent = AttemptEvent | AttemptFailedEvent | DeltaEvent | ReasoningDeltaEvent | ToolCallDeltaEvent | DoneEvent
```

Replace the commit-point first-chunk handling (currently lines 282-289):

```python
            accumulated: list[str] = []
            final_usage: dict = {}

            def _events_for(sc) -> list:
                events: list = []
                if sc.content:
                    accumulated.append(sc.content)
                    events.append(DeltaEvent(text=sc.content))
                if sc.reasoning:
                    events.append(ReasoningDeltaEvent(text=sc.reasoning))
                if sc.tool_call_delta:
                    fn = sc.tool_call_delta.get("function") or {}
                    events.append(ToolCallDeltaEvent(
                        index=sc.tool_call_delta.get("index", 0),
                        id=sc.tool_call_delta.get("id"),
                        name=fn.get("name"),
                        arguments=fn.get("arguments"),
                    ))
                if sc.usage:
                    final_usage.update(sc.usage)
                return events

            if first_chunk is not None:
                for ev in _events_for(first_chunk):
                    yield ev

            async for sc in stream:
                for ev in _events_for(sc):
                    yield ev
```

Replace the result-building block (currently lines 291-296):

```python
            latency_ms = int((time.monotonic() - start) * 1000)
            full_text = "".join(accumulated)
            result = {
                "choices": [{"message": {"role": "assistant", "content": full_text}}],
                "usage": final_usage,
            }
```

- [ ] **Step 6: Update `tests/test_agenerate_stream.py` for the new commit-point structure**

The existing tests assume `first_chunk` is a bare string (e.g. `first_chunk = "Hel"`, per the report's `_router.py:190-197` usage). Update each test's mocked `stream_chat` fake to yield `StreamChunk(content=...)` objects instead of bare strings — read the file's mocking helper (likely a fake async generator function patched onto `AsyncClient.stream_chat`) and update every `yield "..."` to `yield StreamChunk(content="...")`.

Add new test cases:

```python
async def test_reasoning_delta_is_yielded_as_separate_event(config_file, monkeypatch):
    # patch AsyncClient.stream_chat to yield:
    #   StreamChunk(reasoning="thinking"), StreamChunk(content="answer")
    ...
    events = [e async for e in router.agenerate_stream(MESSAGES, tier="low")]
    reasoning_events = [e for e in events if isinstance(e, ReasoningDeltaEvent)]
    assert len(reasoning_events) == 1
    assert reasoning_events[0].text == "thinking"


async def test_tool_call_delta_is_yielded_and_not_accumulated_into_content(config_file, monkeypatch):
    # patch to yield StreamChunk(tool_call_delta={"index": 0, "id": "c1", "function": {"name": "f", "arguments": "{}"}})
    ...
    events = [e async for e in router.agenerate_stream(MESSAGES, tier="low")]
    tc_events = [e for e in events if isinstance(e, ToolCallDeltaEvent)]
    assert len(tc_events) == 1
    assert tc_events[0].name == "f"
    done = [e for e in events if isinstance(e, DoneEvent)][0]
    assert done.result["choices"][0]["message"]["content"] == ""  # tool call didn't add to text


async def test_final_usage_chunk_populates_done_event_usage(config_file, monkeypatch):
    # patch to yield StreamChunk(content="hi"), StreamChunk(usage={"prompt_tokens": 3, "completion_tokens": 1})
    ...
    events = [e async for e in router.agenerate_stream(MESSAGES, tier="low")]
    done = [e for e in events if isinstance(e, DoneEvent)][0]
    assert done.result["usage"] == {"prompt_tokens": 3, "completion_tokens": 1}
```

- [ ] **Step 7: Run the full stream test suite**

Run: `python -m pytest tests/test_agenerate_stream.py tests/test_client.py -v`
Expected: PASS, no regressions in existing commit-point / retry-interleaving tests.

- [ ] **Step 8: Re-export new event types from `flexrouter/__init__.py`**

Modify `flexrouter/__init__.py`'s import line (currently `from flexrouter._router import FlexRouter, AttemptEvent, AttemptFailedEvent, DeltaEvent, DoneEvent`):

```python
from flexrouter._router import (
    FlexRouter, AttemptEvent, AttemptFailedEvent, DeltaEvent,
    ReasoningDeltaEvent, ToolCallDeltaEvent, DoneEvent,
)
```

Update `__all__` in the same file to include `"ReasoningDeltaEvent"` and `"ToolCallDeltaEvent"`.

- [ ] **Step 9: Run full suite and commit**

Run: `python -m pytest -v`
Expected: PASS, no regressions across the whole repo (Tasks 1 and 2's tests must also still pass if done first).

```bash
git add flexrouter/client.py flexrouter/_router.py flexrouter/__init__.py tests/test_client.py tests/test_agenerate_stream.py
git commit -m "feat: structured reasoning/tool_call_delta/usage stream events"
```

---

## Not in this plan (deliberately out of scope)

- **Side 1b (data migration)** — pouring OpenVL's model/key roster into `flexrouter.yaml` using the new `header_parser`/`quotas` fields. This is a config-data task, not a code task; do it once Tasks 1–2 are merged, by hand-editing `flexrouter.yaml` (no code changes needed since both fields are optional/additive).
- **Side 2 (OpenVL rewire)** — rewriting `agent/llm.py` to call flexrouter instead of `router/`, deleting OpenVL's `router/` package. This depends on Side 1b being done first and is risky enough to warrant its own plan + its own review checkpoints. Write that plan only after this one is merged and Side 1b's data migration is complete.
