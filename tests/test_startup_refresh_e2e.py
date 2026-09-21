from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def test_starting_the_real_service_leaves_a_catalog_pending_file(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    client = TestClient(create_app(str(tmp_path / "config.yaml")))
    # Any request forces the router to be constructed, which runs the
    # startup refresh — this proves the wiring end to end through the real
    # service entry point, not just by constructing LocalRouter directly.
    client.get("/v1/models")

    assert (tmp_path / "state" / "catalog_pending.json").exists()
