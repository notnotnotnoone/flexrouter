import json
import time

from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.decider import ErrorVerdict
from flexrouter.exceptions import RouterError


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _router(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


def _traces(tmp_path):
    p = tmp_path / "state" / "traces.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]


def test_a_404_is_classified_as_model_gone_in_the_trace(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_404(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: model archived", status_code=404)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_404)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "model_gone"


def test_an_auth_failure_is_classified_as_bad_key(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_401(self, route, messages, **kwargs):
        raise RouterError("Auth failure for provider 'alpha': 401")

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_401)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "bad_key"


def test_a_429_is_classified_as_too_fast(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)
    calls = {"n": 0}

    from flexrouter.client import RateLimitError

    async def rate_limited_then_ok(self, route, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimitError("429 from alpha/big")
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", rate_limited_then_ok)
    router.generate([{"role": "user", "content": "hi"}], "smart")

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "too_fast"


def test_an_unrecognized_status_falls_back_to_unknown(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def status_418(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: I'm a teapot", status_code=418)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", status_418)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "unknown"


class _FakeDecider:
    configured = True

    def __init__(self, verdict, confidence=0.9):
        self._v = ErrorVerdict(verdict=verdict, source="fake", confidence=confidence)

    def classify_error(self, text, status):
        return self._v

    def describe_model(self, *a, **kw):
        return {}


class _SlowDecider:
    configured = True

    def __init__(self, seconds):
        self._seconds = seconds

    def classify_error(self, text, status):
        time.sleep(self._seconds)
        return ErrorVerdict(verdict="unknown", source="slow", confidence=0.9)

    def describe_model(self, *a, **kw):
        return {}


def test_a_bare_400_classified_as_bad_request_returns_to_the_caller(tmp_path, monkeypatch):
    """§4 overturns ADR 0013: a bare 400 is no longer bad_request at 1.0
    unconditionally - but a confident bad_request verdict still returns."""
    router = _router(tmp_path, monkeypatch)
    router._error_brain._decider = _FakeDecider("bad_request")

    async def always_400(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: malformed request", status_code=400)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_400)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    status = router._status.get("alpha", "big")
    assert status.value == "ready"  # no penalty for the caller's own mistake


def test_a_bare_400_classified_as_model_gone_offers_did_you_mean(tmp_path, monkeypatch):
    """§3/§4: model_gone from ANY status (not just a literal 404) checks
    the live catalogue and offers [Use it] on a close match."""
    from flexrouter.catalogue import KNOWN_MODEL_IDS_FILENAME
    from flexrouter.store import write_json
    from pathlib import Path

    router = _router(tmp_path, monkeypatch)
    router._error_brain._decider = _FakeDecider("model_gone")
    write_json(Path(router._cfg.state_dir) / KNOWN_MODEL_IDS_FILENAME,
              {"alpha": {"checked_at": "now", "ids": ["big-v2"]}})

    async def always_400(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: no such model", status_code=400)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_400)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    status = router._status.get("alpha", "big")
    assert status.value == "needs_you"
    assert status.action == "use:big-v2"


def test_a_bad_key_verdict_on_a_400_is_needs_you_on_the_key_not_the_model(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)
    router._error_brain._decider = _FakeDecider("bad_key")

    async def always_400(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: unauthorized", status_code=400)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_400)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    # §4: bad_key goes through the same path a real 401/403 does - which,
    # with the provider's only key now rejected, marks the whole provider
    # (not just this one model) needs you. record_failure() taking the
    # model-only path would never touch the provider wildcard entry.
    from flexrouter.status import PROVIDER_WILDCARD
    assert router._status.get("alpha", PROVIDER_WILDCARD).value == "needs_you"


def test_a_slow_decider_never_blocks_a_request_more_than_the_jev_timeout(tmp_path, monkeypatch):
    """Done when: 'A slow or down JEV never blocks for more than 0.5s.'"""
    router = _router(tmp_path, monkeypatch)
    router._error_brain._decider = _SlowDecider(seconds=5.0)

    async def always_400(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: something new", status_code=400)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_400)
    started = time.monotonic()
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass
    elapsed = time.monotonic() - started

    assert elapsed < 2.0  # nowhere near the slow decider's real 5s


def test_error_brain_state_file_is_written(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_402(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: payment required", status_code=402)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_402)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    assert (tmp_path / "state" / "error_brain.json").exists()
