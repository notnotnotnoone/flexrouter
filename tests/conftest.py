import asyncio
import copy
import importlib
import os
import time as _real_time

import pytest
import yaml


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_startup_refresh: let LocalRouter run the real startup catalogue "
        "refresh instead of the conftest stub")
    config.addinivalue_line(
        "markers",
        "real_clock: make the router's sleeps really block, instead of "
        "advancing the conftest virtual clock")


# Every module whose idea of "now" decides whether a model, key or window is
# available again. They all `import time` and call time.time()/monotonic(),
# so swapping their `time` name is enough to move them together.
_CLOCKED_MODULES = (
    "flexrouter._router", "flexrouter.engine", "flexrouter.recovery",
    "flexrouter.window", "flexrouter.rate_limits", "flexrouter.quota",
    "flexrouter.key_state",
    # The dashboard's Allowance reads quota timestamps and asks when a cap
    # frees up; it must see the same clock as quota.py writes them with.
    "flexrouter.dashboard.facts",
)


class VirtualClock:
    """Real time plus every second the router asked to sleep.

    The router waits with real `asyncio.sleep` calls: a 2 s backoff after
    each failed attempt, and a wait for a penalised model's cooldown (30 s
    and up) when nothing else in the tier is free. Under test those waits
    were most of the suite's run time. Here a router sleep returns at once
    but moves this clock forward by the same amount, so the penalty it was
    waiting out really has expired when it looks again — the retry logic
    runs exactly as it would in production, only without the wall-clock
    wait. The offset only ever grows, so the clock stays monotonic, and real
    time still passes underneath it for tests that sleep themselves.
    """

    def __init__(self):
        self.offset = 0.0

    def advance(self, seconds: float) -> None:
        self.offset += max(0.0, seconds)

    def time(self) -> float:
        return _real_time.time() + self.offset

    def monotonic(self) -> float:
        return _real_time.monotonic() + self.offset


class _TimeModule:
    """Stands in for the `time` module inside a clocked flexrouter module."""

    def __init__(self, clock: VirtualClock):
        self.time = clock.time
        self.monotonic = clock.monotonic

    def __getattr__(self, name):
        return getattr(_real_time, name)


class _AsyncioModule:
    """Stands in for `asyncio` inside flexrouter._router: sleep is virtual."""

    def __init__(self, clock: VirtualClock):
        self._clock = clock

    async def sleep(self, delay, result=None):
        self._clock.advance(delay)
        return await asyncio.sleep(0, result)

    def __getattr__(self, name):
        return getattr(asyncio, name)


@pytest.fixture(autouse=True)
def virtual_clock(request, monkeypatch):
    clock = VirtualClock()
    if request.node.get_closest_marker("real_clock"):
        return clock
    for name in _CLOCKED_MODULES:
        monkeypatch.setattr(importlib.import_module(name), "time", _TimeModule(clock))
    monkeypatch.setattr("flexrouter._router.asyncio", _AsyncioModule(clock))
    return clock


@pytest.fixture(autouse=True)
def _no_startup_catalogue_refresh(request, monkeypatch):
    """Keep LocalRouter's startup catalogue refresh off the network.

    Every LocalRouter runs refresh_config() when it is built (ADR 0014),
    which calls every configured provider's real /models endpoint and, with
    an AA key present, artificialanalysis.ai. Most tests build a router and
    care about nothing of the sort. Tests that stub refresh_config
    themselves still can (their monkeypatch lands on top of this one), and
    a test that needs the real thing marks itself `real_startup_refresh`.
    """
    if request.node.get_closest_marker("real_startup_refresh"):
        return
    from flexrouter.refresh import RefreshResult

    def _stub_refresh_config(config_path, state_dir, aa_key=None):
        return RefreshResult(timestamp="1970-01-01T00:00:00Z", added=[], removed=[],
                             changed=[], provider_errors=[], pending_path=None)

    monkeypatch.setattr("flexrouter._router.refresh_config", _stub_refresh_config)


@pytest.fixture(autouse=True)
def _isolate_flexrouter_home(tmp_path_factory, monkeypatch):
    """Never let a test touch the real machine-wide flexrouter home.

    `load_config()` (and anything that calls it) unconditionally calls
    `home.ensure_home()`, even when given an explicit config path. Without
    this, any test that doesn't set FLEXROUTER_HOME itself would create or
    write into the developer's real home directory. A test that wants a
    specific home for its own fixture still can — it just calls
    monkeypatch.setenv("FLEXROUTER_HOME", ...) itself, which simply
    overrides this default for its duration.
    """
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path_factory.mktemp("flexrouter_home")))

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
def minimal_config():
    """A fresh deep copy of MINIMAL_CONFIG, safe for a test to mutate.

    A shallow `dict(MINIMAL_CONFIG)` only copies the top level; writing
    through to a nested dict (e.g. `cfg["providers"]["groq"]["api_keys"]`)
    lands on the module-level constant itself and can leak between tests.
    """
    return copy.deepcopy(MINIMAL_CONFIG)


@pytest.fixture
def config_file(tmp_path, minimal_config):
    cfg = minimal_config
    cfg["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    os.environ["GROQ_API_KEY"] = "test-key"
    yield p
    os.environ.pop("GROQ_API_KEY", None)
