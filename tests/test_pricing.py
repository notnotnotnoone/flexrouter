# tests/test_pricing.py
"""Prices are a setting the owner types, and `cost_usd` follows from them.

Most traffic through flexrouter is on a free tier, but not all of it - the
decider is a paid model - so the router has to be able to say what something
cost, and to distinguish "free" from "nobody has priced this".
"""
import pytest
import yaml

from flexrouter.config import load_config
from flexrouter.overrides import ALLOWED_FIELDS, check_fields


def _config(tmp_path, model_extra=None):
    cfg = {
        "tiers": {"low": [dict({
            "provider": "groq", "model": "llama", "score": 80,
            "rpm": 60, "tpm": 60000, "context_window": 131072,
        }, **(model_extra or {}))]},
        "providers": {"groq": {"base_url": "https://x/v1",
                               "api_keys": [{"env": "GROQ_API_KEY"}]}},
        "settings": {"state_dir": str(tmp_path / ".flexrouter")},
    }
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def _model(tmp_path, **extra):
    return load_config(_config(tmp_path, extra)).tiers["low"][0]


def test_a_model_with_no_price_is_unpriced_not_free(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    m = _model(tmp_path)
    assert m.price_in is None and m.price_out is None


def test_prices_are_read_from_the_settings_file(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    m = _model(tmp_path, price_in=0.59, price_out=0.79)
    assert m.price_in == pytest.approx(0.59)
    assert m.price_out == pytest.approx(0.79)


def test_a_cleared_price_field_means_unpriced(monkeypatch, tmp_path):
    # An empty string is what the dashboard's form sends for "clear this".
    monkeypatch.setenv("GROQ_API_KEY", "k")
    m = _model(tmp_path, price_in="", price_out="")
    assert m.price_in is None and m.price_out is None


def test_a_nonsense_price_costs_a_figure_not_the_router(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    m = _model(tmp_path, price_in="free", price_out=-3)
    assert m.price_in is None and m.price_out is None


def test_the_dashboard_may_set_prices():
    assert "price_in" in ALLOWED_FIELDS["models"]
    assert "price_out" in ALLOWED_FIELDS["models"]
    check_fields("models", {"price_in": 0.5, "price_out": 1.5})


# ── what an attempt costs ──────────────────────────────────────────────

class _Router:
    """Just enough of LocalRouter to exercise the two pricing methods."""
    def __init__(self, cfg):
        self._cfg = cfg
    _price_of = None  # bound below


def _router_for(cfg):
    from flexrouter._router import LocalRouter
    r = _Router(cfg)
    r._price_of = LocalRouter._price_of.__get__(r)
    r._cost_of = LocalRouter._cost_of.__get__(r)
    return r


def test_an_unpriced_model_costs_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    cfg = load_config(_config(tmp_path))
    r = _router_for(cfg)
    assert r._cost_of("groq", "llama", 1_000_000, 1_000_000) == 0.0


def test_cost_is_per_million_tokens_each_way(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    cfg = load_config(_config(tmp_path, {"price_in": 1.0, "price_out": 3.0}))
    r = _router_for(cfg)
    # Half a million in, a quarter million out.
    assert r._cost_of("groq", "llama", 500_000, 250_000) == pytest.approx(0.5 + 0.75)


def test_only_one_side_priced_still_charges_that_side(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    cfg = load_config(_config(tmp_path, {"price_out": 2.0}))
    r = _router_for(cfg)
    assert r._cost_of("groq", "llama", 1_000_000, 1_000_000) == pytest.approx(2.0)


def test_a_model_the_router_does_not_know_costs_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    cfg = load_config(_config(tmp_path, {"price_in": 5.0}))
    r = _router_for(cfg)
    assert r._cost_of("elsewhere", "mystery", 1_000_000, 0) == 0.0
