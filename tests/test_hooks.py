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
