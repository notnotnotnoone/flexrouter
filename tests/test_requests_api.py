"""The request log as JSON, the client tag, and the per-request exclude list.

An app built on flexrouter (Agora is the first) shows its own requests: which
models were passed over, which failed, which answered. It reads them from
/api/requests rather than rebuilding them from its own call records, tags its
requests with X-Flexrouter-Client so it can ask for only its own, and names
models a bucket call must leave out with X-Flexrouter-Exclude (ADR 0018).
"""
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from flexrouter._router import LocalRouter
from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.exceptions import RouterBusy

CHAT_URL = "https://alpha.test/v1/chat/completions"
MESSAGES = [{"role": "user", "content": "hi"}]


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [
            ModelConfig(provider="alpha", model="big", score=99, rpm=60, tpm=60000,
                        context_window=100_000),
            ModelConfig(provider="alpha", model="small", score=10, rpm=60, tpm=60000,
                        context_window=100_000),
        ]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["secret-1"])},
        state_dir=str(tmp_path / "state"),
    )


def _echo_model(request):
    """Answer as whichever model was asked, so a test can see who answered."""
    body = json.loads(request.content)
    if body.get("stream"):
        chunk = {"choices": [{"delta": {"content": body["model"]}}]}
        return httpx.Response(200, content=(
            f"data: {json.dumps(chunk)}\n\n".encode() + b"data: [DONE]\n\n"))
    return httpx.Response(200, json={
        "choices": [{"message": {"content": body["model"]}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}})


def _router(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def _traces(tmp_path) -> list[dict]:
    path = tmp_path / "state" / "traces.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def _drain(events) -> str:
    text = ""
    async for ev in events:
        text += getattr(ev, "text", "") or ""
    return text


# ── the router: client tag ─────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_the_client_tag_is_recorded_in_the_trace(tmp_path, monkeypatch):
    respx.post(CHAT_URL).mock(side_effect=_echo_model)
    router = _router(tmp_path, monkeypatch)

    await router.agenerate(MESSAGES, "smart", client="agora-run1")

    assert _traces(tmp_path)[-1]["asked"]["client"] == "agora-run1"


@pytest.mark.asyncio
@respx.mock
async def test_the_client_tag_is_recorded_for_a_stream_too(tmp_path, monkeypatch):
    respx.post(CHAT_URL).mock(side_effect=_echo_model)
    router = _router(tmp_path, monkeypatch)

    await _drain(router.agenerate_stream(MESSAGES, "smart", client="agora-run1"))

    assert _traces(tmp_path)[-1]["asked"]["client"] == "agora-run1"


@pytest.mark.asyncio
@respx.mock
async def test_the_client_tag_never_reaches_the_provider(tmp_path, monkeypatch):
    route = respx.post(CHAT_URL).mock(side_effect=_echo_model)
    router = _router(tmp_path, monkeypatch)

    await router.agenerate(MESSAGES, "smart", client="agora-run1")

    sent = json.loads(route.calls.last.request.content)
    assert "client" not in sent and "agora-run1" not in json.dumps(sent)


# ── the router: exclude ────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_an_excluded_model_is_left_out_of_a_bucket_call(tmp_path, monkeypatch):
    respx.post(CHAT_URL).mock(side_effect=_echo_model)
    router = _router(tmp_path, monkeypatch)

    result = await router.agenerate(MESSAGES, "smart", exclude={"alpha/big"})

    assert result["choices"][0]["message"]["content"] == "small"


@pytest.mark.asyncio
@respx.mock
async def test_an_excluded_model_is_left_out_of_a_stream(tmp_path, monkeypatch):
    respx.post(CHAT_URL).mock(side_effect=_echo_model)
    router = _router(tmp_path, monkeypatch)

    text = await _drain(router.agenerate_stream(MESSAGES, "smart", exclude={"alpha/big"}))

    assert text == "small"


@pytest.mark.asyncio
@respx.mock
async def test_the_trace_says_what_was_excluded_and_why(tmp_path, monkeypatch):
    respx.post(CHAT_URL).mock(side_effect=_echo_model)
    router = _router(tmp_path, monkeypatch)

    await router.agenerate(MESSAGES, "smart", exclude={"alpha/big", "nobody/else"})

    trace = _traces(tmp_path)[-1]
    assert trace["asked"]["exclude"] == ["alpha/big", "nobody/else"]
    assert [(s["provider"], s["model"], s["reason"]) for s in trace["skipped"]] == [
        ("alpha", "big", "excluded")]


@pytest.mark.asyncio
@respx.mock
async def test_excluding_every_model_fails_at_once_instead_of_waiting(tmp_path, monkeypatch):
    route = respx.post(CHAT_URL).mock(side_effect=_echo_model)
    router = _router(tmp_path, monkeypatch)
    router._cfg.failover_budget_seconds = 30

    import time
    start = time.monotonic()
    with pytest.raises(RouterBusy, match="excluded"):
        await router.agenerate(MESSAGES, "smart", exclude={"alpha/big", "alpha/small"})

    assert time.monotonic() - start < 2
    assert not route.called
    assert _traces(tmp_path)[-1]["ok"] is False


@pytest.mark.asyncio
@respx.mock
async def test_a_pinned_call_ignores_the_exclude_list(tmp_path, monkeypatch):
    respx.post(CHAT_URL).mock(side_effect=_echo_model)
    router = _router(tmp_path, monkeypatch)

    result = await router.agenerate(MESSAGES, "alpha/big", exclude={"alpha/big"})

    assert result["choices"][0]["message"]["content"] == "big"


# ── the HTTP surface: headers ──────────────────────────────────────────

@respx.mock
def test_the_client_header_lands_in_the_trace(tmp_path, monkeypatch):
    respx.post(CHAT_URL).mock(side_effect=_echo_model)
    client = _client(tmp_path, monkeypatch)

    r = client.post("/v1/chat/completions", headers={"X-Flexrouter-Client": " agora-run1 "},
                    json={"model": "smart", "messages": MESSAGES})

    assert r.status_code == 200
    assert _traces(tmp_path)[-1]["asked"]["client"] == "agora-run1"


@pytest.mark.parametrize("bad", ["has space", "x" * 65, "semi;colon", "<script>"])
def test_a_client_header_outside_the_safe_set_is_refused(tmp_path, monkeypatch, bad):
    client = _client(tmp_path, monkeypatch)

    r = client.post("/v1/chat/completions", headers={"X-Flexrouter-Client": bad},
                    json={"model": "smart", "messages": MESSAGES})

    assert r.status_code == 400
    assert "X-Flexrouter-Client" in r.json()["error"]["message"]


@respx.mock
def test_the_exclude_header_leaves_models_out(tmp_path, monkeypatch):
    respx.post(CHAT_URL).mock(side_effect=_echo_model)
    client = _client(tmp_path, monkeypatch)

    r = client.post("/v1/chat/completions", headers={"X-Flexrouter-Exclude": "alpha/big"},
                    json={"model": "smart", "messages": MESSAGES})

    assert r.json()["choices"][0]["message"]["content"] == "small"
    assert _traces(tmp_path)[-1]["asked"]["exclude"] == ["alpha/big"]


@respx.mock
def test_the_exclude_header_leaves_models_out_of_a_stream(tmp_path, monkeypatch):
    respx.post(CHAT_URL).mock(side_effect=_echo_model)
    client = _client(tmp_path, monkeypatch)

    r = client.post("/v1/chat/completions",
                    headers={"X-Flexrouter-Exclude": "alpha/big, "},
                    json={"model": "smart", "messages": MESSAGES, "stream": True})

    assert '"content": "small"' in r.text or '"content":"small"' in r.text
    assert _traces(tmp_path)[-1]["asked"]["exclude"] == ["alpha/big"]


@pytest.mark.parametrize("bad", ["no-slash", "alpha/big,bucketname"])
def test_an_exclude_entry_that_is_not_a_model_is_refused(tmp_path, monkeypatch, bad):
    client = _client(tmp_path, monkeypatch)

    r = client.post("/v1/chat/completions", headers={"X-Flexrouter-Exclude": bad},
                    json={"model": "smart", "messages": MESSAGES})

    assert r.status_code == 400
    assert "X-Flexrouter-Exclude" in r.json()["error"]["message"]


def test_excluding_every_model_over_http_is_a_503_naming_the_exclusion(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    r = client.post("/v1/chat/completions",
                    headers={"X-Flexrouter-Exclude": "alpha/big,alpha/small"},
                    json={"model": "smart", "messages": MESSAGES})

    assert r.status_code == 503
    assert "excluded" in r.json()["error"]["message"]


# ── the HTTP surface: /api/requests ────────────────────────────────────

def _write(tmp_path, *traces):
    path = tmp_path / "state" / "traces.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for t in traces:
            f.write(json.dumps(t) + "\n")


def _trace(id, client=None, ok=True, attempts=(), at="2026-09-26T10:00:00.000Z"):
    asked = {"bucket": "smart", "stream": False}
    if client:
        asked["client"] = client
    return {"id": id, "at": at, "asked": asked, "skipped": [], "attempts": list(attempts),
            "answered_by": {"provider": "alpha", "model": "big"} if ok else None,
            "tokens": {"in": 10, "out": 5}, "ms_total": 100, "ok": ok}


FAILED_ATTEMPT = {"n": 1, "provider": "alpha", "model": "small", "status": 429,
                  "provider_message": "slow down", "verdict": "too_fast", "ms": 5}


def test_the_request_log_is_json_newest_first(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _write(tmp_path, _trace("req_1"), _trace("req_2", client="agora-a"))

    body = client.get("/api/requests").json()

    assert [r["id"] for r in body["requests"]] == ["req_2", "req_1"]
    assert body["total"] == 2
    row = body["requests"][0]
    assert row["client"] == "agora-a"
    assert row["answered_by"] == {"provider": "alpha", "model": "big"}
    assert row["outcome"] == "ok"
    assert body["requests"][1]["client"] is None


def test_the_request_log_filters_by_client(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _write(tmp_path, _trace("req_1", client="agora-a"), _trace("req_2", client="agora-b"),
           _trace("req_3"))

    body = client.get("/api/requests", params={"client": "agora-a"}).json()

    assert [r["id"] for r in body["requests"]] == ["req_1"]
    assert body["total"] == 1


def test_the_request_log_filters_by_result_and_limits(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _write(tmp_path, _trace("req_ok"), _trace("req_fo", attempts=[FAILED_ATTEMPT]),
           _trace("req_bad", ok=False, attempts=[FAILED_ATTEMPT]),
           _trace("req_fo2", attempts=[FAILED_ATTEMPT]))

    failovers = client.get("/api/requests", params={"result": "failover"}).json()
    one = client.get("/api/requests", params={"limit": 1}).json()

    assert [r["id"] for r in failovers["requests"]] == ["req_fo2", "req_fo"]
    assert [r["id"] for r in one["requests"]] == ["req_fo2"]
    assert one["total"] == 4


def test_a_bad_request_log_filter_is_a_400(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    assert client.get("/api/requests", params={"result": "maybe"}).status_code == 400
    assert client.get("/api/requests", params={"limit": 0}).status_code == 400


def test_one_request_journey_is_json(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _write(tmp_path, _trace("req_fo", client="agora-a", attempts=[FAILED_ATTEMPT]))

    body = client.get("/api/requests/req_fo").json()

    assert [s["kind"] for s in body["steps"]] == ["failed", "answered"]
    assert body["steps"][0]["status"] == 429
    assert body["client"] == "agora-a"


def test_an_unknown_journey_is_a_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    r = client.get("/api/requests/nope")

    assert r.status_code == 404


@respx.mock
def test_a_real_tagged_request_shows_up_in_the_log_under_its_client(tmp_path, monkeypatch):
    respx.post(CHAT_URL).mock(side_effect=_echo_model)
    client = _client(tmp_path, monkeypatch)

    r = client.post("/v1/chat/completions",
                    headers={"X-Flexrouter-Client": "agora-run9",
                             "X-Flexrouter-Exclude": "alpha/big"},
                    json={"model": "smart", "messages": MESSAGES})
    request_id = r.headers["x-flexrouter-request-id"]

    [row] = client.get("/api/requests", params={"client": "agora-run9"}).json()["requests"]
    journey = client.get(f"/api/requests/{request_id}").json()

    assert row["id"] == request_id
    assert (row["answered_by"]["provider"], row["answered_by"]["model"]) == ("alpha", "small")
    assert [(s["kind"], s["model"]) for s in journey["steps"]] == [
        ("skipped", "big"), ("answered", "small")]
