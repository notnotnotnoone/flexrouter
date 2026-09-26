# tests/test_engine_snapshot.py
import pytest
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.engine import RoutingEngine

def _cfg():
    return FlexConfig(
        tiers={"default": [ModelConfig("groq", "llama", 50, 30, 6000)]},
        providers={"groq": ProviderConfig(base_url="http://x", api_keys=["k"])},
    )

def test_snapshot_lists_all_models():
    eng = RoutingEngine(_cfg())
    snap = eng.health_snapshot()
    assert "groq/llama" in snap["models"]
    assert snap["models"]["groq/llama"]["status"] == "ready"
    assert snap["providers"]["groq"]["models_total"] == 1

def test_snapshot_marks_busy():
    eng = RoutingEngine(_cfg())
    eng._status.set_busy("groq", "llama", 60, "Too many requests")
    snap = eng.health_snapshot()
    assert snap["models"]["groq/llama"]["status"] == "busy"
    assert snap["models"]["groq/llama"]["until"] is not None
    assert snap["providers"]["groq"]["models_up"] == 0


def test_remaining_capacity_reports_full_headroom_when_unused():
    eng = RoutingEngine(_cfg())
    cap = eng.remaining_capacity("default")
    assert cap["groq/llama"]["rpm_remaining"] == 30
    assert cap["groq/llama"]["tpm_remaining"] == 6000


def test_remaining_capacity_subtracts_recorded_usage():
    eng = RoutingEngine(_cfg())
    eng.record_request("groq", "llama", tokens=1000)
    cap = eng.remaining_capacity("default")
    assert cap["groq/llama"]["rpm_remaining"] == 29
    assert cap["groq/llama"]["tpm_remaining"] == 5000


def test_remaining_capacity_excludes_models_outside_the_top_20_percent_pool():
    cfg = FlexConfig(
        tiers={"default": [
            ModelConfig("googleai", "gemini", 95, 15, 1000000),
            ModelConfig("cerebras", "glm", 70, 30, 60000),
        ]},
        providers={
            "googleai": ProviderConfig(base_url="http://x", api_keys=["k"]),
            "cerebras": ProviderConfig(base_url="http://y", api_keys=["k"]),
        },
    )
    eng = RoutingEngine(cfg)
    cap = eng.remaining_capacity("default")
    assert "googleai/gemini" in cap
    assert "cerebras/glm" not in cap  # score 70 < 95 * 0.8 = 76, outside the pool


def test_remaining_capacity_unknown_tier_raises_key_error():
    eng = RoutingEngine(_cfg())
    with pytest.raises(KeyError):
        eng.remaining_capacity("nonexistent")


def test_remaining_capacity_returns_empty_dict_when_everyone_unavailable():
    eng = RoutingEngine(_cfg())
    eng._status.set_busy("groq", "llama", 60, "Too many requests")
    cap = eng.remaining_capacity("default")
    assert cap == {}
