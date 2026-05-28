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
