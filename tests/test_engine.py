import time
import warnings
import pytest
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig, RetryConfig
from flexrouter.window import SlidingWindow
from flexrouter.recovery import PenaltyBox
from flexrouter.budget import DailyBudget
from flexrouter.engine import RoutingEngine, RouteResult


def make_engine(models=None):
    if models is None:
        models = [
            ModelConfig("groq", "llama-8b", score=85, rpm=60, tpm=60000, context_window=131072),
            ModelConfig("groq", "llama-70b", score=60, rpm=30, tpm=30000, context_window=131072),
        ]
    cfg = FlexConfig(
        tiers={"low": models},
        providers={"groq": ProviderConfig("http://groq", ["key"])},
        window_seconds=60,
        penalty_base_seconds=30,
        penalty_max_seconds=1800,
        session_ttl_minutes=30,
    )
    return RoutingEngine(cfg)


def test_picks_highest_score_when_both_available():
    engine = make_engine()
    result = engine.select("low", estimated_tokens=0, vision=False)
    assert result.model == "llama-8b"  # score 85 wins over 70


def test_skips_penalized_model():
    engine = make_engine()
    engine._penalties.penalize("groq", "llama-8b")
    result = engine.select("low", estimated_tokens=0, vision=False)
    assert result.model == "llama-70b"


def test_skips_rpm_exhausted_model():
    engine = make_engine()
    # Fill llama-8b's window
    w = engine._windows["groq/llama-8b"]
    for _ in range(60):
        w.record(tokens=0)
    result = engine.select("low", estimated_tokens=0, vision=False)
    assert result.model == "llama-70b"


def test_no_models_available_returns_none():
    engine = make_engine()
    engine._penalties.penalize("groq", "llama-8b")
    engine._penalties.penalize("groq", "llama-70b")
    result = engine.select("low", estimated_tokens=0, vision=False)
    assert result is None


def test_unknown_tier_raises():
    engine = make_engine()
    with pytest.raises(KeyError):
        engine.select("nuclear", estimated_tokens=0, vision=False)


def test_vision_filter_skips_non_vision():
    models = [
        ModelConfig("groq", "vision-model", score=90, rpm=60, tpm=60000, context_window=4096, vision=True),
        ModelConfig("groq", "text-model", score=95, rpm=60, tpm=60000, context_window=4096, vision=False),
    ]
    engine = make_engine(models)
    result = engine.select("low", estimated_tokens=0, vision=True)
    assert result.model == "vision-model"


def test_context_window_skip_emits_warning():
    models = [
        ModelConfig("groq", "small", score=90, rpm=60, tpm=60000, context_window=100),
        ModelConfig("groq", "large", score=80, rpm=60, tpm=60000, context_window=200000),
    ]
    engine = make_engine(models)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = engine.select("low", estimated_tokens=500, vision=False)
    assert result.model == "large"
    assert any("ContextWindowWarning" in str(x.category) for x in w)


def test_session_stickiness_returns_same_model():
    engine = make_engine()
    r1 = engine.select("low", estimated_tokens=0, vision=False, session_id="s1")
    r2 = engine.select("low", estimated_tokens=0, vision=False, session_id="s1")
    assert r1.model == r2.model


def test_session_expiry_rerouts(monkeypatch):
    engine = make_engine()
    engine._session_ttl = 0  # immediate expiry
    engine.select("low", estimated_tokens=0, vision=False, session_id="s2")
    # After expiry, session cleared — still returns a valid model
    result = engine.select("low", estimated_tokens=0, vision=False, session_id="s2")
    assert result is not None


def test_seconds_until_available():
    engine = make_engine()
    engine._penalties.penalize("groq", "llama-8b")
    engine._penalties.penalize("groq", "llama-70b")
    secs = engine.seconds_until_available("low")
    assert secs >= 0


def test_make_result_raises_on_empty_api_keys():
    models = [ModelConfig("groq", "llama-8b", score=85, rpm=60, tpm=60000, context_window=131072)]
    cfg = FlexConfig(
        tiers={"low": models},
        providers={"groq": ProviderConfig("http://groq", [])},  # empty api_keys
        window_seconds=60,
        penalty_base_seconds=30,
        penalty_max_seconds=1800,
        session_ttl_minutes=30,
    )
    engine = RoutingEngine(cfg)
    with pytest.raises(ValueError, match="no api_keys"):
        engine.select("low", estimated_tokens=0, vision=False)


def test_update_config_updates_penalty_params():
    engine = make_engine()
    assert engine._penalties.base_seconds == 30
    new_cfg = FlexConfig(
        tiers={"low": [ModelConfig("groq", "llama-8b", score=85, rpm=60, tpm=60000, context_window=131072)]},
        providers={"groq": ProviderConfig("http://groq", ["key"])},
        window_seconds=60,
        penalty_base_seconds=60,
        penalty_max_seconds=3600,
        session_ttl_minutes=30,
    )
    engine.update_config(new_cfg)
    assert engine._penalties.base_seconds == 60
    assert engine._penalties.max_seconds == 3600
