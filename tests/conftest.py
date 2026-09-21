import copy
import os

import pytest
import yaml


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
