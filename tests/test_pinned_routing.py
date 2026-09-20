import pytest

from flexrouter._router import LocalRouter
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={
            "smart": [
                ModelConfig(provider="alpha", model="big", score=99, rpm=60, tpm=60000),
                ModelConfig(provider="beta", model="small", score=40, rpm=60, tpm=60000),
            ],
        },
        providers={
            "alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"]),
            "beta": ProviderConfig(base_url="https://beta.test/v1", api_keys=["k"]),
        },
        state_dir=str(tmp_path / "state"),
    )


def _built_router(tmp_path, monkeypatch):
    """A real router over the fixture config, with no file on disk."""
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


def test_pin_engine_has_one_bucket_per_model(tmp_path, monkeypatch):
    router = _built_router(tmp_path, monkeypatch)
    assert set(router._pin_engine._cfg.tiers) == {"alpha/big", "beta/small"}
    assert [mc.model for mc in router._pin_engine._cfg.tiers["alpha/big"]] == ["big"]


def test_engine_for_picks_the_pin_engine_only_for_slashed_names(tmp_path, monkeypatch):
    router = _built_router(tmp_path, monkeypatch)
    assert router._engine_for("smart") is router._engine
    assert router._engine_for("alpha/big") is router._pin_engine


def test_pinned_selection_always_returns_that_model(tmp_path, monkeypatch):
    router = _built_router(tmp_path, monkeypatch)
    for _ in range(20):
        route = router._engine_for("beta/small").select("beta/small", 10, False)
        assert (route.provider, route.model) == ("beta", "small")


def test_pin_and_bucket_share_one_penalty_box(tmp_path, monkeypatch):
    router = _built_router(tmp_path, monkeypatch)
    assert router._pin_engine._penalties is router._engine._penalties
    assert router._pin_engine._cfg is not router._engine._cfg


def test_a_quarantine_through_one_engine_is_seen_by_the_other(tmp_path, monkeypatch):
    router = _built_router(tmp_path, monkeypatch)
    router._penalties.quarantine("beta", "small", "gone")
    assert router._pin_engine.select("beta/small", 10, False) is None


def test_unknown_pin_raises_keyerror(tmp_path, monkeypatch):
    router = _built_router(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        router._engine_for("alpha/nope").select("alpha/nope", 10, False)


def test_reload_rebuilds_the_pin_engine(tmp_path, monkeypatch):
    router = _built_router(tmp_path, monkeypatch)
    new = _cfg(tmp_path)
    new.tiers["smart"].append(ModelConfig(provider="alpha", model="extra", score=40, rpm=60, tpm=60000))
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: new)
    router.reload()
    assert "alpha/extra" in router._pin_engine._cfg.tiers
