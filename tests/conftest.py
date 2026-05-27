import os

import pytest
import yaml

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
