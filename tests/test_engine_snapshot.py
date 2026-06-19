# tests/test_engine_snapshot.py
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
    assert snap["models"]["groq/llama"]["status"] == "up"
    assert snap["providers"]["groq"]["models_total"] == 1

def test_snapshot_marks_penalized():
    eng = RoutingEngine(_cfg())
    eng.penalize("groq", "llama")
    snap = eng.health_snapshot()
    assert snap["models"]["groq/llama"]["penalized"] is True
    assert snap["models"]["groq/llama"]["status"] == "penalized"
    assert snap["providers"]["groq"]["models_up"] == 0
