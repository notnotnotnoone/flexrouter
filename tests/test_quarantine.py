"""Permanent provider failures must not be retried forever.

Before this, every failure was treated as temporary: penalize 30s, recover,
retry, fail, repeat. A model the provider has deleted answers 404 every time,
so that loop never terminated — the real audit log showed 239 penalties
against 235 recoveries with no progress.
"""
import asyncio
import csv
import time

import httpx
import pytest
import respx

from flexrouter import LocalRouter, RouterBusy
from flexrouter.client import ProviderError
from flexrouter.recovery import PenaltyBox

CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.1-8b-instant"


# --- ProviderError classification -------------------------------------------

@pytest.mark.parametrize("status,permanent", [
    (404, True),    # model deleted
    (410, True),    # model retired
    (500, False),   # provider having a bad day
    (503, False),
    (400, False),   # ambiguous — could be our malformed request, don't sideline
])
def test_permanence_classification(status, permanent):
    assert ProviderError("boom", status_code=status).is_permanent is permanent


def test_error_without_status_is_not_permanent():
    # Connection resets and timeouts arrive with no status at all.
    assert ProviderError("connection reset").is_permanent is False


# --- PenaltyBox quarantine ---------------------------------------------------

def test_quarantine_makes_route_unavailable():
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.quarantine("groq", MODEL, "404 model not found")
    assert pb.is_quarantined("groq", MODEL) is True
    # Routing skips anything is_penalized() reports, so this must cover it.
    assert pb.is_penalized("groq", MODEL) is True


def test_quarantine_records_reason():
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.quarantine("groq", MODEL, "404 from groq: no such model")
    assert "no such model" in pb.quarantine_reason("groq", MODEL)


def test_quarantine_expires():
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.quarantine("groq", MODEL, "gone", seconds=-1)
    assert pb.is_quarantined("groq", MODEL) is False
    assert pb.is_penalized("groq", MODEL) is False


def test_quarantine_can_be_cleared():
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.quarantine("groq", MODEL, "gone")
    pb.clear_quarantine("groq", MODEL)
    assert pb.is_quarantined("groq", MODEL) is False


def test_quarantine_survives_restart(tmp_path):
    state = str(tmp_path)
    PenaltyBox(base_seconds=30, max_seconds=1800, state_dir=state).quarantine(
        "groq", MODEL, "404 model not found")
    revived = PenaltyBox(base_seconds=30, max_seconds=1800, state_dir=state)
    assert revived.is_quarantined("groq", MODEL) is True
    assert "404" in revived.quarantine_reason("groq", MODEL)


def test_expired_quarantine_not_reloaded(tmp_path):
    state = str(tmp_path)
    pb = PenaltyBox(base_seconds=30, max_seconds=1800, state_dir=state)
    pb.quarantine("groq", MODEL, "gone", seconds=-1)
    revived = PenaltyBox(base_seconds=30, max_seconds=1800, state_dir=state)
    assert revived.is_quarantined("groq", MODEL) is False


def test_quarantined_listing_excludes_expired():
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.quarantine("groq", "alive-but-gone", "404")
    pb.quarantine("groq", "already-back", "404", seconds=-1)
    listed = pb.quarantined()
    assert "groq/alive-but-gone" in listed
    assert "groq/already-back" not in listed


def test_quarantine_is_separate_from_penalty_backoff():
    # A quarantine must not inflate the exponential backoff counter, or a
    # route that heals comes back with an absurd penalty.
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.quarantine("groq", MODEL, "404")
    assert pb.penalty_seconds("groq", MODEL) == 0


# --- End-to-end through the router ------------------------------------------

def _events(config_file):
    import yaml
    state = yaml.safe_load(config_file.read_text())["settings"]["state_dir"]
    from pathlib import Path
    p = Path(state) / "events.csv"
    if not p.exists():
        return []
    with p.open() as f:
        return list(csv.DictReader(f))


@respx.mock
def test_404_quarantines_the_model(config_file):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(
        404, json={"error": {"message": "model not found"}}))
    router = LocalRouter(str(config_file))
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)
    assert router._engine._penalties.is_quarantined("groq", MODEL) is True


@respx.mock
def test_500_penalizes_but_does_not_quarantine(config_file):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500, text="upstream exploded"))
    router = LocalRouter(str(config_file))
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)
    pens = router._engine._penalties
    assert pens.is_quarantined("groq", MODEL) is False
    assert pens.is_penalized("groq", MODEL) is True


@respx.mock
def test_provider_message_is_recorded_not_discarded(config_file):
    # The whole point: 239 failures in the real log carried zero explanation.
    respx.post(CHAT_URL).mock(return_value=httpx.Response(
        500, text="upstream exploded spectacularly"))
    router = LocalRouter(str(config_file))
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)

    errors = [e for e in _events(config_file) if e["event_type"] == "server_error"]
    assert errors, "no server_error event recorded"
    assert all(e["detail"].strip() for e in errors), \
        f"server_error logged with empty detail: {errors}"
    assert any("500" in e["detail"] for e in errors)


@respx.mock
def test_rate_limit_message_is_recorded(config_file):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(429, json={"error": "slow down"}))
    router = LocalRouter(str(config_file))
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)

    limited = [e for e in _events(config_file) if e["event_type"] == "rate_limited"]
    assert limited, "no rate_limited event recorded"
    assert all(e["detail"].strip() for e in limited), \
        f"rate_limited logged with empty detail: {limited}"


@respx.mock
def test_quarantine_event_is_emitted(config_file):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(404, text="no such model"))
    router = LocalRouter(str(config_file))
    with pytest.raises(RouterBusy):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)

    evs = _events(config_file)
    assert any(e["event_type"] == "quarantined" for e in evs), \
        f"expected a quarantined event, got {[e['event_type'] for e in evs]}"
    detail = " ".join(e["detail"] for e in evs if e["event_type"] == "server_error")
    assert "quarantined" in detail


# --- auth failures sideline the provider, not the request -------------------

TWO_PROVIDER_CONFIG = {
    "tiers": {
        "low": [
            {"provider": "groq", "model": "llama-3.1-8b-instant", "score": 99,
             "rpm": 60, "tpm": 60000, "context_window": 131072},
            {"provider": "cerebras", "model": "gpt-oss-120b", "score": 40,
             "rpm": 60, "tpm": 60000, "context_window": 131072},
        ]
    },
    "providers": {
        "groq": {"base_url": "https://api.groq.com/openai/v1",
                 "api_keys": [{"key": "dead-key"}]},
        "cerebras": {"base_url": "https://api.cerebras.ai/v1",
                     "api_keys": [{"key": "good-key"}]},
    },
    "settings": {"state_dir": "", "window_seconds": 60,
                 "penalty_base_seconds": 30, "penalty_max_seconds": 1800,
                 "session_ttl_minutes": 30, "dashboard_port": 7352,
                 "retry_policy": "balanced"},
}

OK_RESPONSE = {
    "choices": [{"message": {"role": "assistant", "content": "hello"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}


@pytest.fixture
def two_provider_config(tmp_path):
    import copy
    import yaml
    cfg = copy.deepcopy(TWO_PROVIDER_CONFIG)
    cfg["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def test_provider_quarantine_covers_all_its_models():
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.quarantine_provider("groq", "401 Auth failure")
    # Any model on that provider, including ones never seen before.
    assert pb.is_quarantined("groq", "llama-3.1-8b-instant") is True
    assert pb.is_quarantined("groq", "some-model-added-tomorrow") is True
    assert pb.is_quarantined("cerebras", "gpt-oss-120b") is False


def test_provider_quarantine_reason_is_reported_per_model():
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.quarantine_provider("groq", "401 Auth failure")
    assert "401" in pb.quarantine_reason("groq", "any-model")


def test_provider_quarantine_survives_restart(tmp_path):
    state = str(tmp_path)
    PenaltyBox(base_seconds=30, max_seconds=1800,
               state_dir=state).quarantine_provider("groq", "401")
    revived = PenaltyBox(base_seconds=30, max_seconds=1800, state_dir=state)
    assert revived.is_quarantined("groq", "whatever") is True


@respx.mock
def test_auth_failure_rotates_to_a_healthy_provider(two_provider_config):
    """One dead key must not take down a tier that has a working provider.

    This is the reported "flexrouter is really broken": an expired Groq key
    aborted every request outright, even though other providers in the same
    tier answered fine.
    """
    respx.post(CHAT_URL).mock(return_value=httpx.Response(
        401, json={"error": {"message": "Invalid API Key"}}))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE))

    router = LocalRouter(str(two_provider_config))
    result = router.generate([{"role": "user", "content": "hi"}], tier="low")

    assert result["choices"][0]["message"]["content"] == "hello"
    assert router._engine._penalties.is_quarantined("groq", "llama-3.1-8b-instant")
    assert not router._engine._penalties.is_quarantined("cerebras", "gpt-oss-120b")


@respx.mock
def test_auth_failure_is_recorded_with_its_reason(two_provider_config):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(401, json={"error": "nope"}))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE))

    router = LocalRouter(str(two_provider_config))
    router.generate([{"role": "user", "content": "hi"}], tier="low")

    import yaml
    from pathlib import Path
    state = yaml.safe_load(two_provider_config.read_text())["settings"]["state_dir"]
    with (Path(state) / "events.csv").open() as f:
        rows = list(csv.DictReader(f))
    auth = [r for r in rows if "auth" in r["detail"].lower()]
    assert auth, f"auth failure not explained in events: {rows}"
    assert any("401" in r["detail"] for r in auth)


@respx.mock
def test_every_provider_dead_still_raises_a_clear_auth_error(two_provider_config):
    # When nothing is left, the caller should learn it's a credentials
    # problem — not a vague "all retries exhausted".
    respx.post(CHAT_URL).mock(return_value=httpx.Response(401, json={"error": "nope"}))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": "nope"}))

    from flexrouter.exceptions import RouterError
    router = LocalRouter(str(two_provider_config))
    with pytest.raises(RouterError, match="Auth failure"):
        router.generate([{"role": "user", "content": "hi"}], tier="low", wait=False)


def test_402_quarantines_the_whole_provider():
    # Seen live: cerebras answered 402 "Payment required" and the router
    # backed off 30s, 60s, 120s, 240s, 480s against the same model — a
    # billing problem doesn't resolve on a timer.
    assert ProviderError("nope", status_code=402).is_provider_wide is True
    assert ProviderError("nope", status_code=500).is_provider_wide is False


@respx.mock
def test_payment_required_sidelines_provider_and_rotates(two_provider_config):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(
        402, json={"message": "Payment required to access this resource."}))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OK_RESPONSE))

    router = LocalRouter(str(two_provider_config))
    result = router.generate([{"role": "user", "content": "hi"}], tier="low")

    assert result["choices"][0]["message"]["content"] == "hello"
    pens = router._engine._penalties
    assert pens.is_quarantined("groq", "any-model-at-all") is True
    assert "Payment required" in pens.quarantine_reason("groq", "whatever")


@respx.mock
def test_fully_quarantined_tier_fails_fast_instead_of_waiting(two_provider_config):
    """A dead tier must error, not hang.

    wait=True is correct for a rate limit (a slot opens in seconds) and wrong
    for a deleted model (still deleted tomorrow). Sleeping on the latter is
    indistinguishable from a crash, which is how this was reported.
    """
    respx.post(CHAT_URL).mock(return_value=httpx.Response(404, text="model gone"))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(404, text="model gone"))

    router = LocalRouter(str(two_provider_config))
    started = time.monotonic()
    with pytest.raises(RouterBusy, match="quarantined"):
        router.generate([{"role": "user", "content": "hi"}], tier="low")
    assert time.monotonic() - started < 30, "router waited instead of failing fast"


@respx.mock
def test_quarantine_failure_names_the_dead_models(two_provider_config):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(
        404, text="Model llama-3.1-8b-instant is archived"))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(404, text="Model gpt-oss-120b does not exist"))

    router = LocalRouter(str(two_provider_config))
    with pytest.raises(RouterBusy) as excinfo:
        router.generate([{"role": "user", "content": "hi"}], tier="low")
    message = str(excinfo.value)
    assert "groq/llama-3.1-8b-instant" in message
    assert "cerebras/gpt-oss-120b" in message
    assert "archived" in message


@respx.mock
def test_streaming_fully_quarantined_tier_fails_fast(two_provider_config):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(404, text="gone"))
    respx.post("https://api.cerebras.ai/v1/chat/completions").mock(
        return_value=httpx.Response(404, text="gone"))

    router = LocalRouter(str(two_provider_config))

    async def drain():
        async for _ in router.agenerate_stream(
                [{"role": "user", "content": "hi"}], tier="low"):
            pass

    started = time.monotonic()
    with pytest.raises(RouterBusy, match="quarantined"):
        asyncio.run(drain())
    assert time.monotonic() - started < 30, "stream waited instead of failing fast"
