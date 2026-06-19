import json
import time
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


def test_update_zero_rpm_does_not_overwrite_positive_value(tmp_path):
    """rpm=0 must be ignored so it cannot permanently disable a stored positive value."""
    store = RateLimitStore(str(tmp_path))
    store.update("groq", "llama-8b", rpm=60, tpm=12000)
    # Now send a zero — should be a no-op
    store.update("groq", "llama-8b", rpm=0, tpm=0)
    assert store.get_rpm("groq", "llama-8b", default=30) == 60
    assert store.get_tpm("groq", "llama-8b", default=6000) == 12000


def test_update_zero_rpm_alone_does_not_overwrite(tmp_path):
    """rpm=0 with tpm=None must not overwrite a previously stored rpm."""
    store = RateLimitStore(str(tmp_path))
    store.update("groq", "llama-8b", rpm=45, tpm=None)
    store.update("groq", "llama-8b", rpm=0, tpm=None)
    assert store.get_rpm("groq", "llama-8b", default=30) == 45


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
