import time
import warnings
import pytest
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig, RetryConfig
from flexrouter.window import SlidingWindow
from flexrouter.budget import DailyBudget
from flexrouter.engine import RoutingEngine, RouteResult
from flexrouter.rate_limits import RateLimitStore


def make_engine(models=None, bucket_strategy=None):
    if models is None:
        models = [
            ModelConfig("groq", "llama-8b", score=85, rpm=60, tpm=60000, context_window=131072),
            ModelConfig("groq", "llama-70b", score=60, rpm=30, tpm=30000, context_window=131072),
        ]
    cfg = FlexConfig(
        tiers={"low": models},
        providers={"groq": ProviderConfig("http://groq", ["key"])},
        bucket_strategy=bucket_strategy or {},
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


def test_skips_a_busy_model():
    engine = make_engine()
    engine._status.set_busy("groq", "llama-8b", 60, "Too many requests")
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
    engine._status.set_busy("groq", "llama-8b", 60, "Too many requests")
    engine._status.set_busy("groq", "llama-70b", 60, "Too many requests")
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
    engine._status.set_busy("groq", "llama-8b", 60, "Too many requests")
    engine._status.set_busy("groq", "llama-70b", 60, "Too many requests")
    secs = engine.seconds_until_available("low")
    assert secs >= 0


def test_seconds_until_available_measures_penalty_not_epoch():
    """Regression test: seconds_until_available must return "seconds
    remaining", not an epoch timestamp.

    A status's `until` is a wall-clock (time.time()-based)
    timestamp. If seconds_until_available() ever subtracts time.monotonic()
    (a different, arbitrary-reference clock) from that timestamp instead of
    time.time(), the result comes out around the epoch itself (~1.7 billion
    seconds) rather than a small number of seconds — which the old test's
    `secs >= 0` assertion doesn't catch, since an epoch-scale value is still
    >= 0.
    """
    engine = make_engine()
    engine._status.set_busy("groq", "llama-8b", 5, "Too many requests")
    engine._status.set_busy("groq", "llama-70b", 5, "Too many requests")
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


# ── "fastest" bucket strategy ───────────────────────────────────────────

def test_default_strategy_is_smartest_ranks_by_score():
    engine = make_engine()
    result = engine.select("low", estimated_tokens=0, vision=False)
    assert result.model == "llama-8b"  # higher score (85 > 60)


def test_fastest_strategy_ranks_by_tokens_per_second_not_score():
    models = [
        ModelConfig("groq", "smart-slow", score=95, rpm=60, tpm=60000, tokens_per_second=20),
        ModelConfig("groq", "dumb-fast", score=10, rpm=60, tpm=60000, tokens_per_second=500),
    ]
    engine = make_engine(models, bucket_strategy={"low": "fastest"})
    result = engine.select("low", estimated_tokens=0, vision=False)
    assert result.model == "dumb-fast"


def test_fastest_strategy_picks_within_top_20_percent_of_speed():
    models = [
        ModelConfig("groq", "fastest", score=50, rpm=60, tpm=60000, tokens_per_second=100),
        ModelConfig("groq", "close-second", score=50, rpm=60, tpm=60000, tokens_per_second=85),
        ModelConfig("groq", "too-slow", score=50, rpm=60, tpm=60000, tokens_per_second=50),
    ]
    engine = make_engine(models, bucket_strategy={"low": "fastest"})
    seen = {engine.select("low", estimated_tokens=0, vision=False).model for _ in range(50)}
    assert seen == {"fastest", "close-second"}


def test_fastest_strategy_tries_a_model_with_no_speed_data():
    """grill-decisions.md §3/§14: an unmeasured model is tried, not skipped."""
    models = [
        ModelConfig("groq", "has-speed", score=50, rpm=60, tpm=60000, tokens_per_second=100),
        ModelConfig("groq", "no-speed", score=90, rpm=60, tpm=60000),
    ]
    engine = make_engine(models, bucket_strategy={"low": "fastest"})
    seen = {engine.select("low", estimated_tokens=0, vision=False).model for _ in range(50)}
    assert "no-speed" in seen


def test_fastest_strategy_with_no_speed_data_anywhere_still_answers():
    models = [ModelConfig("groq", "no-speed", score=90, rpm=60, tpm=60000)]
    engine = make_engine(models, bucket_strategy={"low": "fastest"})
    assert engine.select("low", estimated_tokens=0, vision=False).model == "no-speed"


def test_explain_unavailable_has_no_speed_data_reason_any_more():
    models = [ModelConfig("groq", "no-speed", score=90, rpm=60, tpm=60000)]
    engine = make_engine(models, bucket_strategy={"low": "fastest"})
    out = engine.explain_unavailable("low")
    assert out[0]["available"] is True


def test_a_model_whose_provider_is_not_set_up_needs_you():
    models = [ModelConfig("zhipu", "glm-5-2", score=90, rpm=60, tpm=60000)]
    engine = make_engine(models)
    out = engine.explain_unavailable("low")
    assert out[0]["reason"] == "needs_you"
    assert "No zhipu provider set up" in out[0]["detail"]
    assert engine.select("low", estimated_tokens=0, vision=False) is None
