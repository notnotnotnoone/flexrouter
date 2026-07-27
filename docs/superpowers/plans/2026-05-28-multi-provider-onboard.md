# Multi-Provider Onboarding Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `flexrouter init` to discover free models from 8 providers, score them via the Artificial Analysis API, and learn real rate limits from live traffic headers.

**Architecture:** Sequential wizard collects API keys per provider, pings `/v1/models` to discover free models, calls AA API for quality scores, then writes a complete `flexrouter.yaml`. A new `RateLimitStore` persists learned rpm/tpm from response headers on every live call, overriding yaml defaults in the routing engine.

**Tech Stack:** Python 3.11+, httpx (async HTTP), click (CLI), pyyaml, pytest + respx (mock HTTP in tests)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `flexrouter/rate_limits.py` | **Create** | Persist learned rpm/tpm to `.flexrouter/rate_limits.json` |
| `flexrouter/client.py` | **Modify** | Parse `x-ratelimit-*` headers after each response, call store |
| `flexrouter/engine.py` | **Modify** | Use learned limits for windowing; allow empty api_keys (Ollama) |
| `flexrouter/_router.py` | **Modify** | Wire `RateLimitStore` into client and engine |
| `flexrouter/onboard.py` | **Rewrite** | Provider registry, async discovery, AA scoring, wizard loop, yaml gen |
| `tests/test_rate_limits.py` | **Create** | Unit tests for RateLimitStore |
| `tests/test_client.py` | **Modify** | Tests for rate limit header extraction |
| `tests/test_engine.py` | **Modify** | Tests for learned limits + empty api_keys |
| `tests/test_onboard.py` | **Create** | Tests for discovery, scoring, yaml generation |

---

## Task 1: RateLimitStore

**Files:**
- Create: `flexrouter/rate_limits.py`
- Create: `tests/test_rate_limits.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_rate_limits.py
import json
import pytest
from flexrouter.rate_limits import RateLimitStore


def test_get_returns_default_when_empty(tmp_path):
    store = RateLimitStore(str(tmp_path))
    assert store.get_rpm("groq", "llama-8b", default=30) == 30
    assert store.get_tpm("groq", "llama-8b", default=6000) == 6000


def test_update_persists_to_disk(tmp_path):
    store = RateLimitStore(str(tmp_path))
    store.update("groq", "llama-8b", rpm=60, tpm=12000)
    data = json.loads((tmp_path / "rate_limits.json").read_text())
    assert data["groq/llama-8b"]["rpm"] == 60
    assert data["groq/llama-8b"]["tpm"] == 12000


def test_get_returns_learned_value(tmp_path):
    store = RateLimitStore(str(tmp_path))
    store.update("groq", "llama-8b", rpm=60, tpm=12000)
    assert store.get_rpm("groq", "llama-8b", default=30) == 60
    assert store.get_tpm("groq", "llama-8b", default=6000) == 12000


def test_loads_existing_data_on_init(tmp_path):
    (tmp_path / "rate_limits.json").write_text(
        json.dumps({"groq/llama-8b": {"rpm": 45, "tpm": 9000}})
    )
    store = RateLimitStore(str(tmp_path))
    assert store.get_rpm("groq", "llama-8b", default=30) == 45


def test_update_none_values_ignored(tmp_path):
    store = RateLimitStore(str(tmp_path))
    store.update("groq", "llama-8b", rpm=None, tpm=None)
    assert not (tmp_path / "rate_limits.json").exists()


def test_empty_state_dir_does_not_crash():
    store = RateLimitStore("")
    store.update("groq", "llama-8b", rpm=30, tpm=6000)  # should be no-op
    assert store.get_rpm("groq", "llama-8b", default=99) == 99
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_rate_limits.py -v
```
Expected: `ModuleNotFoundError: No module named 'flexrouter.rate_limits'`

- [ ] **Step 3: Implement RateLimitStore**

```python
# flexrouter/rate_limits.py
from __future__ import annotations
import json
from pathlib import Path


class RateLimitStore:
    def __init__(self, state_dir: str) -> None:
        self._path = Path(state_dir) / "rate_limits.json" if state_dir else None
        self._data: dict = {}
        if self._path and self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
            except Exception:
                pass

    def update(self, provider: str, model: str, rpm: int | None, tpm: int | None) -> None:
        if self._path is None:
            return
        if rpm is None and tpm is None:
            return
        key = f"{provider}/{model}"
        entry = dict(self._data.get(key, {}))
        changed = False
        if rpm is not None and entry.get("rpm") != rpm:
            entry["rpm"] = rpm
            changed = True
        if tpm is not None and entry.get("tpm") != tpm:
            entry["tpm"] = tpm
            changed = True
        if changed:
            self._data[key] = entry
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, indent=2))

    def get_rpm(self, provider: str, model: str, default: int) -> int:
        return self._data.get(f"{provider}/{model}", {}).get("rpm", default)

    def get_tpm(self, provider: str, model: str, default: int) -> int:
        return self._data.get(f"{provider}/{model}", {}).get("tpm", default)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_rate_limits.py -v
```
Expected: 6 passed

- [ ] **Step 5: Commit**

```
git add flexrouter/rate_limits.py tests/test_rate_limits.py
git commit -m "feat: RateLimitStore persists learned rpm/tpm to state dir"
```

---

## Task 2: Client Rate Limit Extraction

**Files:**
- Modify: `flexrouter/client.py`
- Modify: `tests/test_client.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_client.py`:

```python
# Add these imports at top of test_client.py
from unittest.mock import MagicMock
from flexrouter.rate_limits import RateLimitStore

@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_headers_stored_on_success(tmp_path):
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json=OK_RESPONSE,
            headers={
                "x-ratelimit-limit-requests": "30",
                "x-ratelimit-limit-tokens": "6000",
            },
        )
    )
    store = RateLimitStore(str(tmp_path))
    async with AsyncClient(rate_limit_store=store) as client:
        await client.chat(ROUTE, MESSAGES)
    assert store.get_rpm("groq", "llama-8b", default=0) == 30
    assert store.get_tpm("groq", "llama-8b", default=0) == 6000


@pytest.mark.asyncio
@respx.mock
async def test_missing_rate_limit_headers_no_error(tmp_path):
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE)
    )
    store = RateLimitStore(str(tmp_path))
    async with AsyncClient(rate_limit_store=store) as client:
        result = await client.chat(ROUTE, MESSAGES)
    assert result["choices"][0]["message"]["content"] == "hi"


@pytest.mark.asyncio
@respx.mock
async def test_no_store_still_works():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json=OK_RESPONSE,
            headers={"x-ratelimit-limit-requests": "30"},
        )
    )
    async with AsyncClient() as client:
        result = await client.chat(ROUTE, MESSAGES)
    assert result is not None
```

- [ ] **Step 2: Run new tests to verify they fail**

```
pytest tests/test_client.py::test_rate_limit_headers_stored_on_success tests/test_client.py::test_missing_rate_limit_headers_no_error tests/test_client.py::test_no_store_still_works -v
```
Expected: TypeError (AsyncClient doesn't accept rate_limit_store)

- [ ] **Step 3: Modify AsyncClient**

Replace `flexrouter/client.py` with:

```python
from __future__ import annotations
import httpx
from flexrouter.engine import RouteResult
from flexrouter.exceptions import RouterError


class RateLimitError(Exception):
    pass

class ProviderError(Exception):
    pass


def _parse_int_header(headers, name: str) -> int | None:
    val = headers.get(name)
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


class AsyncClient:
    def __init__(self, rate_limit_store=None) -> None:
        self._client = httpx.AsyncClient(timeout=60.0)
        self._rate_limit_store = rate_limit_store

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

        if self._rate_limit_store is not None:
            rpm = _parse_int_header(resp.headers, "x-ratelimit-limit-requests")
            tpm = _parse_int_header(resp.headers, "x-ratelimit-limit-tokens")
            if rpm is not None or tpm is not None:
                self._rate_limit_store.update(route.provider, route.model, rpm, tpm)

        try:
            return resp.json()
        except Exception as exc:
            raise ProviderError(f"Invalid JSON from {route.provider}/{route.model}: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self.aclose()
```

- [ ] **Step 4: Run all client tests**

```
pytest tests/test_client.py -v
```
Expected: all pass (including original tests — `AsyncClient()` still works without store)

- [ ] **Step 5: Commit**

```
git add flexrouter/client.py tests/test_client.py
git commit -m "feat: extract x-ratelimit headers from responses into RateLimitStore"
```

---

## Task 3: Engine — Learned Limits + Ollama Empty Keys

**Files:**
- Modify: `flexrouter/engine.py`
- Modify: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_engine.py`:

```python
# Add at top: from flexrouter.rate_limits import RateLimitStore

def test_make_result_empty_api_keys_uses_empty_string(tmp_path):
    """Ollama and other local providers have no api_keys."""
    from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig, RetryConfig
    cfg = FlexConfig(
        tiers={"default": [ModelConfig(provider="ollama", model="llama3", score=50, rpm=600, tpm=10_000_000)]},
        providers={"ollama": ProviderConfig(base_url="http://localhost:11434/v1", api_keys=[])},
        retry=RetryConfig(),
    )
    engine = RoutingEngine(cfg)
    result = engine._make_result(cfg.tiers["default"][0], "default")
    assert result.api_key == ""


def test_score_candidates_uses_learned_rpm(tmp_path):
    """Engine uses RateLimitStore rpm over ModelConfig.rpm when available."""
    from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig, RetryConfig
    from flexrouter.window import SlidingWindow

    store = RateLimitStore(str(tmp_path))
    # Model config says rpm=30 but store says rpm=5 (very low)
    store.update("groq", "llama-8b", rpm=5, tpm=None)

    cfg = FlexConfig(
        tiers={"default": [ModelConfig(provider="groq", model="llama-8b", score=80, rpm=30, tpm=6000)]},
        providers={"groq": ProviderConfig(base_url="https://api.groq.com/openai/v1", api_keys=["key"])},
        retry=RetryConfig(),
    )
    engine = RoutingEngine(cfg, rate_limit_store=store)

    # Saturate the window at learned rpm=5 (not config rpm=30)
    window = engine._windows["groq/llama-8b"]
    for _ in range(5):
        window.record(0)

    # With learned rpm=5, window should be full; with config rpm=30, it wouldn't be
    candidates = engine._score_candidates(cfg.tiers["default"], 0, False)
    assert candidates == []
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_engine.py::test_make_result_empty_api_keys_uses_empty_string tests/test_engine.py::test_score_candidates_uses_learned_rpm -v
```
Expected: both fail (`ValueError: Provider 'ollama' has no api_keys` and `TypeError`)

- [ ] **Step 3: Modify engine.py**

Add `rate_limit_store` parameter and helper methods. In `flexrouter/engine.py`:

Change `__init__` signature:
```python
def __init__(self, cfg: FlexConfig, rate_limit_store=None) -> None:
    self._cfg = cfg
    self._rate_limit_store = rate_limit_store
    # ... rest unchanged ...
```

Add helper methods after `_model_available`:
```python
def _model_rpm(self, m: ModelConfig) -> int:
    if self._rate_limit_store is not None:
        return self._rate_limit_store.get_rpm(m.provider, m.model, m.rpm)
    return m.rpm

def _model_tpm(self, m: ModelConfig) -> int:
    if self._rate_limit_store is not None:
        return self._rate_limit_store.get_tpm(m.provider, m.model, m.tpm)
    return m.tpm
```

In `_score_candidates`, replace `w.available(m.rpm, m.tpm)` with:
```python
            if w and not w.available(self._model_rpm(m), self._model_tpm(m)):
                continue
```

In `seconds_until_available`, replace `w.seconds_until_available(m.rpm, m.tpm)` with:
```python
                    secs = w.seconds_until_available(self._model_rpm(m), self._model_tpm(m))
```

In `_make_result`, replace the api_keys check:
```python
    def _make_result(self, m: ModelConfig, tier: str) -> RouteResult:
        provider_cfg = self._cfg.providers[m.provider]
        if provider_cfg.api_keys:
            counter = self._key_counters.get(m.provider, 0)
            api_key = provider_cfg.api_keys[counter % len(provider_cfg.api_keys)]
            self._key_counters[m.provider] = counter + 1
        else:
            api_key = ""  # local providers (Ollama)
        return RouteResult(
            provider=m.provider,
            model=m.model,
            api_key=api_key,
            base_url=provider_cfg.base_url,
            tier=tier,
        )
```

In `_model_available`, remove this check (or leave it — it won't match since we fixed `_make_result`):
```python
    # _model_available doesn't need changes; empty api_keys providers will route fine
```

- [ ] **Step 4: Run all engine tests**

```
pytest tests/test_engine.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```
git add flexrouter/engine.py tests/test_engine.py
git commit -m "feat: engine uses learned rate limits, supports empty api_keys for local providers"
```

---

## Task 4: FlexRouter Wiring

**Files:**
- Modify: `flexrouter/_router.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_flexrouter.py` (check existing test structure first):

```python
def test_flexrouter_creates_rate_limit_store(config_file, tmp_path):
    """FlexRouter wires RateLimitStore to client and engine."""
    from flexrouter import FlexRouter
    from flexrouter.rate_limits import RateLimitStore
    router = FlexRouter(str(config_file))
    assert router._rate_limit_store is not None
    assert isinstance(router._rate_limit_store, RateLimitStore)
    assert router._client._rate_limit_store is router._rate_limit_store
    assert router._engine._rate_limit_store is router._rate_limit_store
    router.close()
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_flexrouter.py::test_flexrouter_creates_rate_limit_store -v
```
Expected: `AttributeError: 'FlexRouter' object has no attribute '_rate_limit_store'`

- [ ] **Step 3: Modify _router.py**

In `FlexRouter.__init__`, add after `self._config_path = path` and before `self._engine`:

```python
from flexrouter.rate_limits import RateLimitStore

class FlexRouter:
    def __init__(self, config_path: Optional[str] = None) -> None:
        # ... existing path resolution unchanged ...
        self._config_path = path
        self._cfg: FlexConfig = load_config(path)
        self._rate_limit_store = RateLimitStore(self._cfg.state_dir)
        self._engine = RoutingEngine(self._cfg, rate_limit_store=self._rate_limit_store)
        self._audit = AuditLogger(self._cfg.state_dir)
        self._client = AsyncClient(rate_limit_store=self._rate_limit_store)
        # ... rest unchanged ...
```

Add the import at the top of `_router.py`:
```python
from flexrouter.rate_limits import RateLimitStore
```

- [ ] **Step 4: Run all tests**

```
pytest -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```
git add flexrouter/_router.py
git commit -m "feat: wire RateLimitStore into FlexRouter, engine, and client"
```

---

## Task 5: Provider Registry

**Files:**
- Modify: `flexrouter/onboard.py` (begin rewrite — replace existing content)
- Create: `tests/test_onboard.py`

- [ ] **Step 1: Write failing tests for registry**

```python
# tests/test_onboard.py
from flexrouter.onboard import PROVIDERS, ProviderDef


def test_provider_count():
    assert len(PROVIDERS) == 8


def test_all_providers_have_required_fields():
    for p in PROVIDERS:
        assert p.name
        assert p.base_url.startswith("http")
        assert p.signup_url.startswith("http")
        assert callable(p.free_filter)
        assert "rpm" in p.rate_limit_headers
        assert "tpm" in p.rate_limit_headers


def test_free_providers():
    free = [p for p in PROVIDERS if p.free]
    names = {p.name for p in free}
    assert names == {"cerebras", "groq", "openrouter", "googleai", "ollama"}


def test_paid_providers():
    paid = [p for p in PROVIDERS if not p.free]
    names = {p.name for p in paid}
    assert names == {"deepseek", "siliconflow", "sambanova"}


def test_ollama_flag():
    ollama = next(p for p in PROVIDERS if p.name == "ollama")
    assert ollama.ollama is True


def test_openrouter_free_filter():
    from flexrouter.onboard import PROVIDERS
    or_provider = next(p for p in PROVIDERS if p.name == "openrouter")
    free_model = {"id": "qwen/qwen3:free", "pricing": {"prompt": "0", "completion": "0"}}
    paid_model = {"id": "openai/gpt-4o", "pricing": {"prompt": "0.000005", "completion": "0.000015"}}
    assert or_provider.free_filter(free_model) is True
    assert or_provider.free_filter(paid_model) is False


def test_googleai_free_filter():
    googleai = next(p for p in PROVIDERS if p.name == "googleai")
    flash = {"id": "gemini-2.5-flash"}
    pro = {"id": "gemini-2.5-pro"}
    assert googleai.free_filter(flash) is True
    assert googleai.free_filter(pro) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_onboard.py -v
```
Expected: `ImportError` or `AttributeError`

- [ ] **Step 3: Write the provider registry**

Replace `flexrouter/onboard.py` with:

```python
from __future__ import annotations
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import click
import httpx
import yaml

STANDARD_RL_HEADERS = {
    "rpm": "x-ratelimit-limit-requests",
    "tpm": "x-ratelimit-limit-tokens",
}


@dataclass
class ProviderDef:
    name: str
    base_url: str
    signup_url: str
    free: bool
    free_filter: Callable[[dict], bool]
    default_rpm: int
    default_tpm: int
    rate_limit_headers: dict = field(default_factory=lambda: dict(STANDARD_RL_HEADERS))
    ollama: bool = False


def _all_free(_model: dict) -> bool:
    return True


def _openrouter_free(model: dict) -> bool:
    pricing = model.get("pricing", {})
    try:
        return float(pricing.get("prompt", "1")) == 0.0
    except (ValueError, TypeError):
        return ":free" in model.get("id", "")


def _googleai_free(model: dict) -> bool:
    mid = model.get("id", "").lower()
    return "flash" in mid


PROVIDERS: list[ProviderDef] = [
    ProviderDef(
        name="cerebras",
        base_url="https://api.cerebras.ai/v1",
        signup_url="https://cloud.cerebras.ai",
        free=True,
        free_filter=_all_free,
        default_rpm=30,
        default_tpm=60_000,
    ),
    ProviderDef(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        signup_url="https://console.groq.com/keys",
        free=True,
        free_filter=_all_free,
        default_rpm=30,
        default_tpm=6_000,
    ),
    ProviderDef(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        signup_url="https://openrouter.ai/keys",
        free=True,
        free_filter=_openrouter_free,
        default_rpm=20,
        default_tpm=100_000,
    ),
    ProviderDef(
        name="googleai",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        signup_url="https://aistudio.google.com/apikey",
        free=True,
        free_filter=_googleai_free,
        default_rpm=15,
        default_tpm=1_000_000,
    ),
    ProviderDef(
        name="ollama",
        base_url="http://localhost:11434/v1",
        signup_url="https://ollama.com",
        free=True,
        free_filter=_all_free,
        default_rpm=600,
        default_tpm=10_000_000,
        ollama=True,
    ),
    ProviderDef(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        signup_url="https://platform.deepseek.com",
        free=False,
        free_filter=_all_free,
        default_rpm=60,
        default_tpm=200_000,
    ),
    ProviderDef(
        name="siliconflow",
        base_url="https://api.siliconflow.cn/v1",
        signup_url="https://cloud.siliconflow.cn",
        free=False,
        free_filter=_all_free,
        default_rpm=20,
        default_tpm=100_000,
    ),
    ProviderDef(
        name="sambanova",
        base_url="https://api.sambanova.ai/v1",
        signup_url="https://cloud.sambanova.ai",
        free=False,
        free_filter=_all_free,
        default_rpm=20,
        default_tpm=50_000,
    ),
]
```

- [ ] **Step 4: Run registry tests**

```
pytest tests/test_onboard.py -v
```
Expected: all 8 tests pass

- [ ] **Step 5: Commit**

```
git add flexrouter/onboard.py tests/test_onboard.py
git commit -m "feat: provider registry with ProviderDef dataclass for 8 providers"
```

---

## Task 6: Model Discovery

**Files:**
- Modify: `flexrouter/onboard.py` (add discovery functions)
- Modify: `tests/test_onboard.py` (add discovery tests)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_onboard.py`:

```python
import pytest
import respx
import httpx


GROQ_MODELS_RESPONSE = {
    "data": [
        {"id": "llama-3.3-70b-versatile", "context_window": 131072},
        {"id": "llama-3.1-8b-instant", "context_window": 131072},
    ]
}

OPENROUTER_MODELS_RESPONSE = {
    "data": [
        {"id": "qwen/qwen3:free", "context_length": 262144, "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "openai/gpt-4o", "context_length": 128000, "pricing": {"prompt": "0.000005", "completion": "0.000015"}},
    ]
}

OLLAMA_MODELS_RESPONSE = {
    "data": [
        {"id": "llama3:latest", "context_window": 8192},
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_discover_models_returns_filtered_list():
    from flexrouter.onboard import discover_models
    groq = next(p for p in PROVIDERS if p.name == "groq")
    respx.get("https://api.groq.com/openai/v1/models").mock(
        return_value=httpx.Response(200, json=GROQ_MODELS_RESPONSE)
    )
    models = await discover_models(groq, api_key="test-key")
    assert len(models) == 2
    assert models[0]["id"] == "llama-3.3-70b-versatile"


@pytest.mark.asyncio
@respx.mock
async def test_discover_models_filters_paid_from_openrouter():
    from flexrouter.onboard import discover_models
    or_provider = next(p for p in PROVIDERS if p.name == "openrouter")
    respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(200, json=OPENROUTER_MODELS_RESPONSE)
    )
    models = await discover_models(or_provider, api_key="test-key")
    assert len(models) == 1
    assert models[0]["id"] == "qwen/qwen3:free"


@pytest.mark.asyncio
@respx.mock
async def test_discover_models_returns_empty_on_auth_error():
    from flexrouter.onboard import discover_models
    groq = next(p for p in PROVIDERS if p.name == "groq")
    respx.get("https://api.groq.com/openai/v1/models").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    models = await discover_models(groq, api_key="bad-key")
    assert models == []


@pytest.mark.asyncio
@respx.mock
async def test_discover_ollama_returns_models_when_running():
    from flexrouter.onboard import discover_ollama
    respx.get("http://localhost:11434/v1/models").mock(
        return_value=httpx.Response(200, json=OLLAMA_MODELS_RESPONSE)
    )
    models = await discover_ollama()
    assert len(models) == 1
    assert models[0]["id"] == "llama3:latest"


@pytest.mark.asyncio
@respx.mock
async def test_discover_ollama_returns_empty_when_not_running():
    from flexrouter.onboard import discover_ollama
    respx.get("http://localhost:11434/v1/models").mock(side_effect=httpx.ConnectError("refused"))
    models = await discover_ollama()
    assert models == []
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_onboard.py::test_discover_models_returns_filtered_list tests/test_onboard.py::test_discover_ollama_returns_models_when_running -v
```
Expected: `ImportError: cannot import name 'discover_models'`

- [ ] **Step 3: Add discovery functions to onboard.py**

Append to `flexrouter/onboard.py` (after the PROVIDERS list):

```python

async def discover_models(provider: ProviderDef, api_key: str) -> list[dict]:
    """GET /v1/models for a provider, return free-filtered model dicts."""
    url = f"{provider.base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code >= 400:
            return []
        data = resp.json()
        models = data.get("data", data) if isinstance(data, dict) else data
        return [m for m in models if provider.free_filter(m)]
    except Exception:
        return []


async def discover_ollama() -> list[dict]:
    """Auto-detect Ollama at localhost:11434. Returns model dicts or []."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://localhost:11434/v1/models")
        if resp.status_code >= 400:
            return []
        data = resp.json()
        return data.get("data", data) if isinstance(data, dict) else data
    except Exception:
        return []


def _context_window(model: dict) -> int:
    return int(
        model.get("context_window")
        or model.get("context_length")
        or 131_072
    )
```

- [ ] **Step 4: Run discovery tests**

```
pytest tests/test_onboard.py -v
```
Expected: all discovery tests pass

- [ ] **Step 5: Commit**

```
git add flexrouter/onboard.py tests/test_onboard.py
git commit -m "feat: async model discovery per provider with free filter"
```

---

## Task 7: AA Scoring

**Files:**
- Modify: `flexrouter/onboard.py` (add scoring)
- Modify: `tests/test_onboard.py` (add scoring tests)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_onboard.py`:

```python
AA_RESPONSE = {
    "data": [
        {
            "id": "llama-3-3-70b-instruct",
            "name": "Llama 3.3 70B Instruct",
            "slug": "llama-3-3-70b-instruct",
            "evaluations": {"artificial_analysis_intelligence_index": 72.5},
        },
        {
            "id": "gemini-2-5-flash",
            "name": "Gemini 2.5 Flash",
            "slug": "gemini-2-5-flash",
            "evaluations": {"artificial_analysis_intelligence_index": 81.0},
        },
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_score_with_aa_matches_known_model():
    from flexrouter.onboard import score_with_aa
    respx.get("https://artificialanalysis.ai/data/llms/models").mock(
        return_value=httpx.Response(200, json=AA_RESPONSE)
    )
    models = [
        {"id": "meta-llama/llama-3.3-70b-instruct:free", "context_window": 131072},
    ]
    scored = await score_with_aa(models, aa_key="test-aa-key")
    assert scored[0]["score"] == 72


@pytest.mark.asyncio
@respx.mock
async def test_score_with_aa_unknown_model_gets_50():
    from flexrouter.onboard import score_with_aa
    respx.get("https://artificialanalysis.ai/data/llms/models").mock(
        return_value=httpx.Response(200, json=AA_RESPONSE)
    )
    models = [{"id": "unknown/totally-new-model", "context_window": 32768}]
    scored = await score_with_aa(models, aa_key="test-aa-key")
    assert scored[0]["score"] == 50


@pytest.mark.asyncio
@respx.mock
async def test_score_with_aa_api_failure_gives_50():
    from flexrouter.onboard import score_with_aa
    respx.get("https://artificialanalysis.ai/data/llms/models").mock(
        return_value=httpx.Response(500, text="error")
    )
    models = [{"id": "groq/llama-8b", "context_window": 131072}]
    scored = await score_with_aa(models, aa_key="test-aa-key")
    assert scored[0]["score"] == 50


@pytest.mark.asyncio
async def test_score_with_aa_no_key_gives_50():
    from flexrouter.onboard import score_with_aa
    models = [{"id": "groq/llama-8b", "context_window": 131072}]
    scored = await score_with_aa(models, aa_key=None)
    assert scored[0]["score"] == 50
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_onboard.py::test_score_with_aa_matches_known_model tests/test_onboard.py::test_score_with_aa_no_key_gives_50 -v
```
Expected: `ImportError: cannot import name 'score_with_aa'`

- [ ] **Step 3: Add scoring to onboard.py**

Append to `flexrouter/onboard.py`:

```python

def _normalize(s: str) -> str:
    """Lowercase, strip provider prefix, remove version/free suffixes, collapse non-alphanum."""
    s = s.lower()
    s = re.sub(r":free$", "", s)          # remove :free suffix
    s = s.split("/")[-1]                   # take part after last /
    s = re.sub(r"[^a-z0-9 ]", " ", s)     # non-alphanum → space
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _best_score(model_id: str, aa_lookup: list[tuple[str, int]]) -> int:
    needle = _normalize(model_id)
    for norm_name, score in aa_lookup:
        if needle in norm_name or norm_name in needle:
            return score
    return 50


async def score_with_aa(models: list[dict], aa_key: str | None) -> list[dict]:
    """Assign AA intelligence scores to models. Unmatched → score=50."""
    if not aa_key:
        return [{**m, "score": 50} for m in models]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://artificialanalysis.ai/data/llms/models",
                headers={"x-api-key": aa_key},
            )
        if resp.status_code >= 400:
            return [{**m, "score": 50} for m in models]
        data = resp.json()
        aa_models = data.get("data", [])
        aa_lookup = [
            (
                _normalize(entry.get("name", "")),
                int(entry.get("evaluations", {}).get("artificial_analysis_intelligence_index", 50)),
            )
            for entry in aa_models
        ]
    except Exception:
        return [{**m, "score": 50} for m in models]

    return [{**m, "score": _best_score(m["id"], aa_lookup)} for m in models]
```

- [ ] **Step 4: Run AA scoring tests**

```
pytest tests/test_onboard.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```
git add flexrouter/onboard.py tests/test_onboard.py
git commit -m "feat: AA Intelligence Index scoring for discovered models"
```

---

## Task 8: YAML Generation

**Files:**
- Modify: `flexrouter/onboard.py` (add yaml builder)
- Modify: `tests/test_onboard.py` (add yaml tests)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_onboard.py`:

```python
def test_build_yaml_produces_valid_yaml():
    from flexrouter.onboard import build_yaml, PROVIDERS

    provider_keys = {"groq": "gsk-test", "openrouter": "sk-or-test"}
    groq_def = next(p for p in PROVIDERS if p.name == "groq")
    or_def = next(p for p in PROVIDERS if p.name == "openrouter")

    free_models = [
        {"id": "llama-3.3-70b-versatile", "context_window": 131072, "score": 72, "_provider": "groq"},
        {"id": "qwen/qwen3:free", "context_window": 262144, "score": 87, "_provider": "openrouter"},
    ]
    paid_models = []

    text = build_yaml(provider_keys, free_models, paid_models, PROVIDERS)
    doc = yaml.safe_load(text)

    assert "groq" in doc["providers"]
    assert doc["providers"]["groq"]["api_keys"][0]["key"] == "gsk-test"
    assert "openrouter" in doc["providers"]
    assert len(doc["tiers"]["default"]) == 2
    assert "paid" not in doc["tiers"]


def test_build_yaml_includes_paid_tier():
    from flexrouter.onboard import build_yaml, PROVIDERS

    provider_keys = {"deepseek": "dsk-test"}
    free_models = []
    paid_models = [
        {"id": "deepseek-chat", "context_window": 65536, "score": 74, "_provider": "deepseek"},
    ]

    text = build_yaml(provider_keys, free_models, paid_models, PROVIDERS)
    doc = yaml.safe_load(text)
    assert "paid" in doc["tiers"]
    assert doc["tiers"]["paid"][0]["model"] == "deepseek-chat"


def test_build_yaml_models_sorted_by_score_desc():
    from flexrouter.onboard import build_yaml, PROVIDERS

    provider_keys = {"groq": "key"}
    free_models = [
        {"id": "model-a", "context_window": 8192, "score": 40, "_provider": "groq"},
        {"id": "model-b", "context_window": 8192, "score": 90, "_provider": "groq"},
        {"id": "model-c", "context_window": 8192, "score": 60, "_provider": "groq"},
    ]
    text = build_yaml(provider_keys, free_models, [], PROVIDERS)
    doc = yaml.safe_load(text)
    scores = [m["score"] for m in doc["tiers"]["default"]]
    assert scores == sorted(scores, reverse=True)


def test_build_yaml_ollama_has_empty_api_keys():
    from flexrouter.onboard import build_yaml, PROVIDERS

    provider_keys = {}  # Ollama needs no key
    free_models = [
        {"id": "llama3:latest", "context_window": 8192, "score": 50, "_provider": "ollama"},
    ]
    text = build_yaml(provider_keys, free_models, [], PROVIDERS)
    doc = yaml.safe_load(text)
    assert doc["providers"]["ollama"]["api_keys"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_onboard.py::test_build_yaml_produces_valid_yaml -v
```
Expected: `ImportError: cannot import name 'build_yaml'`

- [ ] **Step 3: Add build_yaml to onboard.py**

Append to `flexrouter/onboard.py`:

```python

def build_yaml(
    provider_keys: dict[str, str],
    free_models: list[dict],
    paid_models: list[dict],
    providers: list[ProviderDef],
) -> str:
    provider_map = {p.name: p for p in providers}

    # Collect all referenced provider names
    all_models = free_models + paid_models
    used_providers = {m["_provider"] for m in all_models}
    # Always include providers with keys even if no models found
    used_providers |= set(provider_keys.keys())

    lines = ["providers:"]
    for name in sorted(used_providers):
        pdef = provider_map[name]
        lines.append(f"  {name}:")
        lines.append(f"    base_url: {pdef.base_url}")
        key = provider_keys.get(name)
        if key:
            lines.append("    api_keys:")
            lines.append(f"      - key: {key}")
        else:
            lines.append("    api_keys: []")

    def model_block(m: dict, pdef: ProviderDef) -> list[str]:
        return [
            f"    - provider: {m['_provider']}",
            f"      model: {m['id']}",
            f"      score: {m['score']}",
            f"      rpm: {pdef.default_rpm}",
            f"      tpm: {pdef.default_tpm}",
            f"      context_window: {_context_window(m)}",
        ]

    lines.append("")
    lines.append("tiers:")

    if free_models:
        sorted_free = sorted(free_models, key=lambda m: m["score"], reverse=True)
        lines.append("  default:")
        for m in sorted_free:
            lines.extend(model_block(m, provider_map[m["_provider"]]))

    if paid_models:
        sorted_paid = sorted(paid_models, key=lambda m: m["score"], reverse=True)
        lines.append("  paid:")
        for m in sorted_paid:
            lines.extend(model_block(m, provider_map[m["_provider"]]))

    lines += [
        "",
        "settings:",
        "  state_dir: .flexrouter",
        "  dashboard_port: 7352",
        "",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run yaml tests**

```
pytest tests/test_onboard.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```
git add flexrouter/onboard.py tests/test_onboard.py
git commit -m "feat: build_yaml generates flexrouter.yaml from discovered models"
```

---

## Task 9: Wizard Loop

**Files:**
- Modify: `flexrouter/onboard.py` (add run_onboard)

The wizard is interactive; it can't be fully unit-tested. Instead, verify the helpers it calls are all tested in prior tasks.

- [ ] **Step 1: Add run_onboard to onboard.py**

Append to `flexrouter/onboard.py`:

```python

def _read_existing_key(config_path: Path, provider_name: str) -> str | None:
    if not config_path.exists():
        return None
    try:
        raw = yaml.safe_load(config_path.read_text())
        keys = (raw or {}).get("providers", {}).get(provider_name, {}).get("api_keys", [])
        for k in keys:
            if isinstance(k, str) and k:
                return k
            if isinstance(k, dict) and k.get("key"):
                return k["key"]
    except Exception:
        pass
    return None


def _mask(key: str) -> str:
    if len(key) <= 8:
        return key[:2] + "***"
    return key[:6] + "..." + key[-4:]


def run_onboard() -> None:
    import asyncio

    if not sys.stdin.isatty():
        click.echo("init requires an interactive terminal.")
        return

    config_path = Path("flexrouter.yaml")
    provider_keys: dict[str, str] = {}

    # --- Free providers ---
    free_providers = [p for p in PROVIDERS if p.free and not p.ollama]
    click.echo("\nflexrouter onboarding\n")
    click.echo("Free providers:")

    for pdef in free_providers:
        existing = _read_existing_key(config_path, pdef.name)
        click.echo(f"\n- {pdef.name}")
        click.echo(f"  signup: {pdef.signup_url}")
        click.echo(f"  current: {_mask(existing) if existing else '(none)'}")
        click.echo("  Enter key, press Enter to keep, or - to clear.")
        raw = input("  key: ").strip()
        if raw == "-":
            click.echo("  cleared")
        elif raw:
            provider_keys[pdef.name] = raw
            click.echo("  updated")
        elif existing:
            provider_keys[pdef.name] = existing
            click.echo("  unchanged")
        else:
            click.echo("  skipped")

    # --- Paid providers ---
    include_paid = click.confirm("\nInclude paid providers?", default=False)
    paid_providers = []
    if include_paid:
        paid_defs = [p for p in PROVIDERS if not p.free]
        for pdef in paid_defs:
            existing = _read_existing_key(config_path, pdef.name)
            click.echo(f"\n- {pdef.name} (paid)")
            click.echo(f"  signup: {pdef.signup_url}")
            click.echo(f"  current: {_mask(existing) if existing else '(none)'}")
            click.echo("  Enter key, press Enter to keep, or - to clear.")
            raw = input("  key: ").strip()
            if raw == "-":
                click.echo("  cleared")
            elif raw:
                provider_keys[pdef.name] = raw
                paid_providers.append(pdef)
                click.echo("  updated")
            elif existing:
                provider_keys[pdef.name] = existing
                paid_providers.append(pdef)
                click.echo("  unchanged")
            else:
                click.echo("  skipped")

    if not provider_keys:
        click.echo("\nNo keys provided. flexrouter.yaml not written.")
        return

    # --- Discover models ---
    click.echo("\nDiscovering models...")

    async def _discover_all() -> tuple[list[dict], list[dict]]:
        free_models: list[dict] = []
        p_models: list[dict] = []

        for pdef in free_providers:
            key = provider_keys.get(pdef.name)
            if not key:
                continue
            models = await discover_models(pdef, key)
            for m in models:
                m["_provider"] = pdef.name
            free_models.extend(models)
            click.echo(f"  {pdef.name}: {len(models)} free models")

        # Ollama
        ollama_models = await discover_ollama()
        for m in ollama_models:
            m["_provider"] = "ollama"
        if ollama_models:
            click.echo(f"  ollama: {len(ollama_models)} local models")
            free_models.extend(ollama_models)

        for pdef in paid_providers:
            key = provider_keys.get(pdef.name)
            if not key:
                continue
            models = await discover_models(pdef, key)
            for m in models:
                m["_provider"] = pdef.name
            p_models.extend(models)
            click.echo(f"  {pdef.name}: {len(models)} models")

        return free_models, p_models

    free_models, paid_models = asyncio.run(_discover_all())

    # --- AA Scoring ---
    aa_key = os.environ.get("AA_API_KEY")
    if aa_key:
        click.echo("Scoring with Artificial Analysis...")
        all_models = asyncio.run(score_with_aa(free_models + paid_models, aa_key))
        split = len(free_models)
        free_models = all_models[:split]
        paid_models = all_models[split:]
    else:
        click.echo("AA_API_KEY not set — using score=50 for all models.")
        free_models = [{**m, "score": 50} for m in free_models]
        paid_models = [{**m, "score": 50} for m in paid_models]

    # --- Write yaml ---
    all_providers = free_providers + paid_providers
    ollama_def = next(p for p in PROVIDERS if p.name == "ollama")
    if any(m["_provider"] == "ollama" for m in free_models):
        all_providers = [ollama_def] + all_providers

    text = build_yaml(provider_keys, free_models, paid_models, PROVIDERS)
    config_path.write_text(text)
    click.echo(f"\nWritten to {config_path}")
    click.echo(f"  {len(free_models)} free models, {len(paid_models)} paid models")
    click.echo("Run `flexrouter dashboard` to start.")
```

- [ ] **Step 2: Run full test suite**

```
pytest -v
```
Expected: all pass

- [ ] **Step 3: Manual smoke test (if you have provider keys)**

```
AA_API_KEY=aa_ZRUHOdnDpweQdcbWTVNDCtlVKIWBlvVY python -m flexrouter init
```
Enter a Groq or OpenRouter key. Verify `flexrouter.yaml` is written with models and real AA scores.

- [ ] **Step 4: Commit**

```
git add flexrouter/onboard.py
git commit -m "feat: multi-provider onboarding wizard with model discovery and AA scoring"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] 8 providers (5 free, 3 paid-optional) — Task 5
- [x] Sequential wizard, free first, paid opt-in — Task 9
- [x] Ping /v1/models for model list, free filter — Task 6
- [x] Ollama auto-detect — Task 6
- [x] AA scoring with fallback to 50 — Task 7
- [x] Write flexrouter.yaml with providers + tiers — Task 8
- [x] Rate limit learning from response headers — Task 2
- [x] Persist to `.flexrouter/rate_limits.json` — Task 1
- [x] Engine uses learned limits for windowing — Task 3
- [x] Engine handles empty api_keys (Ollama) — Task 3
- [x] FlexRouter wires store to engine + client — Task 4

**Type consistency:**
- `discover_models` returns `list[dict]` — models always have `id`, may have `context_window`/`context_length`
- `score_with_aa` adds `score` key to each dict, returns `list[dict]`
- `build_yaml` expects `_provider` key on each model dict — set by run_onboard before calling
- `RateLimitStore.update(provider, model, rpm, tpm)` — rpm/tpm are `int | None`
