import time
import warnings
import pytest
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig, RetryConfig
from flexrouter.window import SlidingWindow
from flexrouter.recovery import PenaltyBox
from flexrouter.budget import DailyBudget
from flexrouter.engine import RoutingEngine, RouteResult
from flexrouter.rate_limits import RateLimitStore


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


def test_seconds_until_available_measures_penalty_not_epoch():
    """Regression test: seconds_until_available must return "seconds
    remaining", not an epoch timestamp.

    PenaltyBox.penalty_until() returns a wall-clock (time.time()-based)
    timestamp. If seconds_until_available() ever subtracts time.monotonic()
    (a different, arbitrary-reference clock) from that timestamp instead of
    time.time(), the result comes out around the epoch itself (~1.7 billion
    seconds) rather than a small number of seconds — which the old test's
    `secs >= 0` assertion doesn't catch, since an epoch-scale value is still
    >= 0.
    """
    engine = make_engine()
    engine._penalties.penalize_short("groq", "llama-8b", seconds=5)
    engine._penalties.penalize_short("groq", "llama-70b", seconds=5)
    secs = engine.seconds_until_available("low")
    assert secs < 120


def test_make_result_empty_api_keys_uses_empty_string():
    """Ollama and other local providers have no api_keys."""
    cfg = FlexConfig(
        tiers={"default": [ModelConfig(provider="ollama", model="llama3", score=50, rpm=600, tpm=10_000_000)]},
        providers={"ollama": ProviderConfig(base_url="http://localhost:11434/v1", api_keys=[])},
        retry=RetryConfig(),
    )
    engine = RoutingEngine(cfg)
    result = engine._make_result(cfg.tiers["default"][0], "default")
    assert result.api_key == ""


def test_score_candidates_uses_learned_rpm(tmp_path):
    """Engine uses RateLimitStore rpm over ModelConfig.rpm when available."""
    store = RateLimitStore(str(tmp_path))
    # Model config says rpm=30 but store says rpm=5 (very low)
    store.update("groq", "llama-8b", rpm=5, tpm=None)

    cfg = FlexConfig(
        tiers={"default": [ModelConfig(provider="groq", model="llama-8b", score=80, rpm=30, tpm=6000)]},
        providers={"groq": ProviderConfig(base_url="https://api.groq.com/openai/v1", api_keys=["key"])},
        retry=RetryConfig(),
    )
    engine = RoutingEngine(cfg, rate_limit_store=store)

    # Saturate the window at learned rpm=5 (not config rpm=30)
    window = engine._windows["groq/llama-8b"]
    for _ in range(5):
        window.record(0)

    # With learned rpm=5, window should be full; with config rpm=30, it wouldn't be
    candidates = engine._score_candidates(cfg.tiers["default"], 0, False)
    assert candidates == []


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


def test_exhausted_model_skipped(tmp_path):
    import time
    from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
    from flexrouter.engine import RoutingEngine
    from flexrouter.rate_limits import RateLimitStore
    store = RateLimitStore(str(tmp_path))
    store.update_headroom("groq", "llama", remaining_requests=0, reset_requests_at=time.time() + 60)
    cfg = FlexConfig(
        tiers={"default": [ModelConfig("groq", "llama", 50, 30, 6000)]},
        providers={"groq": ProviderConfig(base_url="http://x", api_keys=["k"])},
    )
    eng = RoutingEngine(cfg, rate_limit_store=store)
    assert eng.select("default", 10, False) is None  # only model is exhausted


def test_score_candidates_skips_quota_exhausted_model():
    from flexrouter.quota import QuotaTracker
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        tracker = QuotaTracker(d)
        tracker.record("groq", "m1")
        m1 = ModelConfig(provider="groq", model="m1", score=90, rpm=100, tpm=100000, quotas={"rpd": 1})
        m2 = ModelConfig(provider="groq", model="m2", score=50, rpm=100, tpm=100000)
        cfg = FlexConfig(
            tiers={"low": [m1, m2]},
            providers={"groq": ProviderConfig("http://groq", ["key"])},
        )
        engine = RoutingEngine(cfg, quota_tracker=tracker)
        scored = engine._score_candidates([m1, m2], estimated_tokens=0, vision=False)
        chosen_models = [m.model for _, m in scored]
        assert "m1" not in chosen_models
        assert "m2" in chosen_models
