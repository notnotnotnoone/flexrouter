"""The daemon's HTTP layer.

The headline test here is test_overlapping_requests_run_concurrently. The old
stdlib server shared one LocalRouter across ThreadingMixIn threads while the
router drove a single non-thread-safe asyncio loop through run_until_complete,
so the second overlapping request raised or hung. These tests pin down that
requests now genuinely overlap instead.
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter._router import (AttemptEvent, AttemptFailedEvent,
                                DeltaEvent, DoneEvent)
from flexrouter.app import create_app
from flexrouter.exceptions import RouterBusy, RouterError

OK_RESULT = {
    "choices": [{"message": {"role": "assistant", "content": "hello"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}


class FakePenalties:
    def __init__(self):
        self._q: dict[str, dict] = {}

    def is_quarantined(self, provider, model):
        return f"{provider}/{model}" in self._q

    def quarantine_reason(self, provider, model):
        entry = self._q.get(f"{provider}/{model}")
        return entry["reason"] if entry else None

    def quarantined(self):
        return dict(self._q)

    def clear_quarantine(self, provider, model):
        self._q.pop(f"{provider}/{model}", None)


def _model(provider, model, score=85, vision=False):
    return SimpleNamespace(provider=provider, model=model, score=score,
                           rpm=60, tpm=60000, context_window=131072,
                           vision=vision)


class FakeRouter:
    """Stands in for LocalRouter so these tests exercise the HTTP layer only."""

    def __init__(self, delay: float = 0.0, raises: Exception | None = None):
        self._cfg = SimpleNamespace(
            tiers={
                "low": [_model("groq", "llama-3.1-8b-instant", 85)],
                "high": [_model("openai", "gpt-4o", 95, vision=True)],
            },
            state_dir="",
            providers={
                "groq": SimpleNamespace(
                    base_url="https://api.groq.com/openai/v1",
                    api_keys=["gsk_secret_tail1234"]),
                "ollama": SimpleNamespace(
                    base_url="http://localhost:11434/v1", api_keys=[]),
            },
            auth_token=None,
        )
        self._engine = SimpleNamespace(_penalties=FakePenalties())
        self.delay = delay
        self.raises = raises
        self.calls: list[tuple] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def _track(self):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1

    async def agenerate(self, messages, tier, **kwargs):
        self.calls.append((tier, kwargs))
        await self._track()
        if self.raises:
            raise self.raises
        return dict(OK_RESULT)

    async def agenerate_stream(self, messages, tier, **kwargs):
        self.calls.append((tier, kwargs))
        await self._track()
        if self.raises:
            raise self.raises
        yield DeltaEvent(text="hel")
        yield DeltaEvent(text="lo")
        yield DoneEvent(result=dict(OK_RESULT))

    def close(self):
        pass


@pytest.fixture
def fake():
    return FakeRouter()


@pytest.fixture
def client(fake):
    app = create_app()
    app_module.state.router = fake
    with TestClient(app) as c:
        yield c
    app_module.state.router = None


def _client_for(router):
    app = create_app()
    app_module.state.router = router
    return TestClient(app)


# --- the bug this rewrite exists to fix -------------------------------------

def test_overlapping_requests_run_concurrently():
    """Several in-flight chat requests must actually overlap, not queue.

    Under the old server this raised "this event loop is already running" or
    deadlocked on the second concurrent request.
    """
    router = FakeRouter(delay=0.25)

    async def exercise():
        import httpx
        app = create_app()
        app_module.state.router = router
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            payload = {"model": "auto-low",
                       "messages": [{"role": "user", "content": "hi"}]}
            results = await asyncio.gather(
                *[c.post("/v1/chat/completions", json=payload) for _ in range(8)]
            )
        return results

    try:
        results = asyncio.run(exercise())
    finally:
        app_module.state.router = None

    assert all(r.status_code == 200 for r in results), \
        [r.status_code for r in results]
    assert all(r.json()["choices"][0]["message"]["content"] == "hello"
               for r in results)
    assert router.max_in_flight > 1, (
        f"requests were serialized (max in flight: {router.max_in_flight}) — "
        "the server is still handling one at a time"
    )


# --- chat completions --------------------------------------------------------

def test_chat_returns_openai_shape(client):
    r = client.post("/v1/chat/completions", json={
        "model": "auto-low", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "hello"
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert isinstance(body["created"], int)


def test_chat_requires_messages(client):
    r = client.post("/v1/chat/completions", json={"model": "auto-low"})
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"


def test_chat_rejects_bad_json(client):
    r = client.post("/v1/chat/completions", content=b"{not json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_model_name_selects_tier(client, fake):
    client.post("/v1/chat/completions", json={
        "model": "auto-high", "messages": [{"role": "user", "content": "hi"}]})
    assert fake.calls[-1][0] == "high"


def test_concrete_model_id_resolves_to_its_tier(client, fake):
    # Legacy discovery id "<tier>::<provider>/<model>" is still accepted, but
    # the model half now pins that exact model rather than being dropped in
    # favour of the tier — pinning is real behaviour now, not a no-op.
    client.post("/v1/chat/completions", json={
        "model": "high::openai/gpt-4o",
        "messages": [{"role": "user", "content": "hi"}]})
    assert fake.calls[-1][0] == "openai/gpt-4o"


def test_unknown_bucket_is_a_404(client, fake):
    # An unrecognized bucket used to silently fall back to whichever tier
    # came first. It now reports the mistake back to the caller instead of
    # guessing.
    r = client.post("/v1/chat/completions", json={
        "model": "auto-nonsense", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "model_not_found"
    assert fake.calls == []


def test_auto_picks_highest_scoring_tier(client, fake):
    client.post("/v1/chat/completions", json={
        "model": "auto", "messages": [{"role": "user", "content": "hi"}]})
    assert fake.calls[-1][0] == "high"


@pytest.mark.parametrize("param,value", [
    ("temperature", 0.2),
    ("max_tokens", 512),
    ("tools", [{"type": "function", "function": {"name": "f"}}]),
    ("tool_choice", "auto"),
    ("response_format", {"type": "json_object"}),
    ("stop", ["\n"]),
    ("seed", 7),
])
def test_params_reach_the_router(client, fake, param, value):
    # The old server forwarded five params and silently dropped everything
    # else, so a caller asking for JSON output just didn't get it.
    client.post("/v1/chat/completions", json={
        "model": "auto-low", "messages": [{"role": "user", "content": "hi"}],
        param: value})
    assert fake.calls[-1][1][param] == value


def test_unknown_params_are_not_forwarded(client, fake):
    client.post("/v1/chat/completions", json={
        "model": "auto-low", "messages": [{"role": "user", "content": "hi"}],
        "some_vendor_extension": "x"})
    assert "some_vendor_extension" not in fake.calls[-1][1]


def test_router_busy_maps_to_503():
    with _client_for(FakeRouter(raises=RouterBusy("all models busy"))) as c:
        r = c.post("/v1/chat/completions", json={
            "model": "auto-low", "messages": [{"role": "user", "content": "hi"}]})
    app_module.state.router = None
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "provider_unavailable"


def test_router_error_maps_to_502_not_400():
    """A RouterError is the providers failing, not a malformed request.

    This asserted 400/invalid_request_error until the whole-branch review:
    since Task 7 a RouterError also means "every provider failed" and "the
    model produced nothing after partial output", so calling it the caller's
    mistake sent them to debug the wrong end. The message text is unchanged;
    only the label and the status are.
    """
    with _client_for(FakeRouter(raises=RouterError("auth failure"))) as c:
        r = c.post("/v1/chat/completions", json={
            "model": "auto-low", "messages": [{"role": "user", "content": "hi"}]})
    app_module.state.router = None
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "server_error"
    assert r.json()["error"]["message"] == "auth failure"


def test_an_auth_failure_still_reads_correctly_as_a_server_error():
    """The other caller of this handler: every provider rejecting our keys.

    That is the service's own configured credential being refused upstream,
    which is a server-side problem however it is labelled - the text still
    names the provider and the status, and 502 is the honest status for it.
    """
    exc = RouterError("Auth failure for provider 'groq': 401")
    with _client_for(FakeRouter(raises=exc)) as c:
        r = c.post("/v1/chat/completions", json={
            "model": "auto-low", "messages": [{"role": "user", "content": "hi"}]})
    app_module.state.router = None
    assert r.status_code == 502
    assert "Auth failure for provider 'groq': 401" in r.json()["error"]["message"]


def test_a_streaming_router_error_is_a_server_error_too():
    with _client_for(FakeRouter(raises=RouterError("every provider failed"))) as c:
        r = c.post("/v1/chat/completions", json={
            "model": "auto-low", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]})
    app_module.state.router = None
    payloads = _sse_payloads(r.text)
    errors = [p["error"] for p in payloads if p.get("error")]
    assert errors and errors[0]["type"] == "server_error"
    assert errors[0]["message"] == "every provider failed"


# --- streaming ---------------------------------------------------------------

def _sse_payloads(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        if line.startswith("data: "):
            body = line[len("data: "):]
            if body != "[DONE]":
                out.append(json.loads(body))
    return out


def test_stream_emits_deltas_then_done(client):
    r = client.post("/v1/chat/completions", json={
        "model": "auto-low", "stream": True,
        "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text.endswith("data: [DONE]\n\n")

    payloads = _sse_payloads(r.text)
    content = "".join(
        p["choices"][0]["delta"].get("content", "")
        for p in payloads if p.get("choices")
    )
    assert content == "hello"
    assert all(p["object"] == "chat.completion.chunk" for p in payloads)


def test_stream_reports_finish_reason_and_usage(client):
    r = client.post("/v1/chat/completions", json={
        "model": "auto-low", "stream": True,
        "messages": [{"role": "user", "content": "hi"}]})
    payloads = _sse_payloads(r.text)
    assert any(p.get("choices") and p["choices"][0].get("finish_reason") == "stop"
               for p in payloads)
    assert any("usage" in p for p in payloads)


def test_stream_surfaces_busy_as_sse_error():
    with _client_for(FakeRouter(raises=RouterBusy("no capacity"))) as c:
        r = c.post("/v1/chat/completions", json={
            "model": "auto-low", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]})
    app_module.state.router = None
    # A stream that has already committed its 200 must report the failure in
    # the body rather than vanishing.
    assert "no capacity" in r.text
    assert r.text.endswith("data: [DONE]\n\n")


# --- models ------------------------------------------------------------------

def test_models_lists_auto_and_tiers(client):
    ids = [m["id"] for m in client.get("/v1/models").json()["data"]]
    assert "auto" in ids
    assert "low" in ids
    assert "high" in ids
    assert "groq/llama-3.1-8b-instant" in ids


def test_models_carries_routing_metadata(client):
    data = client.get("/v1/models").json()["data"]
    entry = next(m for m in data if m["id"] == "openai/gpt-4o")
    assert entry["flexrouter"]["vision"] is True
    assert entry["flexrouter"]["score"] == 95
    assert entry["flexrouter"]["quarantined"] is False


def test_models_flags_quarantined_routes(client, fake):
    fake._engine._penalties._q["groq/llama-3.1-8b-instant"] = {
        "until": 1e12, "reason": "404 model not found"}
    data = client.get("/v1/models").json()["data"]
    entry = next(m for m in data if m["id"] == "groq/llama-3.1-8b-instant")
    assert entry["flexrouter"]["quarantined"] is True
    assert "404" in entry["flexrouter"]["quarantine_reason"]


# --- quarantine endpoints ----------------------------------------------------

def test_quarantine_listing(client, fake):
    fake._engine._penalties._q["groq/dead-model"] = {
        "until": 1e12, "reason": "410 gone"}
    body = client.get("/api/quarantine").json()
    assert body["quarantined"] == [
        {"provider": "groq", "model": "dead-model", "until": 1e12, "reason": "410 gone"}
    ]


def test_quarantine_can_be_cleared(client, fake):
    fake._engine._penalties._q["groq/dead-model"] = {"until": 1e12, "reason": "410"}
    assert client.delete("/api/quarantine/groq/dead-model").json() == {"ok": True}
    assert client.get("/api/quarantine").json()["quarantined"] == []


def test_quarantine_clear_handles_slashes_in_model_name(client, fake):
    # Plenty of real model ids contain a slash: "meta-llama/llama-4-scout".
    fake._engine._penalties._q["groq/meta-llama/llama-4"] = {
        "until": 1e12, "reason": "404"}
    client.delete("/api/quarantine/groq/meta-llama/llama-4")
    assert client.get("/api/quarantine").json()["quarantined"] == []


# --- everything on one port --------------------------------------------------

def test_api_and_v1_and_dashboard_share_a_port(client):
    # The whole point of the merge: these used to be two servers.
    assert client.get("/v1/models").status_code == 200
    assert client.get("/api/quarantine").status_code == 200
    assert client.get("/").status_code in (200, 503)  # 503 if dashboard unbuilt


def test_unknown_path_falls_back_to_spa(client):
    r = client.get("/some/client/side/route")
    assert r.status_code in (200, 503)
    assert r.status_code != 404


# --- the server must never hang --------------------------------------------

class SlowRouter(FakeRouter):
    """Stands in for a tier where nothing is free: the library waits forever."""

    async def agenerate(self, messages, tier, **kwargs):
        await asyncio.sleep(30)
        return dict(OK_RESULT)

    async def agenerate_stream(self, messages, tier, **kwargs):
        await asyncio.sleep(30)
        yield DoneEvent(result=dict(OK_RESULT))
        return


def test_saturated_tier_returns_504_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(app_module, "ROUTE_TIMEOUT_SECONDS", 0.2)
    with _client_for(SlowRouter()) as c:
        r = c.post("/v1/chat/completions", json={
            "model": "auto-low", "messages": [{"role": "user", "content": "hi"}]})
    app_module.state.router = None
    assert r.status_code == 504
    assert r.json()["error"]["code"] == "provider_unavailable"


def test_timeout_message_says_what_to_do(monkeypatch):
    monkeypatch.setattr(app_module, "ROUTE_TIMEOUT_SECONDS", 0.2)
    with _client_for(SlowRouter()) as c:
        r = c.post("/v1/chat/completions", json={
            "model": "auto-low", "messages": [{"role": "user", "content": "hi"}]})
    app_module.state.router = None
    message = r.json()["error"]["message"]
    assert "flexrouter refresh" in message
    assert "'low'" in message


def test_streaming_saturated_tier_ends_the_stream(monkeypatch):
    monkeypatch.setattr(app_module, "ROUTE_TIMEOUT_SECONDS", 0.2)
    with _client_for(SlowRouter()) as c:
        r = c.post("/v1/chat/completions", json={
            "model": "auto-low", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]})
    app_module.state.router = None
    assert r.status_code == 200
    assert "became available within" in r.text
    assert r.text.endswith("data: [DONE]\n\n")


def test_slow_generation_is_not_cut_off(monkeypatch):
    """The bound covers finding a model, not producing tokens.

    A long answer must not be truncated just because it takes a while.
    """
    monkeypatch.setattr(app_module, "ROUTE_TIMEOUT_SECONDS", 1.0)

    class SlowTokens(FakeRouter):
        async def agenerate_stream(self, messages, tier, **kwargs):
            yield DeltaEvent(text="start")
            await asyncio.sleep(1.5)   # longer than the routing bound
            yield DeltaEvent(text="-end")
            yield DoneEvent(result=dict(OK_RESULT))

    with _client_for(SlowTokens()) as c:
        r = c.post("/v1/chat/completions", json={
            "model": "auto-low", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]})
    app_module.state.router = None
    content = "".join(
        p["choices"][0]["delta"].get("content", "")
        for p in _sse_payloads(r.text) if p.get("choices")
    )
    assert content == "start-end"
    assert "became available within" not in r.text


# --- provider keys ----------------------------------------------------------

from flexrouter.probe import ProbeResult  # noqa: E402


def test_provider_list_masks_the_key(client):
    rows = client.get("/api/providers").json()["providers"]
    groq = next(r for r in rows if r["provider"] == "groq")
    assert groq["key_masked"] == "...1234"
    assert groq["has_key"] is True
    # The real key must never reach the browser.
    assert "gsk_secret_tail1234" not in client.get("/api/providers").text


def test_provider_list_reports_keyless_providers(client):
    rows = client.get("/api/providers").json()["providers"]
    ollama = next(r for r in rows if r["provider"] == "ollama")
    assert ollama["has_key"] is False
    assert ollama["key_masked"] == ""


def test_provider_list_includes_configured_models(client):
    rows = client.get("/api/providers").json()["providers"]
    groq = next(r for r in rows if r["provider"] == "groq")
    assert groq["configured_models"] == ["llama-3.1-8b-instant"]


def test_testing_a_key_reports_success_and_model_count(client, monkeypatch):
    async def fake_probe(base_url, api_key, timeout=15.0):
        return ProbeResult(ok=True, models=["a", "b", "c"], status_code=200)
    monkeypatch.setattr(app_module, "probe_key", fake_probe)

    body = client.post("/api/providers/test", json={
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": "gsk_new"}).json()
    assert body["ok"] is True
    assert body["model_count"] == 3


def test_testing_a_key_surfaces_the_providers_own_words(client, monkeypatch):
    async def fake_probe(base_url, api_key, timeout=15.0):
        return ProbeResult(ok=False, error="Invalid API Key (the key was rejected)",
                           status_code=401)
    monkeypatch.setattr(app_module, "probe_key", fake_probe)

    body = client.post("/api/providers/test", json={
        "base_url": "https://api.groq.com/openai/v1", "api_key": "bad"}).json()
    assert body["ok"] is False
    assert "Invalid API Key" in body["error"]


def test_testing_a_key_requires_a_base_url(client):
    r = client.post("/api/providers/test", json={"api_key": "x"})
    assert r.status_code == 400


def test_testing_a_named_provider_flags_its_dead_models(client, monkeypatch):
    # groq has llama-3.1-8b-instant configured; the provider no longer lists it.
    async def fake_probe(base_url, api_key, timeout=15.0):
        return ProbeResult(ok=True, models=["llama-3.3-70b-versatile"],
                           status_code=200)
    monkeypatch.setattr(app_module, "probe_key", fake_probe)

    body = client.post("/api/providers/test", json={
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": "k", "provider": "groq"}).json()
    assert body["stale_models"] == ["llama-3.1-8b-instant"]


def test_retesting_a_saved_provider_needs_no_key(client, monkeypatch):
    seen = {}

    async def fake_probe(base_url, api_key, timeout=15.0):
        seen["key"] = api_key
        return ProbeResult(ok=True, models=["llama-3.1-8b-instant"], status_code=200)
    monkeypatch.setattr(app_module, "probe_key", fake_probe)

    body = client.post("/api/providers/groq/test").json()
    assert body["ok"] is True
    assert body["provider"] == "groq"
    assert seen["key"] == "gsk_secret_tail1234"   # pulled from stored config


def test_retesting_an_unknown_provider_is_a_404(client):
    assert client.post("/api/providers/nope/test").status_code == 404


def test_a_working_retest_lifts_a_stale_quarantine(client, fake, monkeypatch):
    fake._engine._penalties._q["groq/*"] = {"until": 1e12, "reason": "401 rejected"}

    async def fake_probe(base_url, api_key, timeout=15.0):
        return ProbeResult(ok=True, models=["x"], status_code=200)
    monkeypatch.setattr(app_module, "probe_key", fake_probe)

    client.post("/api/providers/groq/test")
    assert fake._engine._penalties.is_quarantined("groq", "*") is False


def test_a_failing_retest_leaves_the_quarantine_alone(client, fake, monkeypatch):
    fake._engine._penalties._q["groq/*"] = {"until": 1e12, "reason": "401 rejected"}

    async def fake_probe(base_url, api_key, timeout=15.0):
        return ProbeResult(ok=False, error="still rejected", status_code=401)
    monkeypatch.setattr(app_module, "probe_key", fake_probe)

    client.post("/api/providers/groq/test")
    assert fake._engine._penalties.is_quarantined("groq", "*") is True


def test_the_streaming_bound_covers_the_whole_call_not_each_attempt(monkeypatch):
    """One deadline for the call, not a fresh one per routing attempt.

    `first` stays True while routing-progress events go by, which is right -
    chatter is not content - but the wait was re-armed with the full
    ROUTE_TIMEOUT_SECONDS every time round the loop, so a bucket that kept
    failing over held the client for roughly retries x the bound while the
    non-streaming path capped the same call once. Each attempt below takes
    0.4s against a 0.5s bound, so under the old code nothing timed out until
    the sixth attempt gave up (~2.9s); under one absolute deadline the
    timeout fires once, at 0.5s.
    """
    monkeypatch.setattr(app_module, "ROUTE_TIMEOUT_SECONDS", 0.5)

    class FailingOver(FakeRouter):
        async def agenerate_stream(self, messages, tier, **kwargs):
            for attempt in range(1, 7):
                await asyncio.sleep(0.4)
                yield AttemptEvent(attempt=attempt, max_attempts=6,
                                   provider="groq", model="m1")
                yield AttemptFailedEvent(attempt=attempt, max_attempts=6,
                                         provider="groq", model="m1",
                                         reason="rate_limited")
            await asyncio.sleep(30)
            yield DoneEvent(result=dict(OK_RESULT))

    started = time.monotonic()
    with _client_for(FailingOver()) as c:
        r = c.post("/v1/chat/completions", json={
            "model": "auto-low", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]})
    elapsed = time.monotonic() - started
    app_module.state.router = None

    assert r.status_code == 200
    assert r.text.count("became available within") == 1   # once, not per attempt
    assert r.text.rstrip().endswith("data: [DONE]")
    # Generous headroom over the 0.5s bound, still far under the ~2.9s the
    # per-attempt re-arm produced.
    assert elapsed < 1.5, elapsed
