# tests/test_router_wiring.py
import json
from pathlib import Path
import pytest
from flexrouter._router import LocalRouter

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

def _make(tmp_path) -> LocalRouter:
    state = tmp_path / "st"
    cfg = tmp_path / "flexrouter.yaml"
    cfg.write_text(CONFIG.replace("STATE", str(state).replace("\\", "/")))
    return LocalRouter(config_path=str(cfg))

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
