import pytest
from flexrouter.budget import DailyBudget

def test_no_budget_always_available():
    db = DailyBudget(limits={})
    assert db.is_available("openai") is True

def test_under_budget_available():
    db = DailyBudget(limits={"openai": 5.00})
    db.record("openai", cost_usd=1.00)
    assert db.is_available("openai") is True

def test_over_budget_unavailable():
    db = DailyBudget(limits={"openai": 5.00})
    db.record("openai", cost_usd=5.01)
    assert db.is_available("openai") is False

def test_exact_budget_unavailable():
    db = DailyBudget(limits={"openai": 5.00})
    db.record("openai", cost_usd=5.00)
    assert db.is_available("openai") is False

def test_daily_spend_resets_at_midnight():
    db = DailyBudget(limits={"openai": 5.00})
    db.record("openai", cost_usd=5.01)
    import datetime
    db._day["openai"] = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    assert db.is_available("openai") is True

def test_provider_without_limit_always_available():
    db = DailyBudget(limits={"openai": 5.00})
    assert db.is_available("groq") is True

def test_total_spent_tracks_correctly():
    db = DailyBudget(limits={})
    db.record("openai", cost_usd=1.50)
    db.record("openai", cost_usd=0.50)
    assert db.daily_spend("openai") == pytest.approx(2.00)
