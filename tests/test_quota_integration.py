# tests/test_quota_integration.py
import os
import pytest
import yaml

from flexrouter import FlexRouter, RouterBusy
from flexrouter.quota import QuotaTracker


@pytest.fixture
def quota_config_file(tmp_path):
    state_dir = str(tmp_path / ".flexrouter")
    cfg = {
        "tiers": {
            "low": [
                {
                    "provider": "groq",
                    "model": "llama-3.1-8b-instant",
                    "score": 85,
                    "rpm": 60,
                    "tpm": 60000,
                    "context_window": 131072,
                    "quotas": {"rpd": 1},
                }
            ]
        },
        "providers": {
            "groq": {
                "base_url": "https://api.groq.com/openai/v1",
                "api_keys": [{"env": "GROQ_API_KEY"}],
            }
        },
        "settings": {"state_dir": state_dir},
    }
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    os.environ["GROQ_API_KEY"] = "test-key"
    yield p, state_dir
    os.environ.pop("GROQ_API_KEY", None)


def test_router_raises_busy_when_daily_quota_exhausted(quota_config_file):
    config_path, state_dir = quota_config_file

    # Pre-populate the daily quota as already exhausted before the router exists.
    QuotaTracker(state_dir).record("groq", "llama-3.1-8b-instant")

    router = FlexRouter(str(config_path))
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)
