# flexrouter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `flexrouter`, a Python library that routes LLM requests across providers using named tiers, with rate-limit awareness, cost tracking, and a live dashboard.

**Architecture:** Stateful `FlexRouter` class wraps an async routing engine; sync `generate()` runs async engine via managed event loop. State (windows, penalties, sessions) lives in-memory; audit log and health snapshot persist to `.flexrouter/` directory. Dashboard is a pre-built React SPA served by a lightweight Python HTTP server.

**Tech Stack:** Python 3.11+, httpx, pyyaml, watchdog, tiktoken, click, pytest, respx, React + Vite + Tailwind (pre-built, committed)

---

## File Map

```
flexrouter/
├── __init__.py          # FlexRouter class + public re-exports
├── exceptions.py        # RouterBusy, RouterError, ContextWindowWarning
├── config.py            # FlexConfig dataclasses + YAML loader + mtime watcher
├── window.py            # SlidingWindow — RPM/TPM tracking per model
├── recovery.py          # PenaltyBox — exponential backoff per model/key
├── budget.py            # DailyBudget — per-provider daily spend cap
├── hooks.py             # HookRunner — detect_vision, estimate_tokens
├── engine.py            # RoutingEngine — tier filter, scoring, session stickiness
├── client.py            # AsyncClient — httpx dispatch, OpenAI-compatible
├── audit.py             # AuditLogger — CSV append + health.json write
├── cli.py               # Click CLI — init, dashboard, status, config
└── dashboard/
    ├── __init__.py
    ├── server.py         # HTTP server — /api/* + static file serving
    ├── api.py            # API handlers — status, logs, config GET/POST
    └── static/           # Pre-built React frontend (committed)
        └── index.html

tests/
├── conftest.py
├── test_exceptions.py
├── test_window.py
├── test_recovery.py
├── test_budget.py
├── test_config.py
├── test_hooks.py
├── test_engine.py
├── test_client.py
├── test_audit.py
└── test_flexrouter.py   # integration tests for FlexRouter.generate()

dashboard/frontend/       # npm project — run `npm run build` to update static/
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── api.ts            # fetch wrappers for /api/*
│   ├── tabs/
│   │   ├── LiveTelemetry.tsx
│   │   ├── Chat.tsx
│   │   ├── RequestLogs.tsx
│   │   ├── AccountStatus.tsx
│   │   ├── Settings.tsx
│   │   └── Setup.tsx
│   └── components/
│       ├── ModelRow.tsx
│       ├── RateBar.tsx
│       ├── StatusDot.tsx
│       └── Drawer.tsx
├── package.json
├── vite.config.ts
└── tailwind.config.js

pyproject.toml
MANIFEST.in
.gitignore
.github/workflows/publish.yml
```

---

## Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `flexrouter/__init__.py`
- Create: `flexrouter/exceptions.py`
- Create: `tests/conftest.py`
- Create: `.gitignore`
- Create: `MANIFEST.in`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "flexrouter"
version = "0.1.0"
description = "Universal LLM router with named tiers, rate-limit awareness, and a live dashboard"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "pyyaml>=6.0",
    "watchdog>=4.0",
    "tiktoken>=0.7",
    "click>=8.1",
]

[project.scripts]
flexrouter = "flexrouter.cli:cli"

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "respx>=0.21",
    "pytest-cov>=5.0",
]

[tool.setuptools.packages.find]
where = ["."]
include = ["flexrouter*"]

[tool.setuptools.package-data]
"flexrouter.dashboard" = ["static/**/*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create `flexrouter/exceptions.py`**

```python
class RouterBusy(Exception):
    """All models in the requested tier are rate-limited and wait=False."""

class RouterError(Exception):
    """Unrecoverable provider error (auth failure, repeated 5xx)."""

class ConfigError(Exception):
    """Invalid or missing flexrouter.yaml."""

class ContextWindowWarning(UserWarning):
    """Some models in tier skipped due to context window size."""
```

- [ ] **Step 3: Create `flexrouter/__init__.py`**

```python
from flexrouter._router import FlexRouter
from flexrouter.exceptions import RouterBusy, RouterError, ConfigError, ContextWindowWarning

__all__ = ["FlexRouter", "RouterBusy", "RouterError", "ConfigError", "ContextWindowWarning"]
```

- [ ] **Step 4: Create `tests/conftest.py`**

```python
import pytest
from pathlib import Path
import tempfile, os, yaml

MINIMAL_CONFIG = {
    "tiers": {
        "low": [
            {
                "provider": "groq",
                "model": "llama-3.1-8b-instant",
                "score": 85,
                "rpm": 60,
                "tpm": 60000,
                "context_window": 131072,
            }
        ]
    },
    "providers": {
        "groq": {
            "base_url": "https://api.groq.com/openai/v1",
            "api_keys": [{"env": "GROQ_API_KEY"}],
        }
    },
    "settings": {
        "state_dir": "",  # overridden per test
        "window_seconds": 60,
        "penalty_base_seconds": 30,
        "penalty_max_seconds": 1800,
        "session_ttl_minutes": 30,
        "dashboard_port": 7352,
        "retry_policy": "balanced",
    },
}

@pytest.fixture
def config_file(tmp_path):
    cfg = dict(MINIMAL_CONFIG)
    cfg["settings"] = dict(cfg["settings"])
    cfg["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    os.environ["GROQ_API_KEY"] = "test-key"
    yield p
    os.environ.pop("GROQ_API_KEY", None)
```

- [ ] **Step 5: Create `.gitignore`**

```
.flexrouter/
__pycache__/
*.py[cod]
.pytest_cache/
dist/
*.egg-info/
.env
dashboard/frontend/node_modules/
dashboard/frontend/dist/
```

- [ ] **Step 6: Create `MANIFEST.in`**

```
recursive-include flexrouter/dashboard/static *
```

- [ ] **Step 7: Install dev dependencies and verify**

```bash
pip install -e ".[dev]"
pytest --collect-only
```

Expected: `no tests ran` (0 errors, just no tests yet).

- [ ] **Step 8: Commit**

```bash
git init
git add pyproject.toml flexrouter/ tests/ .gitignore MANIFEST.in
git commit -m "feat: scaffold flexrouter package"
```

---

## Task 2: Sliding Window Rate Limiter

**Files:**
- Create: `flexrouter/window.py`
- Create: `tests/test_window.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_window.py
import time
import pytest
from flexrouter.window import SlidingWindow

def test_empty_window_is_available():
    w = SlidingWindow(window_seconds=60)
    assert w.available(rpm_limit=10, tpm_limit=10000) is True

def test_records_increment_rpm():
    w = SlidingWindow(window_seconds=60)
    w.record(tokens=100)
    w.record(tokens=200)
    assert w.current_rpm() == 2
    assert w.current_tpm() == 300

def test_rpm_limit_exhausted():
    w = SlidingWindow(window_seconds=60)
    for _ in range(5):
        w.record(tokens=0)
    assert w.available(rpm_limit=5, tpm_limit=999999) is False

def test_tpm_limit_exhausted():
    w = SlidingWindow(window_seconds=60)
    w.record(tokens=5000)
    assert w.available(rpm_limit=999, tpm_limit=4999) is False

def test_old_records_expire(monkeypatch):
    now = time.monotonic()
    w = SlidingWindow(window_seconds=60)
    # Manually backdate a record
    from collections import deque
    w._requests = deque([now - 61])
    w._tokens = deque([(now - 61, 500)])
    assert w.current_rpm() == 0
    assert w.current_tpm() == 0

def test_seconds_until_available_zero_when_free():
    w = SlidingWindow(window_seconds=60)
    assert w.seconds_until_available(rpm_limit=10, tpm_limit=10000) == 0.0

def test_seconds_until_available_positive_when_full(monkeypatch):
    now = time.monotonic()
    w = SlidingWindow(window_seconds=60)
    # Put one record 30s ago
    w._requests = __import__("collections").deque([now - 30])
    w._tokens = __import__("collections").deque([(now - 30, 0)])
    secs = w.seconds_until_available(rpm_limit=1, tpm_limit=9999)
    assert 29 <= secs <= 31
```

- [ ] **Step 2: Run — expect failures**

```bash
pytest tests/test_window.py -v
```

Expected: `ImportError: cannot import name 'SlidingWindow'`

- [ ] **Step 3: Implement `flexrouter/window.py`**

```python
from collections import deque
import time


class SlidingWindow:
    """Tracks requests and tokens within a rolling time window."""

    def __init__(self, window_seconds: int) -> None:
        self.window_seconds = window_seconds
        self._requests: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()

    def record(self, tokens: int = 0) -> None:
        now = time.monotonic()
        self._requests.append(now)
        self._tokens.append((now, tokens))
        self._clean(now)

    def current_rpm(self) -> int:
        self._clean(time.monotonic())
        return len(self._requests)

    def current_tpm(self) -> int:
        self._clean(time.monotonic())
        return sum(t for _, t in self._tokens)

    def available(self, rpm_limit: int, tpm_limit: int) -> bool:
        return self.current_rpm() < rpm_limit and self.current_tpm() < tpm_limit

    def seconds_until_available(self, rpm_limit: int, tpm_limit: int) -> float:
        now = time.monotonic()
        self._clean(now)
        if self.available(rpm_limit, tpm_limit):
            return 0.0
        if self._requests:
            return max(0.0, self._requests[0] + self.window_seconds - now)
        return 0.0

    def _clean(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._requests and self._requests[0] < cutoff:
            self._requests.popleft()
        while self._tokens and self._tokens[0][0] < cutoff:
            self._tokens.popleft()
```

- [ ] **Step 4: Run — expect all pass**

```bash
pytest tests/test_window.py -v
```

Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add flexrouter/window.py tests/test_window.py
git commit -m "feat: sliding window rate limiter"
```

---

## Task 3: Penalty Box

**Files:**
- Create: `flexrouter/recovery.py`
- Create: `tests/test_recovery.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_recovery.py
import time
import pytest
from flexrouter.recovery import PenaltyBox

def test_no_penalty_initially():
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    assert pb.is_penalized("groq", "llama") is False

def test_penalize_sets_cooldown():
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.penalize("groq", "llama")
    assert pb.is_penalized("groq", "llama") is True

def test_penalty_doubles_on_repeat():
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.penalize("groq", "llama")
    first = pb.penalty_seconds("groq", "llama")
    pb.penalize("groq", "llama")
    second = pb.penalty_seconds("groq", "llama")
    assert second == first * 2

def test_penalty_caps_at_max():
    pb = PenaltyBox(base_seconds=30, max_seconds=60)
    for _ in range(10):
        pb.penalize("groq", "llama")
    assert pb.penalty_seconds("groq", "llama") <= 60

def test_short_penalty():
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.penalize_short("groq", "llama", seconds=30)
    assert pb.is_penalized("groq", "llama") is True

def test_clear_removes_penalty():
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.penalize("groq", "llama")
    pb.clear("groq", "llama")
    assert pb.is_penalized("groq", "llama") is False

def test_penalty_expires(monkeypatch):
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.penalize("groq", "llama")
    # Move time forward past penalty
    monkeypatch.setattr(time, "monotonic", lambda: time.monotonic.__wrapped__() + 35)
    assert pb.is_penalized("groq", "llama") is False

def test_penalty_until_returns_timestamp():
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.penalize("groq", "llama")
    until = pb.penalty_until("groq", "llama")
    assert until is not None
    assert until > time.monotonic()
```

- [ ] **Step 2: Run — expect failures**

```bash
pytest tests/test_recovery.py -v
```

Expected: `ImportError: cannot import name 'PenaltyBox'`

- [ ] **Step 3: Implement `flexrouter/recovery.py`**

```python
import time
from typing import Optional


class PenaltyBox:
    """Tracks per-(provider, model) exponential backoff penalties."""

    def __init__(self, base_seconds: int, max_seconds: int) -> None:
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds
        # key -> (penalty_until: float, failure_count: int)
        self._state: dict[str, tuple[float, int]] = {}

    def _key(self, provider: str, model: str) -> str:
        return f"{provider}/{model}"

    def penalize(self, provider: str, model: str) -> None:
        k = self._key(provider, model)
        _, count = self._state.get(k, (0.0, 0))
        count += 1
        secs = min(self.base_seconds * (2 ** (count - 1)), self.max_seconds)
        self._state[k] = (time.monotonic() + secs, count)

    def penalize_short(self, provider: str, model: str, seconds: int) -> None:
        k = self._key(provider, model)
        _, count = self._state.get(k, (0.0, 0))
        self._state[k] = (time.monotonic() + seconds, count)

    def clear(self, provider: str, model: str) -> None:
        self._state.pop(self._key(provider, model), None)

    def is_penalized(self, provider: str, model: str) -> bool:
        k = self._key(provider, model)
        if k not in self._state:
            return False
        until, _ = self._state[k]
        if time.monotonic() >= until:
            del self._state[k]
            return False
        return True

    def penalty_seconds(self, provider: str, model: str) -> int:
        k = self._key(provider, model)
        _, count = self._state.get(k, (0.0, 0))
        return min(self.base_seconds * (2 ** (count - 1)), self.max_seconds)

    def penalty_until(self, provider: str, model: str) -> Optional[float]:
        k = self._key(provider, model)
        if k not in self._state:
            return None
        until, _ = self._state[k]
        return until if time.monotonic() < until else None
```

- [ ] **Step 4: Fix monkeypatch test** — the `monkeypatch` approach for `time.monotonic` requires a wrapper. Replace the `test_penalty_expires` test with a time-based approach:

```python
def test_penalty_expires():
    pb = PenaltyBox(base_seconds=1, max_seconds=10)  # 1s penalty
    pb.penalize("groq", "llama")
    assert pb.is_penalized("groq", "llama") is True
    time.sleep(1.1)
    assert pb.is_penalized("groq", "llama") is False
```

- [ ] **Step 5: Run — expect all pass**

```bash
pytest tests/test_recovery.py -v
```

Expected: `8 passed`

- [ ] **Step 6: Commit**

```bash
git add flexrouter/recovery.py tests/test_recovery.py
git commit -m "feat: penalty box with exponential backoff"
```

---

## Task 4: Daily Budget Tracker

**Files:**
- Create: `flexrouter/budget.py`
- Create: `tests/test_budget.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_budget.py
import pytest
from flexrouter.budget import DailyBudget

def test_no_budget_always_available():
    db = DailyBudget(limits={})
    assert db.is_available("openai") is True

def test_under_budget_available():
    db = DailyBudget(limits={"openai": 5.00})
    db.record("openai", cost_usd=1.00)
    assert db.is_available("openai") is True

def test_over_budget_unavailable():
    db = DailyBudget(limits={"openai": 5.00})
    db.record("openai", cost_usd=5.01)
    assert db.is_available("openai") is False

def test_exact_budget_unavailable():
    db = DailyBudget(limits={"openai": 5.00})
    db.record("openai", cost_usd=5.00)
    assert db.is_available("openai") is False

def test_daily_spend_resets_at_midnight():
    db = DailyBudget(limits={"openai": 5.00})
    db.record("openai", cost_usd=5.01)
    # Force day rollover
    import datetime
    db._day["openai"] = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    assert db.is_available("openai") is True

def test_provider_without_limit_always_available():
    db = DailyBudget(limits={"openai": 5.00})
    assert db.is_available("groq") is True

def test_total_spent_tracks_correctly():
    db = DailyBudget(limits={})
    db.record("openai", cost_usd=1.50)
    db.record("openai", cost_usd=0.50)
    assert db.daily_spend("openai") == pytest.approx(2.00)
```

- [ ] **Step 2: Run — expect failures**

```bash
pytest tests/test_budget.py -v
```

Expected: `ImportError: cannot import name 'DailyBudget'`

- [ ] **Step 3: Implement `flexrouter/budget.py`**

```python
import datetime
from typing import Optional


class DailyBudget:
    """Tracks per-provider daily spend against configurable limits."""

    def __init__(self, limits: dict[str, float]) -> None:
        self._limits = limits
        self._spend: dict[str, float] = {}
        self._day: dict[str, str] = {}

    def record(self, provider: str, cost_usd: float) -> None:
        self._maybe_reset(provider)
        self._spend[provider] = self._spend.get(provider, 0.0) + cost_usd

    def is_available(self, provider: str) -> bool:
        if provider not in self._limits:
            return True
        self._maybe_reset(provider)
        return self._spend.get(provider, 0.0) < self._limits[provider]

    def daily_spend(self, provider: str) -> float:
        self._maybe_reset(provider)
        return self._spend.get(provider, 0.0)

    def _maybe_reset(self, provider: str) -> None:
        today = datetime.date.today().isoformat()
        if self._day.get(provider) != today:
            self._spend[provider] = 0.0
            self._day[provider] = today
```

- [ ] **Step 4: Run — expect all pass**

```bash
pytest tests/test_budget.py -v
```

Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add flexrouter/budget.py tests/test_budget.py
git commit -m "feat: daily budget tracker per provider"
```

---

## Task 5: Config Loader

**Files:**
- Create: `flexrouter/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py
import os, yaml, pytest
from pathlib import Path
from flexrouter.config import FlexConfig, load_config, RETRY_PRESETS

def test_load_minimal_config(config_file):
    cfg = load_config(config_file)
    assert "low" in cfg.tiers
    assert cfg.tiers["low"][0].model == "llama-3.1-8b-instant"
    assert cfg.tiers["low"][0].score == 85

def test_provider_api_key_resolved_from_env(config_file):
    cfg = load_config(config_file)
    keys = cfg.providers["groq"].api_keys
    assert keys[0] == "test-key"

def test_missing_env_var_raises(tmp_path):
    raw = {
        "tiers": {"low": [{"provider": "x", "model": "m", "score": 50, "rpm": 10, "tpm": 1000, "context_window": 4096}]},
        "providers": {"x": {"base_url": "http://x", "api_keys": [{"env": "MISSING_KEY_XYZ"}]}},
        "settings": {"state_dir": str(tmp_path)},
    }
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(raw))
    with pytest.raises(Exception, match="MISSING_KEY_XYZ"):
        load_config(p)

def test_retry_preset_balanced(config_file):
    cfg = load_config(config_file)
    assert cfg.retry.retries == RETRY_PRESETS["balanced"]["retries"]
    assert cfg.retry.backoff_seconds == RETRY_PRESETS["balanced"]["backoff_seconds"]

def test_manual_retry_override(tmp_path):
    raw = yaml.safe_load((tmp_path.parent / "flexrouter.yaml").read_text()) if (tmp_path.parent / "flexrouter.yaml").exists() else None
    # Build config with manual retry
    import os
    os.environ["GROQ_API_KEY"] = "k"
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump({
        "tiers": {"low": [{"provider": "groq", "model": "m", "score": 50, "rpm": 10, "tpm": 1000, "context_window": 4096}]},
        "providers": {"groq": {"base_url": "http://groq", "api_keys": [{"env": "GROQ_API_KEY"}]}},
        "settings": {"state_dir": str(tmp_path), "retries": 7, "backoff_seconds": 3.5},
    }))
    cfg = load_config(p)
    assert cfg.retry.retries == 7
    assert cfg.retry.backoff_seconds == 3.5

def test_config_defaults(config_file):
    cfg = load_config(config_file)
    assert cfg.window_seconds == 60
    assert cfg.session_ttl_minutes == 30
    assert cfg.dashboard_port == 7352

def test_vision_flag_parsed(tmp_path):
    os.environ["GROQ_API_KEY"] = "k"
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump({
        "tiers": {"high": [{"provider": "groq", "model": "vision-model", "score": 90, "rpm": 10, "tpm": 1000, "context_window": 4096, "vision": True}]},
        "providers": {"groq": {"base_url": "http://groq", "api_keys": [{"env": "GROQ_API_KEY"}]}},
        "settings": {"state_dir": str(tmp_path)},
    }))
    cfg = load_config(p)
    assert cfg.tiers["high"][0].vision is True
```

- [ ] **Step 2: Run — expect failures**

```bash
pytest tests/test_config.py -v
```

Expected: `ImportError: cannot import name 'FlexConfig'`

- [ ] **Step 3: Implement `flexrouter/config.py`**

```python
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import yaml
from flexrouter.exceptions import ConfigError

RETRY_PRESETS = {
    "conservative": {"retries": 2, "backoff_seconds": 5.0},
    "balanced":     {"retries": 3, "backoff_seconds": 2.0},
    "aggressive":   {"retries": 5, "backoff_seconds": 1.0},
}

@dataclass
class ModelConfig:
    provider: str
    model: str
    score: int
    rpm: int
    tpm: int
    context_window: int = 200000
    vision: bool = False

@dataclass
class ProviderConfig:
    base_url: str
    api_keys: list[str]  # resolved values

@dataclass
class RetryConfig:
    retries: int = 3
    backoff_seconds: float = 2.0

@dataclass
class FlexConfig:
    tiers: dict[str, list[ModelConfig]]
    providers: dict[str, ProviderConfig]
    state_dir: str = ".flexrouter"
    window_seconds: int = 60
    penalty_base_seconds: int = 30
    penalty_max_seconds: int = 1800
    session_ttl_minutes: int = 30
    dashboard_port: int = 7352
    retry: RetryConfig = field(default_factory=RetryConfig)
    provider_budget: dict[str, float] = field(default_factory=dict)
    hooks: list[str] = field(default_factory=list)


def load_config(path: Path) -> FlexConfig:
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except Exception as e:
        raise ConfigError(f"Cannot read {path}: {e}") from e

    if not raw:
        raise ConfigError(f"{path} is empty")

    settings = raw.get("settings", {})

    # Parse retry
    preset_name = settings.get("retry_policy", "balanced")
    preset = RETRY_PRESETS.get(preset_name, RETRY_PRESETS["balanced"])
    retry = RetryConfig(
        retries=settings.get("retries", preset["retries"]),
        backoff_seconds=settings.get("backoff_seconds", preset["backoff_seconds"]),
    )

    # Parse providers
    providers: dict[str, ProviderConfig] = {}
    for name, praw in raw.get("providers", {}).items():
        keys_raw = praw.get("api_keys", [])
        if isinstance(keys_raw, str):
            keys_raw = [{"env": keys_raw}]
        resolved: list[str] = []
        for k in keys_raw:
            env_name = k["env"]
            val = os.environ.get(env_name)
            if not val:
                raise ConfigError(f"Env var {env_name!r} not set (required by provider {name!r})")
            resolved.append(val)
        providers[name] = ProviderConfig(base_url=praw["base_url"], api_keys=resolved)

    # Parse tiers
    tiers: dict[str, list[ModelConfig]] = {}
    for tier_name, models in raw.get("tiers", {}).items():
        tiers[tier_name] = [
            ModelConfig(
                provider=m["provider"],
                model=m["model"],
                score=m["score"],
                rpm=m["rpm"],
                tpm=m["tpm"],
                context_window=m.get("context_window", 200000),
                vision=m.get("vision", False),
            )
            for m in models
        ]

    return FlexConfig(
        tiers=tiers,
        providers=providers,
        state_dir=settings.get("state_dir", ".flexrouter"),
        window_seconds=settings.get("window_seconds", 60),
        penalty_base_seconds=settings.get("penalty_base_seconds", 30),
        penalty_max_seconds=settings.get("penalty_max_seconds", 1800),
        session_ttl_minutes=settings.get("session_ttl_minutes", 30),
        dashboard_port=settings.get("dashboard_port", 7352),
        retry=retry,
        provider_budget=settings.get("provider_budget", {}),
        hooks=settings.get("hooks", []),
    )


def discover_config() -> Optional[Path]:
    """Search CWD then home for flexrouter.yaml."""
    cwd_path = Path("flexrouter.yaml")
    if cwd_path.exists():
        return cwd_path
    home_path = Path.home() / ".flexrouter.yaml"
    if home_path.exists():
        return home_path
    return None
```

- [ ] **Step 4: Run — expect all pass**

```bash
pytest tests/test_config.py -v
```

Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add flexrouter/config.py tests/test_config.py
git commit -m "feat: YAML config loader with retry presets and env var resolution"
```

---

## Task 6: Built-in Hooks

**Files:**
- Create: `flexrouter/hooks.py`
- Create: `tests/test_hooks.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_hooks.py
import pytest
from flexrouter.hooks import HookRunner, HookContext

def make_ctx(messages, hooks, token_counts=None):
    return HookContext(
        messages=messages,
        hooks=hooks,
        token_counts=token_counts or {},
    )

def test_detect_vision_sets_flag_when_image_present():
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "http://x.com/img.jpg"}},
        {"type": "text", "text": "What is this?"},
    ]}]
    ctx = make_ctx(messages, hooks=["detect_vision"])
    runner = HookRunner()
    result = runner.run(ctx)
    assert result.vision is True

def test_detect_vision_no_flag_when_text_only():
    messages = [{"role": "user", "content": "hello"}]
    ctx = make_ctx(messages, hooks=["detect_vision"])
    runner = HookRunner()
    result = runner.run(ctx)
    assert result.vision is False

def test_estimate_tokens_returns_count():
    messages = [{"role": "user", "content": "hello world"}]
    ctx = make_ctx(messages, hooks=["estimate_tokens"])
    runner = HookRunner()
    result = runner.run(ctx)
    assert result.estimated_tokens > 0

def test_no_hooks_returns_defaults():
    messages = [{"role": "user", "content": "hi"}]
    ctx = make_ctx(messages, hooks=[])
    runner = HookRunner()
    result = runner.run(ctx)
    assert result.vision is False
    assert result.estimated_tokens == 0

def test_unknown_hook_raises():
    ctx = make_ctx([], hooks=["nonexistent_hook"])
    runner = HookRunner()
    with pytest.raises(ValueError, match="Unknown hook"):
        runner.run(ctx)
```

- [ ] **Step 2: Run — expect failures**

```bash
pytest tests/test_hooks.py -v
```

Expected: `ImportError: cannot import name 'HookRunner'`

- [ ] **Step 3: Implement `flexrouter/hooks.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
import tiktoken


@dataclass
class HookContext:
    messages: list[dict]
    hooks: list[str]
    token_counts: dict = field(default_factory=dict)
    vision: bool = False
    estimated_tokens: int = 0


class HookRunner:
    _HOOKS = {"detect_vision", "estimate_tokens"}

    def run(self, ctx: HookContext) -> HookContext:
        for hook in ctx.hooks:
            if hook not in self._HOOKS:
                raise ValueError(f"Unknown hook: {hook!r}. Available: {sorted(self._HOOKS)}")
            getattr(self, f"_hook_{hook}")(ctx)
        return ctx

    def _hook_detect_vision(self, ctx: HookContext) -> None:
        for msg in ctx.messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        ctx.vision = True
                        return

    def _hook_estimate_tokens(self, ctx: HookContext) -> None:
        enc = tiktoken.get_encoding("cl100k_base")
        total = 0
        for msg in ctx.messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(enc.encode(content))
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += len(enc.encode(part.get("text", "")))
        ctx.estimated_tokens = total
```

- [ ] **Step 4: Run — expect all pass**

```bash
pytest tests/test_hooks.py -v
```

Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add flexrouter/hooks.py tests/test_hooks.py
git commit -m "feat: built-in hooks (detect_vision, estimate_tokens)"
```

---

## Task 7: Routing Engine

**Files:**
- Create: `flexrouter/engine.py`
- Create: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_engine.py
import time, warnings, pytest
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig, RetryConfig
from flexrouter.window import SlidingWindow
from flexrouter.recovery import PenaltyBox
from flexrouter.budget import DailyBudget
from flexrouter.engine import RoutingEngine, RouteResult

def make_engine(models=None):
    if models is None:
        models = [
            ModelConfig("groq", "llama-8b", score=85, rpm=60, tpm=60000, context_window=131072),
            ModelConfig("groq", "llama-70b", score=70, rpm=30, tpm=30000, context_window=131072),
        ]
    cfg = FlexConfig(
        tiers={"low": models},
        providers={"groq": ProviderConfig("http://groq", ["key"])},
        window_seconds=60,
        penalty_base_seconds=30,
        penalty_max_seconds=1800,
        session_ttl_minutes=30,
    )
    return RoutingEngine(cfg)

def test_picks_highest_score_when_both_available():
    engine = make_engine()
    result = engine.select("low", estimated_tokens=0, vision=False)
    assert result.model == "llama-8b"  # score 85 wins over 70

def test_skips_penalized_model():
    engine = make_engine()
    engine._penalties.penalize("groq", "llama-8b")
    result = engine.select("low", estimated_tokens=0, vision=False)
    assert result.model == "llama-70b"

def test_skips_rpm_exhausted_model():
    engine = make_engine()
    # Fill llama-8b's window
    w = engine._windows["groq/llama-8b"]
    for _ in range(60):
        w.record(tokens=0)
    result = engine.select("low", estimated_tokens=0, vision=False)
    assert result.model == "llama-70b"

def test_no_models_available_returns_none():
    engine = make_engine()
    engine._penalties.penalize("groq", "llama-8b")
    engine._penalties.penalize("groq", "llama-70b")
    result = engine.select("low", estimated_tokens=0, vision=False)
    assert result is None

def test_unknown_tier_raises():
    engine = make_engine()
    with pytest.raises(KeyError):
        engine.select("nuclear", estimated_tokens=0, vision=False)

def test_vision_filter_skips_non_vision():
    models = [
        ModelConfig("groq", "vision-model", score=90, rpm=60, tpm=60000, context_window=4096, vision=True),
        ModelConfig("groq", "text-model", score=95, rpm=60, tpm=60000, context_window=4096, vision=False),
    ]
    engine = make_engine(models)
    result = engine.select("low", estimated_tokens=0, vision=True)
    assert result.model == "vision-model"

def test_context_window_skip_emits_warning():
    models = [
        ModelConfig("groq", "small", score=90, rpm=60, tpm=60000, context_window=100),
        ModelConfig("groq", "large", score=80, rpm=60, tpm=60000, context_window=200000),
    ]
    engine = make_engine(models)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = engine.select("low", estimated_tokens=500, vision=False)
    assert result.model == "large"
    assert any("ContextWindowWarning" in str(x.category) for x in w)

def test_session_stickiness_returns_same_model():
    engine = make_engine()
    r1 = engine.select("low", estimated_tokens=0, vision=False, session_id="s1")
    r2 = engine.select("low", estimated_tokens=0, vision=False, session_id="s1")
    assert r1.model == r2.model

def test_session_expiry_rerouts(monkeypatch):
    engine = make_engine()
    engine._session_ttl = 0  # immediate expiry
    engine.select("low", estimated_tokens=0, vision=False, session_id="s2")
    # After expiry, session cleared — still returns a valid model
    result = engine.select("low", estimated_tokens=0, vision=False, session_id="s2")
    assert result is not None

def test_seconds_until_available():
    engine = make_engine()
    engine._penalties.penalize("groq", "llama-8b")
    engine._penalties.penalize("groq", "llama-70b")
    secs = engine.seconds_until_available("low")
    assert secs >= 0
```

- [ ] **Step 2: Run — expect failures**

```bash
pytest tests/test_engine.py -v
```

Expected: `ImportError: cannot import name 'RoutingEngine'`

- [ ] **Step 3: Implement `flexrouter/engine.py`**

```python
from __future__ import annotations
import random
import time
import warnings
from dataclasses import dataclass
from typing import Optional

from flexrouter.config import FlexConfig, ModelConfig
from flexrouter.exceptions import ContextWindowWarning
from flexrouter.recovery import PenaltyBox
from flexrouter.window import SlidingWindow
from flexrouter.budget import DailyBudget


@dataclass
class RouteResult:
    provider: str
    model: str
    api_key: str
    base_url: str
    tier: str


class RoutingEngine:
    def __init__(self, cfg: FlexConfig) -> None:
        self._cfg = cfg
        self._penalties = PenaltyBox(cfg.penalty_base_seconds, cfg.penalty_max_seconds)
        self._budget = DailyBudget(cfg.provider_budget)
        self._windows: dict[str, SlidingWindow] = {}
        self._key_counters: dict[str, int] = {}
        # session_id -> (provider, model, last_used: float)
        self._sessions: dict[str, tuple[str, str, float]] = {}
        self._session_ttl = cfg.session_ttl_minutes * 60

        for tier_models in cfg.tiers.values():
            for m in tier_models:
                k = f"{m.provider}/{m.model}"
                if k not in self._windows:
                    self._windows[k] = SlidingWindow(cfg.window_seconds)

    def update_config(self, cfg: FlexConfig) -> None:
        """Hot-reload: update config, add new windows, preserve existing state."""
        self._cfg = cfg
        self._budget = DailyBudget(cfg.provider_budget)
        self._session_ttl = cfg.session_ttl_minutes * 60
        for tier_models in cfg.tiers.values():
            for m in tier_models:
                k = f"{m.provider}/{m.model}"
                if k not in self._windows:
                    self._windows[k] = SlidingWindow(cfg.window_seconds)

    def select(
        self,
        tier: str,
        estimated_tokens: int,
        vision: bool,
        session_id: Optional[str] = None,
    ) -> Optional[RouteResult]:
        models = self._cfg.tiers[tier]  # raises KeyError for unknown tier

        # Session stickiness
        if session_id:
            result = self._try_session(session_id, tier, estimated_tokens, vision)
            if result:
                return result

        candidates = self._score_candidates(models, estimated_tokens, vision)
        if not candidates:
            return None

        result = self._pick(candidates, tier)

        if session_id:
            self._sessions[session_id] = (result.provider, result.model, time.monotonic())

        return result

    def record_request(self, provider: str, model: str, tokens: int) -> None:
        k = f"{provider}/{model}"
        if k in self._windows:
            self._windows[k].record(tokens)

    def record_cost(self, provider: str, cost_usd: float) -> None:
        self._budget.record(provider, cost_usd)

    def penalize(self, provider: str, model: str) -> None:
        self._penalties.penalize(provider, model)

    def penalize_short(self, provider: str, model: str, seconds: int = 30) -> None:
        self._penalties.penalize_short(provider, model, seconds)

    def seconds_until_available(self, tier: str) -> float:
        models = self._cfg.tiers.get(tier, [])
        min_wait = float("inf")
        for m in models:
            if self._penalties.is_penalized(m.provider, m.model):
                until = self._penalties.penalty_until(m.provider, m.model)
                if until:
                    min_wait = min(min_wait, until - time.monotonic())
            else:
                w = self._windows.get(f"{m.provider}/{m.model}")
                if w:
                    secs = w.seconds_until_available(m.rpm, m.tpm)
                    min_wait = min(min_wait, secs)
        return max(0.0, min_wait) if min_wait != float("inf") else 0.0

    # --- internals ---

    def _try_session(
        self, session_id: str, tier: str, estimated_tokens: int, vision: bool
    ) -> Optional[RouteResult]:
        entry = self._sessions.get(session_id)
        if not entry:
            return None
        provider, model, last_used = entry
        # Expire stale sessions
        if time.monotonic() - last_used > self._session_ttl:
            del self._sessions[session_id]
            return None
        # Check pinned model is still available
        models = self._cfg.tiers.get(tier, [])
        pinned = next((m for m in models if m.provider == provider and m.model == model), None)
        if not pinned:
            return None
        if not self._model_available(pinned, estimated_tokens, vision, emit_warning=False):
            return None  # fall through to normal selection
        self._sessions[session_id] = (provider, model, time.monotonic())
        return self._make_result(pinned, tier)

    def _score_candidates(
        self, models: list[ModelConfig], estimated_tokens: int, vision: bool
    ) -> list[tuple[int, ModelConfig]]:
        ctx_skipped = False
        scored = []
        for m in models:
            if vision and not m.vision:
                continue
            if self._penalties.is_penalized(m.provider, m.model):
                continue
            if not self._budget.is_available(m.provider):
                continue
            if estimated_tokens > 0 and estimated_tokens >= m.context_window:
                ctx_skipped = True
                continue
            w = self._windows.get(f"{m.provider}/{m.model}")
            if w and not w.available(m.rpm, m.tpm):
                continue
            scored.append((m.score, m))

        if ctx_skipped:
            warnings.warn(
                "Some models skipped: estimated token count exceeds their context window.",
                ContextWindowWarning,
                stacklevel=4,
            )

        return scored

    def _pick(self, scored: list[tuple[int, ModelConfig]], tier: str) -> RouteResult:
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score = scored[0][0]
        threshold = best_score * 0.8
        top = [m for score, m in scored if score >= threshold]
        chosen = random.choice(top)
        return self._make_result(chosen, tier)

    def _make_result(self, m: ModelConfig, tier: str) -> RouteResult:
        provider_cfg = self._cfg.providers[m.provider]
        counter = self._key_counters.get(m.provider, 0)
        api_key = provider_cfg.api_keys[counter % len(provider_cfg.api_keys)]
        self._key_counters[m.provider] = counter + 1
        return RouteResult(
            provider=m.provider,
            model=m.model,
            api_key=api_key,
            base_url=provider_cfg.base_url,
            tier=tier,
        )

    def _model_available(
        self, m: ModelConfig, estimated_tokens: int, vision: bool, emit_warning: bool
    ) -> bool:
        if vision and not m.vision:
            return False
        if self._penalties.is_penalized(m.provider, m.model):
            return False
        if not self._budget.is_available(m.provider):
            return False
        if estimated_tokens > 0 and estimated_tokens >= m.context_window:
            return False
        w = self._windows.get(f"{m.provider}/{m.model}")
        if w and not w.available(m.rpm, m.tpm):
            return False
        return True
```

- [ ] **Step 4: Run — expect all pass**

```bash
pytest tests/test_engine.py -v
```

Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add flexrouter/engine.py tests/test_engine.py
git commit -m "feat: routing engine with tier selection, scoring, session stickiness"
```

---

## Task 8: HTTP Client

**Files:**
- Create: `flexrouter/client.py`
- Create: `tests/test_client.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_client.py
import pytest
import respx
import httpx
from flexrouter.client import AsyncClient
from flexrouter.engine import RouteResult

ROUTE = RouteResult(
    provider="groq",
    model="llama-8b",
    api_key="test-key",
    base_url="https://api.groq.com/openai/v1",
    tier="low",
)

MESSAGES = [{"role": "user", "content": "hello"}]

OK_RESPONSE = {
    "choices": [{"message": {"role": "assistant", "content": "hi"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}

@pytest.mark.asyncio
@respx.mock
async def test_successful_call_returns_dict():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE)
    )
    client = AsyncClient()
    result = await client.chat(ROUTE, MESSAGES)
    assert result["choices"][0]["message"]["content"] == "hi"

@pytest.mark.asyncio
@respx.mock
async def test_returns_tokens_used():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE)
    )
    client = AsyncClient()
    result = await client.chat(ROUTE, MESSAGES)
    assert result["usage"]["total_tokens"] == 8

@pytest.mark.asyncio
@respx.mock
async def test_429_raises_rate_limit_error():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": {"message": "rate limited"}})
    )
    client = AsyncClient()
    from flexrouter.client import RateLimitError
    with pytest.raises(RateLimitError):
        await client.chat(ROUTE, MESSAGES)

@pytest.mark.asyncio
@respx.mock
async def test_401_raises_router_error():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "unauthorized"}})
    )
    client = AsyncClient()
    from flexrouter.exceptions import RouterError
    with pytest.raises(RouterError, match="Auth failure"):
        await client.chat(ROUTE, MESSAGES)

@pytest.mark.asyncio
@respx.mock
async def test_500_raises_provider_error():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": {"message": "server error"}})
    )
    client = AsyncClient()
    from flexrouter.client import ProviderError
    with pytest.raises(ProviderError):
        await client.chat(ROUTE, MESSAGES)

@pytest.mark.asyncio
@respx.mock
async def test_passes_extra_kwargs():
    captured = {}
    def capture(request, *args, **kwargs):
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=OK_RESPONSE)
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=capture)
    client = AsyncClient()
    await client.chat(ROUTE, MESSAGES, max_tokens=512, temperature=0.2)
    assert captured["max_tokens"] == 512
    assert captured["temperature"] == 0.2
```

- [ ] **Step 2: Run — expect failures**

```bash
pytest tests/test_client.py -v
```

Expected: `ImportError: cannot import name 'AsyncClient'`

- [ ] **Step 3: Implement `flexrouter/client.py`**

```python
from __future__ import annotations
import httpx
from flexrouter.engine import RouteResult
from flexrouter.exceptions import RouterError


class RateLimitError(Exception):
    pass

class ProviderError(Exception):
    pass


class AsyncClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=60.0)

    async def chat(
        self,
        route: RouteResult,
        messages: list[dict],
        **kwargs,
    ) -> dict:
        url = f"{route.base_url.rstrip('/')}/chat/completions"
        payload = {"model": route.model, "messages": messages, **kwargs}
        headers = {"Authorization": f"Bearer {route.api_key}"}

        resp = await self._client.post(url, json=payload, headers=headers)

        if resp.status_code == 429:
            raise RateLimitError(f"429 from {route.provider}/{route.model}")
        if resp.status_code in (401, 403):
            raise RouterError(f"Auth failure for provider {route.provider!r}: {resp.status_code}")
        if resp.status_code >= 500:
            raise ProviderError(f"{resp.status_code} from {route.provider}/{route.model}")
        if resp.status_code >= 400:
            raise ProviderError(f"{resp.status_code} from {route.provider}: {resp.text[:200]}")

        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 4: Run — expect all pass**

```bash
pytest tests/test_client.py -v
```

Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add flexrouter/client.py tests/test_client.py
git commit -m "feat: async HTTP client with OpenAI-compatible dispatch"
```

---

## Task 9: Audit Logger

**Files:**
- Create: `flexrouter/audit.py`
- Create: `tests/test_audit.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_audit.py
import csv, json, pytest
from pathlib import Path
from flexrouter.audit import AuditLogger

def test_creates_state_dir(tmp_path):
    state = tmp_path / ".flexrouter"
    logger = AuditLogger(state_dir=str(state))
    logger.log(tier="low", provider="groq", model="llama", prompt_tokens=10,
               completion_tokens=5, cost_usd=0.001, latency_ms=300, status="ok")
    assert state.exists()

def test_appends_csv_row(tmp_path):
    logger = AuditLogger(str(tmp_path))
    logger.log("low", "groq", "llama", 10, 5, 0.001, 300, "ok")
    logger.log("low", "groq", "llama", 20, 10, 0.002, 400, "ok")
    rows = list(csv.DictReader((tmp_path / "audit.csv").open()))
    assert len(rows) == 2
    assert rows[0]["provider"] == "groq"
    assert rows[1]["prompt_tokens"] == "20"

def test_csv_has_correct_headers(tmp_path):
    logger = AuditLogger(str(tmp_path))
    logger.log("low", "groq", "llama", 0, 0, 0, 0, "ok")
    headers = list(csv.DictReader((tmp_path / "audit.csv").open()).fieldnames)
    assert headers == ["timestamp", "tier", "provider", "model",
                       "prompt_tokens", "completion_tokens", "cost_usd", "latency_ms", "status"]

def test_health_json_updated(tmp_path):
    logger = AuditLogger(str(tmp_path))
    logger.log("low", "groq", "llama", 10, 5, 0.005, 300, "ok")
    health = json.loads((tmp_path / "health.json").read_text())
    assert health["total_cost_usd"] == pytest.approx(0.005)
    assert "groq" in health["providers"]

def test_health_json_accumulates_cost(tmp_path):
    logger = AuditLogger(str(tmp_path))
    logger.log("low", "groq", "llama", 10, 5, 0.005, 300, "ok")
    logger.log("low", "openai", "gpt-4o", 100, 50, 0.020, 1200, "ok")
    health = json.loads((tmp_path / "health.json").read_text())
    assert health["total_cost_usd"] == pytest.approx(0.025)

def test_last_50_logs(tmp_path):
    logger = AuditLogger(str(tmp_path))
    health = json.loads((tmp_path / "health.json").read_text()) if (tmp_path / "health.json").exists() else {}
    for i in range(60):
        logger.log("low", "groq", "llama", i, 0, 0, 100, "ok")
    entries = logger.recent_logs(50)
    assert len(entries) == 50
```

- [ ] **Step 2: Run — expect failures**

```bash
pytest tests/test_audit.py -v
```

Expected: `ImportError: cannot import name 'AuditLogger'`

- [ ] **Step 3: Implement `flexrouter/audit.py`**

```python
from __future__ import annotations
import csv
import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


_HEADERS = ["timestamp", "tier", "provider", "model",
            "prompt_tokens", "completion_tokens", "cost_usd", "latency_ms", "status"]


class AuditLogger:
    def __init__(self, state_dir: str) -> None:
        self._dir = Path(state_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._csv_path = self._dir / "audit.csv"
        self._health_path = self._dir / "health.json"
        self._recent: deque[dict] = deque(maxlen=500)
        self._total_cost: float = 0.0
        self._provider_cost: dict[str, float] = {}
        self._session_start = datetime.now(timezone.utc).isoformat()

        # Write CSV header if new file
        if not self._csv_path.exists():
            with self._csv_path.open("w", newline="") as f:
                csv.DictWriter(f, fieldnames=_HEADERS).writeheader()

    def log(
        self,
        tier: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        latency_ms: int,
        status: str,
    ) -> None:
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tier": tier,
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            "status": status,
        }
        with self._csv_path.open("a", newline="") as f:
            csv.DictWriter(f, fieldnames=_HEADERS).writerow(row)

        self._recent.append(row)
        self._total_cost += cost_usd
        self._provider_cost[provider] = self._provider_cost.get(provider, 0.0) + cost_usd
        self._write_health()

    def recent_logs(self, n: int = 50) -> list[dict]:
        entries = list(self._recent)
        return entries[-n:]

    def snapshot(self) -> dict:
        return {
            "total_cost_usd": round(self._total_cost, 6),
            "session_start": self._session_start,
            "providers": {
                p: {"daily_cost_usd": round(c, 6)}
                for p, c in self._provider_cost.items()
            },
        }

    def _write_health(self) -> None:
        self._health_path.write_text(json.dumps(self.snapshot(), indent=2))
```

- [ ] **Step 4: Run — expect all pass**

```bash
pytest tests/test_audit.py -v
```

Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add flexrouter/audit.py tests/test_audit.py
git commit -m "feat: audit logger — CSV append and health.json"
```

---

## Task 10: FlexRouter Integration

**Files:**
- Create: `flexrouter/_router.py`
- Create: `tests/test_flexrouter.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_flexrouter.py
import pytest, respx, httpx, warnings
from flexrouter import FlexRouter, RouterBusy, RouterError, ContextWindowWarning

OK_RESPONSE = {
    "choices": [{"message": {"role": "assistant", "content": "hello"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}

@respx.mock
def test_generate_returns_response(config_file):
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE)
    )
    router = FlexRouter(str(config_file))
    result = router.generate([{"role": "user", "content": "hi"}], tier="low")
    assert result["choices"][0]["message"]["content"] == "hello"

@respx.mock
def test_generate_raises_router_busy_when_no_wait(config_file):
    router = FlexRouter(str(config_file))
    # Exhaust the single model
    router._engine.penalize("groq", "llama-3.1-8b-instant")
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)

@respx.mock
def test_agenerate_works(config_file):
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE)
    )
    import asyncio
    router = FlexRouter(str(config_file))
    result = asyncio.run(router.agenerate([{"role": "user", "content": "hi"}], tier="low"))
    assert "choices" in result

@respx.mock
def test_generate_penalizes_on_429(config_file):
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": {"message": "rate limited"}})
    )
    router = FlexRouter(str(config_file))
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)
    assert router._engine._penalties.is_penalized("groq", "llama-3.1-8b-instant")

@respx.mock
def test_reload_reloads_config(config_file):
    router = FlexRouter(str(config_file))
    router.reload()  # should not raise

@respx.mock
def test_unknown_tier_raises_key_error(config_file):
    router = FlexRouter(str(config_file))
    with pytest.raises(KeyError):
        router.generate([], tier="nuclear", wait=False)
```

- [ ] **Step 2: Run — expect failures**

```bash
pytest tests/test_flexrouter.py -v
```

Expected: `ImportError` or similar

- [ ] **Step 3: Implement `flexrouter/_router.py`**

```python
from __future__ import annotations
import asyncio
import time
import warnings
from pathlib import Path
from typing import Optional

from flexrouter.audit import AuditLogger
from flexrouter.client import AsyncClient, RateLimitError, ProviderError
from flexrouter.config import FlexConfig, load_config, discover_config
from flexrouter.engine import RoutingEngine
from flexrouter.exceptions import ConfigError, RouterBusy, RouterError
from flexrouter.hooks import HookRunner, HookContext


class FlexRouter:
    def __init__(self, config_path: Optional[str] = None) -> None:
        if config_path:
            path = Path(config_path)
        else:
            path = discover_config()
        if not path or not path.exists():
            raise ConfigError("No flexrouter.yaml found. Pass path or create ./flexrouter.yaml")

        self._config_path = path
        self._cfg: FlexConfig = load_config(path)
        self._engine = RoutingEngine(self._cfg)
        self._audit = AuditLogger(self._cfg.state_dir)
        self._client = AsyncClient()
        self._hooks = HookRunner()
        self._loop = asyncio.new_event_loop()

    def generate(
        self,
        messages: list[dict],
        tier: str,
        wait: bool = True,
        vision: bool = False,
        session_id: Optional[str] = None,
        **kwargs,
    ) -> dict:
        return self._loop.run_until_complete(
            self.agenerate(messages, tier, wait=wait, vision=vision, session_id=session_id, **kwargs)
        )

    async def agenerate(
        self,
        messages: list[dict],
        tier: str,
        wait: bool = True,
        vision: bool = False,
        session_id: Optional[str] = None,
        **kwargs,
    ) -> dict:
        self._maybe_hot_reload()

        # Run hooks
        ctx = HookContext(messages=messages, hooks=self._cfg.hooks, vision=vision)
        ctx = self._hooks.run(ctx)
        vision = ctx.vision
        estimated_tokens = ctx.estimated_tokens

        retries = self._cfg.retry.retries
        backoff = self._cfg.retry.backoff_seconds

        for attempt in range(retries + 1):
            route = self._engine.select(tier, estimated_tokens, vision, session_id)

            if route is None:
                if not wait:
                    raise RouterBusy(f"All models in tier {tier!r} are unavailable")
                secs = self._engine.seconds_until_available(tier)
                await asyncio.sleep(max(secs, 1.0))
                continue

            start = time.monotonic()
            try:
                result = await self._client.chat(route, messages, **kwargs)
            except RateLimitError:
                self._engine.penalize(route.provider, route.model)
                if attempt == retries:
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue
            except RouterError:
                raise
            except ProviderError:
                self._engine.penalize(route.provider, route.model)
                if attempt == retries:
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue

            latency_ms = int((time.monotonic() - start) * 1000)
            usage = result.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)

            self._engine.record_request(route.provider, route.model, total_tokens)
            self._audit.log(
                tier=tier,
                provider=route.provider,
                model=route.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=0.0,  # cost tracking added in future task
                latency_ms=latency_ms,
                status="ok",
            )
            return result

        raise RouterBusy(f"All models in tier {tier!r} are unavailable after {retries} retries")

    def reload(self) -> None:
        self._cfg = load_config(self._config_path)
        self._engine.update_config(self._cfg)
        self._audit = AuditLogger(self._cfg.state_dir)

    def _maybe_hot_reload(self) -> None:
        try:
            mtime = self._config_path.stat().st_mtime
            if not hasattr(self, "_last_mtime"):
                self._last_mtime = mtime
            elif mtime != self._last_mtime:
                self._last_mtime = mtime
                self.reload()
        except OSError:
            pass
```

- [ ] **Step 4: Run — expect all pass**

```bash
pytest tests/test_flexrouter.py -v
```

Expected: `6 passed`

- [ ] **Step 5: Run full test suite**

```bash
pytest -v
```

Expected: all tests from Tasks 1-10 passing (40+ tests)

- [ ] **Step 6: Commit**

```bash
git add flexrouter/_router.py flexrouter/__init__.py tests/test_flexrouter.py
git commit -m "feat: FlexRouter integration — generate(), agenerate(), hot-reload"
```

---

## Task 11: CLI

**Files:**
- Create: `flexrouter/cli.py`

- [ ] **Step 1: Implement `flexrouter/cli.py`**

```python
import base64
import json
import webbrowser
import click
import yaml
from pathlib import Path

from flexrouter.config import discover_config, load_config
from flexrouter.exceptions import ConfigError


@click.group()
def cli():
    """flexrouter — universal LLM router."""


@cli.command()
def init():
    """Open browser setup wizard to generate flexrouter.yaml."""
    from flexrouter.dashboard.server import start_server
    port = 7352
    click.echo(f"Starting setup wizard at http://localhost:{port}/#setup")
    webbrowser.open(f"http://localhost:{port}/#setup")
    start_server(port=port, open_tab=False)


@cli.command()
def dashboard():
    """Start the live dashboard at http://localhost:<port>."""
    path = discover_config()
    port = 7352
    if path:
        try:
            cfg = load_config(path)
            port = cfg.dashboard_port
        except ConfigError:
            pass
    from flexrouter.dashboard.server import start_server
    click.echo(f"Dashboard running at http://localhost:{port}")
    webbrowser.open(f"http://localhost:{port}")
    start_server(port=port, open_tab=False)


@cli.command()
def status():
    """Print tier health to terminal."""
    path = discover_config()
    if not path:
        click.echo("No flexrouter.yaml found.", err=True)
        raise SystemExit(1)
    cfg = load_config(path)
    state = Path(cfg.state_dir) / "health.json"
    if not state.exists():
        click.echo("No health data yet. Run router.generate() first.")
        return
    health = json.loads(state.read_text())
    click.echo(f"Total cost: ${health['total_cost_usd']:.4f}")
    for provider, info in health.get("providers", {}).items():
        click.echo(f"  {provider}: ${info['daily_cost_usd']:.4f} today")


@cli.group()
def config():
    """Manage flexrouter configuration."""


@config.command("export")
def config_export():
    """Export config as a base64 token."""
    path = discover_config()
    if not path:
        click.echo("No flexrouter.yaml found.", err=True)
        raise SystemExit(1)
    token = base64.b64encode(path.read_bytes()).decode()
    click.echo(token)


@config.command("import")
@click.argument("token")
def config_import(token: str):
    """Import config from a base64 token."""
    data = base64.b64decode(token.encode())
    out = Path("flexrouter.yaml")
    out.write_bytes(data)
    click.echo(f"Config written to {out}")
```

- [ ] **Step 2: Verify CLI is wired**

```bash
flexrouter --help
```

Expected output:
```
Usage: flexrouter [OPTIONS] COMMAND [ARGS]...

  flexrouter — universal LLM router.

Commands:
  config     Manage flexrouter configuration.
  dashboard  Start the live dashboard at http://localhost:<port>.
  init       Open browser setup wizard to generate flexrouter.yaml.
  status     Print tier health to terminal.
```

- [ ] **Step 3: Commit**

```bash
git add flexrouter/cli.py
git commit -m "feat: CLI — init, dashboard, status, config export/import"
```

---

## Task 12: Dashboard Backend

**Files:**
- Create: `flexrouter/dashboard/__init__.py`
- Create: `flexrouter/dashboard/api.py`
- Create: `flexrouter/dashboard/server.py`

- [ ] **Step 1: Create `flexrouter/dashboard/__init__.py`**

```python
```

(empty)

- [ ] **Step 2: Implement `flexrouter/dashboard/api.py`**

```python
from __future__ import annotations
import json
from pathlib import Path
from flexrouter.config import discover_config, load_config
from flexrouter.exceptions import ConfigError


def get_status(state_dir: str) -> dict:
    health_path = Path(state_dir) / "health.json"
    if health_path.exists():
        return json.loads(health_path.read_text())
    return {"total_cost_usd": 0.0, "session_start": None, "providers": {}, "models": {}}


def get_logs(state_dir: str, n: int = 50) -> list[dict]:
    import csv
    csv_path = Path(state_dir) / "audit.csv"
    if not csv_path.exists():
        return []
    rows = list(csv.DictReader(csv_path.open()))
    return rows[-n:]


def get_config() -> dict:
    path = discover_config()
    if not path:
        return {}
    import yaml
    return yaml.safe_load(path.read_text()) or {}


def post_config(raw: dict) -> None:
    path = discover_config() or Path("flexrouter.yaml")
    import yaml
    path.write_text(yaml.dump(raw, default_flow_style=False))
```

- [ ] **Step 3: Implement `flexrouter/dashboard/server.py`**

```python
from __future__ import annotations
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from flexrouter.config import discover_config, load_config
from flexrouter.dashboard.api import get_config, get_logs, get_status, post_config

STATIC_DIR = Path(__file__).parent / "static"


def _state_dir() -> str:
    path = discover_config()
    if path:
        try:
            return load_config(path).state_dir
        except Exception:
            pass
    return ".flexrouter"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # silence default access log
        pass

    def _send_json(self, data: dict | list, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._handle_api_get()
        else:
            self._serve_static()

    def do_POST(self):
        if self.path == "/api/config":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            post_config(body)
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "not found"}, 404)

    def _handle_api_get(self):
        state = _state_dir()
        if self.path == "/api/status":
            self._send_json(get_status(state))
        elif self.path.startswith("/api/logs"):
            self._send_json(get_logs(state))
        elif self.path == "/api/config":
            self._send_json(get_config())
        else:
            self._send_json({"error": "not found"}, 404)

    def _serve_static(self):
        # All non-API routes serve index.html (SPA routing)
        index = STATIC_DIR / "index.html"
        if not index.exists():
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"Dashboard not built. Run: cd dashboard/frontend && npm run build")
            return
        mime, _ = mimetypes.guess_type(str(index))
        body = index.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "text/html")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


def start_server(port: int = 7352, open_tab: bool = True) -> None:
    server = HTTPServer(("127.0.0.1", port), _Handler)
    server.serve_forever()
```

- [ ] **Step 4: Verify server starts**

```bash
python -c "from flexrouter.dashboard.server import start_server; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add flexrouter/dashboard/
git commit -m "feat: dashboard HTTP server with /api/* endpoints"
```

---

## Task 13: Dashboard Frontend

**Files:**
- Create: `dashboard/frontend/package.json`
- Create: `dashboard/frontend/vite.config.ts`
- Create: `dashboard/frontend/tailwind.config.js`
- Create: `dashboard/frontend/src/main.tsx`
- Create: `dashboard/frontend/src/App.tsx`
- Create: `dashboard/frontend/src/api.ts`
- Create: `dashboard/frontend/src/tabs/LiveTelemetry.tsx`
- Create: `dashboard/frontend/src/tabs/Chat.tsx`
- Create: `dashboard/frontend/src/tabs/RequestLogs.tsx`
- Create: `dashboard/frontend/src/tabs/AccountStatus.tsx`
- Create: `dashboard/frontend/src/tabs/Settings.tsx`
- Create: `dashboard/frontend/src/tabs/Setup.tsx`
- Create: `dashboard/frontend/src/components/RateBar.tsx`
- Create: `dashboard/frontend/src/components/StatusDot.tsx`
- Create: `dashboard/frontend/index.html`

- [ ] **Step 1: Create `dashboard/frontend/package.json`**

```json
{
  "name": "flexrouter-dashboard",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.0",
    "react-dom": "^18.3.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "autoprefixer": "^10.4.0",
    "postcss": "^8.4.0",
    "tailwindcss": "^3.4.0",
    "typescript": "^5.4.0",
    "vite": "^5.3.0"
  }
}
```

- [ ] **Step 2: Create `dashboard/frontend/vite.config.ts`**

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: path.resolve(__dirname, '../../flexrouter/dashboard/static'),
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:7352',
    },
  },
})
```

- [ ] **Step 3: Create `dashboard/frontend/tailwind.config.js`**

```javascript
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        brand: '#3b82f6',
      },
      fontFamily: {
        sans: ['Geist', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
}
```

- [ ] **Step 4: Create `dashboard/frontend/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>flexrouter</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
  </head>
  <body class="bg-gray-50 dark:bg-gray-950 text-gray-900 dark:text-gray-100">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Create `dashboard/frontend/src/api.ts`**

```typescript
const BASE = ''  // relative, proxied in dev

export async function fetchStatus() {
  const res = await fetch(`${BASE}/api/status`)
  return res.json()
}

export async function fetchLogs(n = 50) {
  const res = await fetch(`${BASE}/api/logs?n=${n}`)
  return res.json()
}

export async function fetchConfig() {
  const res = await fetch(`${BASE}/api/config`)
  return res.json()
}

export async function postConfig(cfg: object) {
  const res = await fetch(`${BASE}/api/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  })
  return res.json()
}

export async function chatCompletion(messages: object[], model: string) {
  const res = await fetch('/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, messages }),
  })
  return res.json()
}
```

- [ ] **Step 6: Create `dashboard/frontend/src/components/RateBar.tsx`**

```typescript
type Props = { current: number; limit: number; label?: string }

export function RateBar({ current, limit, label }: Props) {
  const pct = limit > 0 ? Math.min((current / limit) * 100, 100) : 0
  const color = pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-amber-400' : 'bg-blue-500'
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
      {label && <span className="text-xs text-gray-500 dark:text-gray-400 font-mono">{label}</span>}
    </div>
  )
}
```

- [ ] **Step 7: Create `dashboard/frontend/src/components/StatusDot.tsx`**

```typescript
type Status = 'up' | 'limited' | 'penalized' | 'down'
const COLORS: Record<Status, string> = {
  up: 'bg-green-500',
  limited: 'bg-amber-400',
  penalized: 'bg-red-500',
  down: 'bg-gray-400',
}
export function StatusDot({ status }: { status: Status }) {
  return <span className={`inline-block w-2 h-2 rounded-full ${COLORS[status]}`} />
}
```

- [ ] **Step 8: Create `dashboard/frontend/src/tabs/LiveTelemetry.tsx`**

```typescript
import { useEffect, useState } from 'react'
import { fetchStatus } from '../api'
import { RateBar } from '../components/RateBar'
import { StatusDot } from '../components/StatusDot'

export function LiveTelemetry() {
  const [status, setStatus] = useState<any>(null)
  const [paused, setPaused] = useState(false)
  const [search, setSearch] = useState('')

  useEffect(() => {
    const load = () => { if (!paused) fetchStatus().then(setStatus) }
    load()
    const id = setInterval(load, 2000)
    return () => clearInterval(id)
  }, [paused])

  const models: [string, any][] = Object.entries(status?.models ?? {})
  const filtered = models.filter(([key]) =>
    key.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <input
          type="text"
          placeholder="Search models..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-1.5 text-sm bg-white dark:bg-gray-800 w-64"
        />
        <button
          onClick={() => setPaused(p => !p)}
          className="text-xs px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700"
        >
          {paused ? '▶ Resume' : '⏸ Pause'}
        </button>
        <span className="text-sm text-gray-500">
          Total cost: <b>${(status?.total_cost_usd ?? 0).toFixed(4)}</b>
        </span>
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-500 uppercase border-b border-gray-100 dark:border-gray-800">
            <th className="pb-2 pr-4">Model</th>
            <th className="pb-2 pr-4">RPM</th>
            <th className="pb-2 pr-4">TPM</th>
            <th className="pb-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map(([key, info]) => {
            const penalized = !!info.penalty_until
            const dotStatus = penalized ? 'penalized' : 'up'
            return (
              <tr key={key} className="border-b border-gray-50 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800/50">
                <td className="py-2 pr-4 font-mono font-medium">{key}</td>
                <td className="py-2 pr-4">
                  <RateBar current={info.rpm_current ?? 0} limit={60} label={`${info.rpm_current ?? 0}`} />
                </td>
                <td className="py-2 pr-4">
                  <RateBar current={info.tpm_current ?? 0} limit={60000} label={`${((info.tpm_current ?? 0) / 1000).toFixed(0)}k`} />
                </td>
                <td className="py-2">
                  <div className="flex items-center gap-1.5">
                    <StatusDot status={dotStatus} />
                    <span className="text-xs capitalize">{dotStatus}</span>
                  </div>
                </td>
              </tr>
            )
          })}
          {filtered.length === 0 && (
            <tr><td colSpan={4} className="py-8 text-center text-gray-400 text-sm">No data yet. Run router.generate() first.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 9: Create `dashboard/frontend/src/tabs/Chat.tsx`**

```typescript
import { useState } from 'react'
import { chatCompletion } from '../api'

export function Chat() {
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([])
  const [input, setInput] = useState('')
  const [tier, setTier] = useState('low')
  const [loading, setLoading] = useState(false)

  const send = async () => {
    if (!input.trim() || loading) return
    const userMsg = { role: 'user', content: input }
    setMessages(m => [...m, userMsg])
    setInput('')
    setLoading(true)
    try {
      const result = await chatCompletion([...messages, userMsg], `auto-${tier}`)
      const reply = result?.choices?.[0]?.message?.content ?? '(no response)'
      setMessages(m => [...m, { role: 'assistant', content: reply }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-[60vh]">
      <div className="flex gap-2 mb-3 items-center">
        <span className="text-sm font-medium">Tier:</span>
        {['low', 'medium', 'high'].map(t => (
          <button
            key={t}
            onClick={() => setTier(t)}
            className={`px-3 py-1 rounded-lg text-sm font-medium border ${tier === t ? 'bg-blue-500 text-white border-blue-500' : 'border-gray-200 dark:border-gray-700'}`}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto border border-gray-100 dark:border-gray-800 rounded-xl p-3 space-y-3 bg-gray-50 dark:bg-gray-900">
        {messages.length === 0 && <p className="text-gray-400 text-sm text-center mt-8">Start a conversation…</p>}
        {messages.map((m, i) => (
          <div key={i} className={`p-3 rounded-lg text-sm font-mono whitespace-pre-wrap border-l-2 ${m.role === 'user' ? 'border-green-400 bg-white dark:bg-gray-800' : 'border-blue-400 bg-white dark:bg-gray-800'}`}>
            <span className="text-xs font-sans text-gray-400 block mb-1">{m.role}</span>
            {m.content}
          </div>
        ))}
        {loading && <div className="text-xs text-gray-400 animate-pulse">Thinking…</div>}
      </div>
      <div className="flex gap-2 mt-3">
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder="Type a message (Enter to send)"
          className="flex-1 border border-gray-200 dark:border-gray-700 rounded-xl px-3 py-2 text-sm resize-none bg-white dark:bg-gray-800 min-h-[60px]"
        />
        <button onClick={send} disabled={loading} className="px-4 bg-blue-500 text-white rounded-xl font-semibold text-sm disabled:opacity-50">Send</button>
      </div>
    </div>
  )
}
```

- [ ] **Step 10: Create `dashboard/frontend/src/tabs/RequestLogs.tsx`**

```typescript
import { useEffect, useState } from 'react'
import { fetchLogs } from '../api'

export function RequestLogs() {
  const [logs, setLogs] = useState<any[]>([])
  const [paused, setPaused] = useState(false)

  useEffect(() => {
    const load = () => { if (!paused) fetchLogs(50).then(setLogs) }
    load()
    const id = setInterval(load, 2000)
    return () => clearInterval(id)
  }, [paused])

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <h2 className="font-semibold">Recent Requests</h2>
        <button onClick={() => setPaused(p => !p)} className="text-xs px-3 py-1 rounded border border-gray-200 dark:border-gray-700">
          {paused ? '▶ Resume' : '⏸ Pause'}
        </button>
      </div>
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-left text-gray-400 border-b border-gray-100 dark:border-gray-800">
            {['Time', 'Tier', 'Model', 'In', 'Out', 'Cost', 'ms', 'Status'].map(h => (
              <th key={h} className="pb-2 pr-3 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[...logs].reverse().map((row, i) => (
            <tr key={i} className="border-b border-gray-50 dark:border-gray-800">
              <td className="py-1.5 pr-3 text-gray-400">{row.timestamp?.slice(11, 19)}</td>
              <td className="py-1.5 pr-3"><span className="px-1.5 py-0.5 bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded">{row.tier}</span></td>
              <td className="py-1.5 pr-3">{row.provider}/{row.model}</td>
              <td className="py-1.5 pr-3">{row.prompt_tokens}</td>
              <td className="py-1.5 pr-3">{row.completion_tokens}</td>
              <td className="py-1.5 pr-3">${Number(row.cost_usd).toFixed(5)}</td>
              <td className="py-1.5 pr-3">{row.latency_ms}</td>
              <td className="py-1.5"><span className={row.status === 'ok' ? 'text-green-500' : 'text-red-500'}>{row.status}</span></td>
            </tr>
          ))}
          {logs.length === 0 && <tr><td colSpan={8} className="py-8 text-center text-gray-400">No requests yet.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 11: Create remaining tabs (AccountStatus, Settings, Setup)**

```typescript
// dashboard/frontend/src/tabs/AccountStatus.tsx
import { useEffect, useState } from 'react'
import { fetchStatus } from '../api'

export function AccountStatus() {
  const [status, setStatus] = useState<any>(null)
  useEffect(() => { fetchStatus().then(setStatus) }, [])
  const providers = status?.providers ?? {}
  return (
    <div className="space-y-4">
      <h2 className="font-semibold">Provider Accounts</h2>
      {Object.entries(providers).map(([name, info]: any) => (
        <div key={name} className="border border-gray-100 dark:border-gray-800 rounded-xl p-4">
          <div className="flex justify-between items-center">
            <span className="font-medium">{name}</span>
            <span className="text-sm text-gray-500">${info.daily_cost_usd?.toFixed(4) ?? '0.0000'} today</span>
          </div>
          {info.budget_usd && (
            <div className="mt-2 text-xs text-gray-400">Budget: ${info.budget_usd}/day</div>
          )}
        </div>
      ))}
      {Object.keys(providers).length === 0 && <p className="text-gray-400 text-sm">No provider data yet.</p>}
    </div>
  )
}
```

```typescript
// dashboard/frontend/src/tabs/Settings.tsx
import { useEffect, useState } from 'react'
import { fetchConfig, postConfig } from '../api'

export function Settings() {
  const [cfg, setCfg] = useState<string>('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    fetchConfig().then(c => setCfg(JSON.stringify(c, null, 2)))
  }, [])

  const save = async () => {
    try {
      await postConfig(JSON.parse(cfg))
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch {
      alert('Invalid JSON')
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="font-semibold">Configuration</h2>
        <button onClick={save} className="px-4 py-1.5 bg-blue-500 text-white rounded-lg text-sm font-medium">
          {saved ? '✓ Saved' : 'Save'}
        </button>
      </div>
      <textarea
        value={cfg}
        onChange={e => setCfg(e.target.value)}
        className="w-full h-96 font-mono text-xs border border-gray-200 dark:border-gray-700 rounded-xl p-3 bg-white dark:bg-gray-900"
      />
    </div>
  )
}
```

```typescript
// dashboard/frontend/src/tabs/Setup.tsx
export function Setup() {
  return (
    <div className="max-w-lg space-y-6">
      <h2 className="font-semibold text-lg">Setup</h2>
      <p className="text-gray-500 text-sm">Create a <code className="bg-gray-100 dark:bg-gray-800 px-1 rounded">flexrouter.yaml</code> in your project directory:</p>
      <pre className="bg-gray-900 text-green-300 p-4 rounded-xl text-xs overflow-x-auto">{`tiers:
  low:
    - provider: groq
      model: llama-3.1-8b-instant
      score: 85
      rpm: 60
      tpm: 60000
      context_window: 131072

providers:
  groq:
    base_url: https://api.groq.com/openai/v1
    api_keys:
      - env: GROQ_API_KEY

settings:
  state_dir: .flexrouter/
  retry_policy: balanced`}</pre>
      <p className="text-sm text-gray-500">Then set your environment variables and run:</p>
      <pre className="bg-gray-900 text-green-300 p-4 rounded-xl text-xs">{`from flexrouter import FlexRouter
router = FlexRouter()
response = router.generate(
    messages=[{"role": "user", "content": "hello"}],
    tier="low",
)
print(response["choices"][0]["message"]["content"])`}</pre>
    </div>
  )
}
```

- [ ] **Step 12: Create `dashboard/frontend/src/App.tsx`**

```typescript
import { useState } from 'react'
import { LiveTelemetry } from './tabs/LiveTelemetry'
import { Chat } from './tabs/Chat'
import { RequestLogs } from './tabs/RequestLogs'
import { AccountStatus } from './tabs/AccountStatus'
import { Settings } from './tabs/Settings'
import { Setup } from './tabs/Setup'

const TABS = [
  { id: 'telemetry', label: 'Live Telemetry', component: LiveTelemetry },
  { id: 'chat', label: 'Chat', component: Chat },
  { id: 'logs', label: 'Request Logs', component: RequestLogs },
  { id: 'accounts', label: 'Account Status', component: AccountStatus },
  { id: 'settings', label: 'Settings', component: Settings },
  { id: 'setup', label: 'Setup', component: Setup },
]

export default function App() {
  const [activeTab, setActiveTab] = useState(
    window.location.hash === '#setup' ? 'setup' : 'telemetry'
  )
  const [dark, setDark] = useState(false)
  const Tab = TABS.find(t => t.id === activeTab)?.component ?? LiveTelemetry

  return (
    <div className={dark ? 'dark' : ''}>
      <div className="min-h-screen bg-gray-50 dark:bg-gray-950 text-gray-900 dark:text-gray-100">
        <div className="max-w-6xl mx-auto px-6 py-10">
          {/* Header */}
          <div className="flex justify-between items-start mb-8">
            <div>
              <p className="text-xs font-semibold uppercase tracking-widest text-blue-500 mb-1">Router Control Center</p>
              <h1 className="text-3xl font-bold">flexrouter</h1>
              <p className="text-gray-500 text-sm mt-1">Live model telemetry, provider health, and routing controls.</p>
            </div>
            <button onClick={() => setDark(d => !d)} className="text-xl p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800">
              {dark ? '☀️' : '🌙'}
            </button>
          </div>

          {/* Tabs */}
          <div className="flex gap-1 border-b border-gray-200 dark:border-gray-800 mb-6">
            {TABS.map(t => (
              <button
                key={t.id}
                onClick={() => setActiveTab(t.id)}
                className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === t.id
                    ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                    : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {/* Content */}
          <Tab />
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 13: Create `dashboard/frontend/src/main.tsx`**

```typescript
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
```

- [ ] **Step 14: Create `dashboard/frontend/src/index.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 15: Install dependencies and build**

```bash
cd dashboard/frontend
npm install
npm run build
```

Expected: `flexrouter/dashboard/static/index.html` created (and JS/CSS assets alongside it).

- [ ] **Step 16: Verify dashboard serves**

```bash
# In one terminal:
python -c "
from flexrouter.dashboard.server import start_server
import threading, webbrowser, time
threading.Timer(0.5, lambda: webbrowser.open('http://localhost:7352')).start()
start_server(7352)
"
```

Expected: browser opens, dashboard loads with tabs visible.

- [ ] **Step 17: Commit**

```bash
cd ../..
git add dashboard/frontend/ flexrouter/dashboard/static/
git commit -m "feat: React dashboard with Live Telemetry, Chat, Logs, Settings tabs"
```

---

## Task 14: GitHub Actions + PyPI Publish

**Files:**
- Create: `.github/workflows/publish.yml`

- [ ] **Step 1: Create `.github/workflows/publish.yml`**

```yaml
name: Publish to PyPI

on:
  push:
    tags:
      - 'v*.*.*'

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install build tools
        run: pip install build

      - name: Build package
        run: python -m build

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
```

- [ ] **Step 2: Add PyPI trusted publisher**

In PyPI project settings → Publishing → Add a trusted publisher:
- Owner: `<your GitHub username>`
- Repository: `router`
- Workflow: `publish.yml`
- Environment: (leave blank)

- [ ] **Step 3: Tag and release**

```bash
git tag v0.1.0
git push origin main --tags
```

Expected: GitHub Actions runs, package published to PyPI.

- [ ] **Step 4: Verify install**

```bash
pip install flexrouter
flexrouter --help
```

Expected: CLI works from fresh install.

- [ ] **Step 5: Final commit**

```bash
git add .github/
git commit -m "ci: GitHub Actions publish to PyPI on tag"
```

---

## Self-Review Against Spec

| Spec requirement | Task |
|---|---|
| Named tiers, strict isolation | Task 7 (engine) |
| 1-100 score within tier | Task 7 |
| Top-20% random pick (thundering herd) | Task 7 `_pick()` |
| No cross-tier fallback | Task 7 — unknown tier raises KeyError |
| Within-tier context window fallback + warning | Task 7 `_score_candidates()` |
| Hot-reload on mtime change | Task 10 `_maybe_hot_reload()` |
| Multi-key round-robin | Task 7 `_make_result()` |
| Session stickiness + TTL | Task 7 |
| Penalty box (30s → 1800s) | Task 3 |
| Daily budget per provider | Task 4 |
| detect_vision hook | Task 6 |
| estimate_tokens hook | Task 6 |
| Retry presets (conservative/balanced/aggressive) | Task 5 |
| Manual retry override | Task 5 |
| `wait=True/False` | Task 10 |
| `RouterBusy`, `RouterError`, `ContextWindowWarning` | Task 1 |
| Config auto-discovery (CWD → home) | Task 5 `discover_config()` |
| Audit CSV + health.json | Task 9 |
| Dashboard — 6 tabs | Task 13 |
| Dashboard — /api/status, /api/logs, /api/config | Task 12 |
| CLI — init, dashboard, status, config export/import | Task 11 |
| `flexrouter init` opens browser to setup tab | Task 11 |
| PyPI publish on tag | Task 14 |
| Python 3.11 minimum | Task 1 `pyproject.toml` |
| Pre-built frontend committed | Task 13 step 17 |
| `.flexrouter/` in .gitignore | Task 1 |
