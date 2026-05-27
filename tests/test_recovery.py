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

def test_penalty_expires():
    pb = PenaltyBox(base_seconds=1, max_seconds=10)  # 1s penalty
    pb.penalize("groq", "llama")
    assert pb.is_penalized("groq", "llama") is True
    time.sleep(1.1)
    assert pb.is_penalized("groq", "llama") is False

def test_penalty_until_returns_timestamp():
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.penalize("groq", "llama")
    until = pb.penalty_until("groq", "llama")
    assert until is not None
    assert until > time.monotonic()
