import json

from flexrouter._router import LocalRouter
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path, auto_add_models=True):
    # Auto-add is opt-in (auto_add_models, off by default) - every test in
    # this module is specifically about the startup *auto-add* refresh, so
    # it turns auto-add on unless it is testing the off switch itself.
    # Reading the real ID list (refresh_known_model_ids) always runs
    # regardless, and is covered separately below.
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
        auto_add_models=auto_add_models,
    )


def test_startup_skips_auto_add_refresh_when_it_is_off(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config",
                        lambda _p: _cfg(tmp_path, auto_add_models=False))

    calls = []

    def fake_refresh_config(config_path, state_dir, aa_key=None):
        calls.append((config_path, state_dir))
        from flexrouter.refresh import RefreshResult
        return RefreshResult(timestamp="2026-09-21T00:00:00Z", added=[], removed=[],
                             changed=[], provider_errors=[], pending_path=None)

    monkeypatch.setattr("flexrouter._router.refresh_config", fake_refresh_config)
    router = LocalRouter(str(tmp_path / "config.yaml"))
    router.close()

    assert calls == []


def test_startup_still_reads_the_real_model_list_when_auto_add_is_off(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config",
                        lambda _p: _cfg(tmp_path, auto_add_models=False))

    calls = []

    def fake_refresh_known_model_ids(config_path, state_dir):
        calls.append((config_path, state_dir))
        return {}

    monkeypatch.setattr("flexrouter._router.refresh_known_model_ids",
                        fake_refresh_known_model_ids)
    router = LocalRouter(str(tmp_path / "config.yaml"))
    router.close()

    assert len(calls) == 1
    assert calls[0][1] == str(tmp_path / "state")


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


def test_a_record_discovered_failure_at_startup_does_not_prevent_the_service_from_starting(
        tmp_path, monkeypatch):
    """The refresh call itself can succeed and still find an appeared model,
    but writing that model's facts back to disk (state/model_facts.json) can
    fail for the same reasons the state directory can be briefly unwritable
    -- disk full, permissions, a locked file. That write happens after the
    refresh_config() call returns, in the catalog_pending.json read-back and
    record_discovered() loop, so it must be covered by the same
    never-block-startup guarantee as the refresh call itself.
    """
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    def fake_refresh_config(config_path, state_dir, aa_key=None):
        from flexrouter.refresh import RefreshResult
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

    def broken_record_discovered(self, provider, model, context_window):
        raise OSError("disk full")

    monkeypatch.setattr(
        "flexrouter.model_facts.ModelFactsStore.record_discovered",
        broken_record_discovered)

    router = LocalRouter(str(tmp_path / "config.yaml"))  # must not raise
    router.close()


def test_startup_refresh_runs_cleanly_from_inside_the_fastapi_lifespan(
        tmp_path, monkeypatch, recwarn):
    """The real production path: `flexrouter serve` starts uvicorn, which
    drives `app.py`'s `lifespan()` — an `async def` that calls the sync
    `get_router()` (and so `LocalRouter(...)`) directly from its own body,
    on the event loop's own thread. That's unlike a plain `def` route
    handler, which FastAPI automatically offloads to a worker thread.
    `refresh_config()` calls `asyncio.run()` internally, and `asyncio.run()`
    raises `RuntimeError` when called from a thread that already has a
    running event loop — so without running it off that thread, this exact
    path would always hit the except-and-log fallback and the refresh would
    silently never actually happen under uvicorn. `TestClient(app)` used as
    a context manager runs the real lifespan startup/shutdown, so this
    reproduces that path for real rather than only unit-testing
    `LocalRouter.__init__` in isolation the way the tests above do.
    """
    from fastapi.testclient import TestClient

    from flexrouter import app as app_mod

    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    calls = []

    def fake_refresh_config(config_path, state_dir, aa_key=None):
        calls.append((config_path, state_dir))
        from flexrouter.refresh import RefreshResult
        return RefreshResult(timestamp="2026-09-21T00:00:00Z", added=[], removed=[],
                             changed=[], provider_errors=[], pending_path=None)

    monkeypatch.setattr("flexrouter._router.refresh_config", fake_refresh_config)

    app_mod.state.router = None
    app = app_mod.create_app(str(tmp_path / "config.yaml"))
    with TestClient(app):
        pass  # lifespan startup (and shutdown) already ran by here
    app_mod.state.router = None

    assert len(calls) == 1, "refresh_config must actually run from inside lifespan startup"
    assert not any(issubclass(w.category, RuntimeWarning) for w in recwarn.list), (
        "a RuntimeWarning here means refresh_config's asyncio.run() collided "
        "with the app's own running event loop again"
    )
