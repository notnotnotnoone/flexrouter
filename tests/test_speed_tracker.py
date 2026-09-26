from flexrouter.speed import SpeedTracker


def test_a_fresh_model_has_no_median_and_no_samples():
    tracker = SpeedTracker()
    assert tracker.median_ms("groq", "llama") is None
    assert tracker.sample_count("groq", "llama") == 0


def test_a_single_sample_is_its_own_median():
    tracker = SpeedTracker()
    tracker.record("groq", "llama", 420.0)
    assert tracker.median_ms("groq", "llama") == 420.0
    assert tracker.sample_count("groq", "llama") == 1


def test_the_median_ignores_a_stale_sample_past_the_cap():
    tracker = SpeedTracker(max_samples=3)
    for ms in (900.0, 100.0, 200.0, 300.0):  # the leading 900 falls off
        tracker.record("groq", "llama", ms)
    assert tracker.sample_count("groq", "llama") == 3
    assert tracker.median_ms("groq", "llama") == 200.0


def test_models_are_tracked_independently():
    tracker = SpeedTracker()
    tracker.record("groq", "llama", 100.0)
    tracker.record("openai", "gpt", 500.0)
    assert tracker.median_ms("groq", "llama") == 100.0
    assert tracker.median_ms("openai", "gpt") == 500.0
