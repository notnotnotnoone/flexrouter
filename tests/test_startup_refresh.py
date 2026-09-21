import json

from flexrouter._router import LocalRouter
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def test_startup_calls_refresh_and_writes_catalog_pending(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    calls = []

    def fake_refresh_config(config_path, state_dir, aa_key=None):
        calls.append((config_path, state_dir))
        from flexrouter.refresh import RefreshResult
        return RefreshResult(timestamp="2026-09-21T00:00:00Z", added=[], removed=[],
                             changed=[], provider_errors=[], pending_path=None)

    monkeypatch.setattr("flexrouter._router.refresh_config", fake_refresh_config)
    router = LocalRouter(str(tmp_path / "config.yaml"))
    router.close()

    assert len(calls) == 1
    assert calls[0][1] == str(tmp_path / "state")


def test_startup_refresh_records_model_facts_for_appeared_models(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    def fake_refresh_config(config_path, state_dir, aa_key=None):
        from flexrouter.refresh import RefreshResult
        # Write the same shape refresh_config itself would, so record_discovered
        # has something real to read.
        import os
        pending = {"alpha": {"checked_at": "2026-09-21T00:00:00Z",
                             "appeared": [{"model": "new-model", "context_window": 131072,
                                          "rpm": None, "tpm": None, "score": 50, "free": True}],
                             "vanished": [], "changed": []}}
        os.makedirs(state_dir, exist_ok=True)
        with open(os.path.join(state_dir, "catalog_pending.json"), "w", encoding="utf-8") as f:
            json.dump(pending, f)
        return RefreshResult(timestamp="2026-09-21T00:00:00Z", added=["alpha/new-model"],
                             removed=[], changed=[], provider_errors=[], pending_path=None)

    monkeypatch.setattr("flexrouter._router.refresh_config", fake_refresh_config)
    router = LocalRouter(str(tmp_path / "config.yaml"))
    router.close()

    facts = router._model_facts.get("alpha", "new-model")
    assert facts.context.value == 131072
    assert facts.context.source == "published"


def test_a_refresh_failure_at_startup_does_not_prevent_the_service_from_starting(
        tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    def broken_refresh_config(config_path, state_dir, aa_key=None):
        raise RuntimeError("state directory briefly unwritable")

    monkeypatch.setattr("flexrouter._router.refresh_config", broken_refresh_config)
    router = LocalRouter(str(tmp_path / "config.yaml"))  # must not raise
    router.close()
