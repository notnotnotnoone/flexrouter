"""The flexrouter daemon: one process, one port, everything on it.

Replaces the two stdlib ``http.server`` instances this used to run (dashboard
on 7352, OpenAI API on 7353). Those shared a single LocalRouter across
ThreadingMixIn worker threads while the router drove one non-thread-safe
asyncio loop via ``run_until_complete`` — so the second overlapping request
either raised "this event loop is already running" or deadlocked. That is not
a race you can win with locks; it needs the server to own the loop, which is
what this does. Endpoints that touch the router are ``async def`` and await it
directly. Nothing here creates an event loop.

Layout on the single port:
    /v1/*     OpenAI-compatible API (what chat clients point at)
    /api/*    dashboard data
    /*        the dashboard's own pages (server-rendered, no build step)
"""
from __future__ import annotations

import asyncio
import copy
import hmac
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from flexrouter.dashboard.api import (
    get_config, get_config_validation, get_health_current, get_last_refresh,
    get_logs, get_stats, get_status, get_uptime, post_config, run_refresh,
)
from flexrouter import app_password
from flexrouter.dashboard.assets import STATIC_DIR
from flexrouter.dashboard.pages import pages as dashboard_pages
from flexrouter.exceptions import RouterBusy, RouterError
from flexrouter.probe import probe_key, stale_models
from flexrouter.redact import scrub
from flexrouter.traces import new_trace_id
from flexrouter.wire import bucket_id, model_id, parse_model, resolve

# The library blocks indefinitely when a tier is saturated, which is the right
# default for a script. A server must not: an HTTP client that never gets a
# response is indistinguishable from a crash, and that is how this was
# reported. Cap how long routing may spend looking for a free slot.
ROUTE_TIMEOUT_SECONDS = float(os.environ.get("FLEXROUTER_ROUTE_TIMEOUT", "90"))

_TIMEOUT_HINT = (
    "No model in bucket {tier!r} became available within {secs:g}s - every model "
    "is rate-limited, over budget, or quarantined. Check the dashboard for why, "
    "or run `flexrouter refresh` if the model list is stale."
)

# Params we forward to the provider. The old server passed five and silently
# dropped the rest, so a client asking for a seed or a JSON response format got
# neither, and no error explaining why.
_PASSTHROUGH = (
    "temperature", "top_p", "max_tokens", "max_completion_tokens", "tools",
    "tool_choice", "response_format", "stop", "seed", "n",
    "presence_penalty", "frequency_penalty", "logit_bias", "user",
)


class _State:
    """Holds the one router the whole process shares."""

    router: Optional[Any] = None
    config_path: Optional[str] = None


state = _State()


def get_router():
    if state.router is None:
        from flexrouter._router import LocalRouter
        state.router = LocalRouter(state.config_path)
    return state.router


def _state_dir() -> str:
    return get_router()._cfg.state_dir


REQUEST_ID_HEADER = "x-flexrouter-request-id"


def _attempt_envelope(attempts: list[dict]) -> list[dict]:
    """The attempts list built for internal use (traces, the error brain)
    trimmed and scrubbed for something handed back to a caller."""
    return [{
        "model": f"{a['provider']}/{a['model']}",
        "status": a.get("status"),
        "provider_message": scrub(a.get("provider_message", "")),
        "ms": a.get("ms"),
        "waited_ms": a.get("waited_ms", 0),
        "verdict": a.get("verdict"),
    } for a in attempts]


def openai_error(message: str, error_type: str = "server_error",
                 code: str | None = None, status: int = 500, *,
                 scrub_message: bool = True, request_id: str | None = None,
                 attempts: list[dict] | None = None) -> JSONResponse:
    """`scrub_message=False` is only for a fixed string this codebase wrote
    itself - never for anything derived from a provider response, an
    exception, a config file, or a request. The default is True so a new
    call site that forgets this parameter is safe by accident, not by luck.
    """
    # Scrubbed here, at the single exit, rather than at each raise site. A
    # raise site added later would otherwise be a leak nobody notices.
    text = scrub(message) if scrub_message else message
    err: dict = {"message": text, "type": error_type}
    if code is not None:
        err["code"] = code
    if request_id is not None:
        err["flexrouter"] = {"request_id": request_id,
                             "attempts": _attempt_envelope(attempts or [])}
    headers = {REQUEST_ID_HEADER: request_id} if request_id is not None else None
    return JSONResponse({"error": err}, status_code=status, headers=headers)


_UNAUTHORIZED = ("This flexrouter needs a key. Send it as an Authorization "
                 "header: Bearer <your key>. It is the auth_token line in "
                 "your settings.")


def _check_token(request: Request):
    """None when the request may proceed, an error response when it may not.

    Guards /v1 only. The dashboard and its data stay open: a browser has no
    way to carry this header, and the service binds to 127.0.0.1. ADR 0009.
    """
    # A password generated from the dashboard wins over the settings file's
    # auth_token (ADR 0015); with neither, /v1 is open as before.
    expected = app_password.effective(get_router()._cfg.auth_token)
    if not expected:
        return None
    header = request.headers.get("authorization") or ""
    prefix = "Bearer "
    given = header[len(prefix):] if header.startswith(prefix) else ""
    # compare_digest, not ==, so how long this takes says nothing about how
    # much of the key was right. Compared as bytes, not str: compare_digest
    # rejects a non-ASCII str operand outright, and a key typed with an
    # accented character is plausible enough (it is hand-written) that this
    # must not surface as a bare 500. Encoded as latin-1, not utf-8: HTTP
    # header values are latin-1 on the wire (what Starlette decoded `given`
    # from), so that is the encoding that reconstructs the bytes a real
    # client actually sent. If `expected` itself holds a character no header
    # can carry, it can never match anything a real request presents, so
    # that failure means "wrong key", not a 500.
    try:
        given_bytes = given.encode("latin-1")
        expected_bytes = expected.encode("latin-1")
    except UnicodeEncodeError:
        given_bytes = expected_bytes = None
    if given and given_bytes is not None and hmac.compare_digest(
            given_bytes, expected_bytes):
        return None
    # The message contains neither key, right or wrong - it is a fixed
    # string this codebase wrote, not anything derived from the request, so
    # scrubbing it buys nothing and only makes it unreadable.
    return openai_error(_UNAUTHORIZED, "invalid_request_error",
                        "invalid_api_key", 401, scrub_message=False)


# --------------------------------------------------------------------------
# OpenAI-compatible API
# --------------------------------------------------------------------------

v1 = APIRouter(prefix="/v1")


def _best_bucket(router) -> str:
    """The bucket holding the single highest-scoring model. What `auto` means."""
    buckets = list(router._cfg.tiers.keys())
    if not buckets:
        return "default"
    best_score = None
    best = buckets[0]
    for name in buckets:
        for mc in router._cfg.tiers[name]:
            if best_score is None or mc.score > best_score:
                best_score, best = mc.score, name
    return best


def _model_entries(router) -> list[dict]:
    """Everything /v1/models advertises: buckets first, then `auto`, then every
    model by its own name.

    Buckets come first because they are the normal path — the owner's code and
    his chat clients ask for "smart", not for a particular provider's model. A
    chat client that renders this list in a dropdown shows him the names he
    chose himself, at the top.
    """
    tiers = router._cfg.tiers
    penalties = router._engine._penalties
    entries: list[dict] = []

    for name, model_configs in tiers.items():
        entries.append({
            "id": bucket_id(name),
            "object": "model",
            "created": 0,
            "owned_by": "flexrouter",
            "flexrouter": {
                "kind": "bucket",
                "models": [model_id(mc.provider, mc.model) for mc in model_configs],
            },
        })

    entries.append({
        "id": "auto", "object": "model", "created": 0, "owned_by": "flexrouter",
        "flexrouter": {"kind": "bucket", "models": [],
                       "resolves_to": _best_bucket(router)},
    })

    buckets_by_model: dict[tuple[str, str], list[str]] = {}
    for name, model_configs in tiers.items():
        for mc in model_configs:
            buckets_by_model.setdefault((mc.provider, mc.model), []).append(name)

    seen: set[tuple[str, str]] = set()
    for model_configs in tiers.values():
        for mc in model_configs:
            key = (mc.provider, mc.model)
            if key in seen:
                continue
            seen.add(key)
            entries.append({
                "id": model_id(mc.provider, mc.model),
                "object": "model",
                "created": 0,
                "owned_by": mc.provider,
                "flexrouter": {
                    "kind": "model",
                    "provider": mc.provider,
                    "model": mc.model,
                    "buckets": buckets_by_model[key],
                    "score": mc.score,
                    "rpm": mc.rpm,
                    "tpm": mc.tpm,
                    "context_window": mc.context_window,
                    "vision": mc.vision,
                    "quarantined": penalties.is_quarantined(mc.provider, mc.model),
                    "quarantine_reason": penalties.quarantine_reason(mc.provider, mc.model),
                },
            })

    return entries


@v1.get("/models")
async def list_models():
    return {"object": "list", "data": _model_entries(get_router())}


@v1.get("/models/{wanted:path}")
async def get_model(wanted: str):
    """One entry.

    The path converter is needed because a model's name contains a slash,
    which FastAPI would otherwise read as another path segment.
    """
    router = get_router()
    target = parse_model(wanted)
    looking_for = target.name if target.kind == "pin" else bucket_id(target.name)
    for entry in _model_entries(router):
        if entry["id"] == looking_for:
            return entry
    return openai_error(
        f"There is no bucket or model named {wanted!r}.",
        "invalid_request_error", "model_not_found", 404)


def _kwargs_from(body: dict) -> dict:
    return {k: body[k] for k in _PASSTHROUGH if k in body}


@v1.post("/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except Exception:
        return openai_error("Invalid JSON in request body",
                            "invalid_request_error", status=400)

    messages = body.get("messages") or []
    if not messages:
        return openai_error("messages is required", "invalid_request_error",
                            status=400)

    model = body.get("model", "auto")
    router = get_router()
    # Minted here, not inside the router, so it's known for the response
    # header even when routing never gets far enough to produce one itself
    # (a bad model name, a timeout) - every response, success or failure,
    # carries the same ID.
    trace_id = new_trace_id()
    try:
        tier = resolve(parse_model(model), list(router._cfg.tiers.keys()),
                       _best_bucket(router))
    except KeyError as exc:
        return openai_error(str(exc.args[0]), "invalid_request_error",
                            "model_not_found", 404, request_id=trace_id)
    kwargs = _kwargs_from(body)

    if body.get("stream"):
        return StreamingResponse(
            _stream_chat(router, messages, tier, model, kwargs, trace_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                     REQUEST_ID_HEADER: trace_id},
        )

    try:
        result = await asyncio.wait_for(
            router.agenerate(messages, tier, trace_id=trace_id, **kwargs),
            timeout=ROUTE_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, TimeoutError):
        return openai_error(
            _TIMEOUT_HINT.format(tier=tier, secs=ROUTE_TIMEOUT_SECONDS),
            "server_error", "provider_unavailable", 504, request_id=trace_id)
    except RouterBusy as exc:
        return openai_error(str(exc), "server_error", "provider_unavailable", 503,
                            request_id=trace_id, attempts=getattr(exc, "attempts", None))
    except RouterError as exc:
        # A provider-side failure, not the caller's mistake. Since Task 7
        # RouterError also means "every provider failed" and "the model
        # produced nothing after partial output"; calling that
        # invalid_request_error sent callers to debug their own payload.
        return openai_error(str(exc), "server_error", status=502,
                            request_id=trace_id, attempts=getattr(exc, "attempts", None))
    except KeyError:
        # The pin engine raises KeyError for a model that is not in the
        # settings at all. The client asked for something specific by name;
        # say so rather than returning a bare 500.
        return openai_error(
            f"There is no bucket or model named {model!r}.",
            "invalid_request_error", "model_not_found", 404, request_id=trace_id)
    except Exception as exc:  # noqa: BLE001
        return openai_error(str(exc), request_id=trace_id,
                            attempts=getattr(exc, "attempts", None))

    result.setdefault("id", f"chatcmpl-{uuid.uuid4().hex[:29]}")
    result.setdefault("object", "chat.completion")
    result.setdefault("created", int(time.time()))
    result.setdefault("model", model)
    return JSONResponse(result, headers={REQUEST_ID_HEADER: trace_id})


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload) + "\n\n"


def _sse_error(message: str, error_type: str = "server_error",
               code: str | None = None, *, request_id: str | None = None,
               attempts: list[dict] | None = None) -> str:
    err: dict = {"message": scrub(message), "type": error_type}
    if code is not None:
        err["code"] = code
    if request_id is not None:
        err["flexrouter"] = {"request_id": request_id,
                             "attempts": _attempt_envelope(attempts or [])}
    return _sse({"error": err})


def _tool_call_delta(event) -> dict:
    """The lossy rebuild, kept only for events that carry no raw dictionary."""
    delta: dict = {"index": event.index}
    if event.id is not None:
        delta["id"] = event.id
        delta["type"] = "function"
    if event.name is not None:
        delta["function"] = {"name": event.name}
    if event.arguments is not None:
        delta.setdefault("function", {})["arguments"] = event.arguments
    return delta


async def _stream_chat(router, messages: list[dict], tier: str, model: str,
                       kwargs: dict, trace_id: str) -> AsyncIterator[str]:
    from flexrouter._router import (
        AttemptEvent, AttemptFailedEvent, DeltaEvent, DoneEvent,
        ReasoningDeltaEvent, ToolCallDeltaEvent,
    )

    chat_id = "chatcmpl-" + uuid.uuid4().hex[:29]
    created = int(time.time())

    def chunk(choices: list, **extra) -> str:
        payload = {"id": chat_id, "object": "chat.completion.chunk",
                   "created": created, "model": model, "choices": choices}
        payload.update(extra)
        return _sse(payload)

    def error_chunk(message: str, error_type: str = "server_error",
                    code: str | None = None, *,
                    attempts: list[dict] | None = None) -> str:
        """One chunk a client can actually parse, carrying the failure.

        A bare {"error": ...} object is what this used to send; a client that
        expects every payload to be a chat.completion.chunk throws on it and
        shows the user nothing, losing the partial answer that already
        arrived. This uses the same envelope as every other chunk, so a client
        that ignores unknown keys renders the partial answer and stops
        cleanly.
        """
        err: dict = {"message": scrub(message), "type": error_type,
                    "flexrouter": {"request_id": trace_id,
                                  "attempts": _attempt_envelope(attempts or [])}}
        if code is not None:
            err["code"] = code
        return chunk([{"index": 0, "delta": {}, "finish_reason": "error"}],
                     error=err)

    try:
        events = router.agenerate_stream(
            messages, tier, trace_id=trace_id, **kwargs).__aiter__()
        first = True
        emitted = False       # at least one content chunk has gone out
        # One absolute deadline for the whole call, fixed before the first
        # attempt. Waiting ROUTE_TIMEOUT_SECONDS per __anext__ re-armed the
        # bound on every routing attempt, so a bucket that kept failing over
        # held the client for retries x ROUTE_TIMEOUT_SECONDS while the
        # non-streaming path capped the same call once.
        deadline = time.monotonic() + ROUTE_TIMEOUT_SECONDS
        while True:
            try:
                if first:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    event = await asyncio.wait_for(
                        events.__anext__(), timeout=remaining)
                else:
                    event = await events.__anext__()
            except StopAsyncIteration:
                break
            except (asyncio.TimeoutError, TimeoutError):
                message = _TIMEOUT_HINT.format(tier=tier, secs=ROUTE_TIMEOUT_SECONDS)
                if emitted:
                    yield error_chunk(message, "server_error", "provider_unavailable")
                else:
                    yield _sse_error(message, "server_error", "provider_unavailable")
                yield "data: [DONE]\n\n"
                return
            if isinstance(event, (AttemptEvent, AttemptFailedEvent)):
                # Routing-progress chatter, not output: `first` stays True,
                # so the bound stays armed until real content arrives. It is
                # not re-armed - the next wait runs against what is left of
                # the one deadline fixed above, so a tier that keeps retrying
                # runs that deadline down instead of resetting it.
                continue
            first = False

            if isinstance(event, DeltaEvent):
                yield chunk([{"index": 0, "delta": {"content": event.text},
                              "finish_reason": None}])
                emitted = True

            elif isinstance(event, ReasoningDeltaEvent):
                yield chunk([{"index": 0,
                              "delta": {"reasoning_content": event.text},
                              "finish_reason": None}])
                emitted = True

            elif isinstance(event, ToolCallDeltaEvent):
                # Forwarded exactly as the provider wrote it. Rebuilding it
                # from the parsed fields is how LiteLLM loses tool calls
                # (BerriAI/litellm#17246), and the spec names that explicitly.
                # `raw` is empty only for an event built by older code; fall
                # back to the parsed fields in that case.
                # Deep copy: `raw`'s nested `function` sub-dict is the same
                # object the provider client's chunk holds. A shallow copy
                # would only isolate the top level, letting a later edit to
                # delta["function"] reach back into the stream the router is
                # still reading.
                delta = copy.deepcopy(event.raw) if event.raw else _tool_call_delta(event)
                yield chunk([{"index": 0, "delta": {"tool_calls": [delta]},
                              "finish_reason": None}])
                emitted = True

            elif isinstance(event, DoneEvent):
                choices = event.result.get("choices") or []
                message = choices[0].get("message", {}) if choices else {}
                finish = choices[0].get("finish_reason", "stop") if choices else "stop"
                if message.get("tool_calls"):
                    finish = "tool_calls"
                yield chunk([{"index": 0, "delta": {}, "finish_reason": finish}])
                usage = event.result.get("usage")
                if usage:
                    yield chunk([], usage=usage)
                yield "data: [DONE]\n\n"
                return

        # Unconditionally _sse_error, not emitted-branched: this line is only
        # reached when the `while True` loop above exits via
        # StopAsyncIteration without ever seeing a DoneEvent. Every event
        # that carries content, reasoning or a tool call sets emitted = True
        # and is followed either by a DoneEvent (which returns above) or an
        # exception from the router (caught by the handlers below, which are
        # emitted-branched) — so this point is only reachable with
        # emitted == False.
        yield _sse_error("No model available", "server_error", "provider_unavailable")
        yield "data: [DONE]\n\n"

    except RouterBusy as exc:
        attempts = getattr(exc, "attempts", None)
        if emitted:
            yield error_chunk(str(exc), "server_error", "provider_unavailable", attempts=attempts)
        else:
            yield _sse_error(str(exc), "server_error", "provider_unavailable",
                             request_id=trace_id, attempts=attempts)
        yield "data: [DONE]\n\n"
    except RouterError as exc:
        # Same relabel as the non-streaming path: a RouterError here is the
        # providers failing, not the request being malformed.
        attempts = getattr(exc, "attempts", None)
        if emitted:
            yield error_chunk(str(exc), "server_error", attempts=attempts)
        else:
            yield _sse_error(str(exc), "server_error", request_id=trace_id, attempts=attempts)
        yield "data: [DONE]\n\n"
    except KeyError:
        message = f"There is no bucket or model named {model!r}."
        if emitted:
            yield error_chunk(message, "invalid_request_error", "model_not_found")
        else:
            yield _sse_error(message, "invalid_request_error", "model_not_found")
        yield "data: [DONE]\n\n"
    except Exception as exc:  # noqa: BLE001
        if emitted:
            yield error_chunk(str(exc))
        else:
            yield _sse_error(str(exc))
        yield "data: [DONE]\n\n"


# --------------------------------------------------------------------------
# Dashboard data. Sync defs on purpose — FastAPI runs them in a threadpool, so
# their file reads don't stall the event loop serving chat requests.
# --------------------------------------------------------------------------

api = APIRouter(prefix="/api")


@api.get("/status")
def api_status():
    return get_status(_state_dir())


@api.get("/logs")
def api_logs(n: int = 50):
    return get_logs(_state_dir(), n)


@api.get("/config")
def api_get_config():
    return get_config()


@api.post("/config")
async def api_post_config(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "empty or invalid body"}, status_code=400)
    if not body:
        return JSONResponse({"error": "empty body"}, status_code=400)
    try:
        post_config(body)
    except (ValueError, TypeError) as e:
        # A rejected change is the caller's mistake, not a server fault, and
        # nothing was written.
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True}


@api.get("/stats")
def api_stats():
    return get_stats(_state_dir())


@api.get("/uptime")
def api_uptime():
    return get_uptime(_state_dir())


@api.get("/config/validate")
def api_config_validate():
    return get_config_validation()


@api.get("/health/current")
def api_health_current():
    return get_health_current(_state_dir())


@api.get("/refresh")
def api_get_refresh():
    return get_last_refresh(_state_dir())


@api.post("/refresh")
def api_post_refresh():
    return run_refresh(_state_dir())


@api.get("/quarantine")
def api_quarantine():
    """Routes sidelined because a provider said the model is gone.

    The dashboard needs this distinct from the penalty countdown: a 30-second
    backoff is worth waiting out, a quarantine means fix your config.
    """
    penalties = get_router()._engine._penalties
    entries = []
    for key, entry in penalties.quarantined().items():
        provider, _, model = key.partition("/")
        entries.append({
            "provider": provider,
            "model": model,
            "until": entry["until"],
            "reason": entry["reason"],
        })
    return {"quarantined": entries}


@api.delete("/quarantine/{provider}/{model:path}")
def api_clear_quarantine(provider: str, model: str):
    get_router()._engine._penalties.clear_quarantine(provider, model)
    return {"ok": True}


# --------------------------------------------------------------------------
# Provider keys. The point of these is that a key gets checked against the
# real provider before anyone trusts it - the old flow was "paste it into the
# YAML and find out at 3am when a request fails".
# --------------------------------------------------------------------------

def _mask(key: str | None) -> str:
    """Never send a stored key back to the browser in full."""
    if not key:
        return ""
    return f"...{key[-4:]}" if len(key) > 4 else "..."


def _configured_models(router, provider: str) -> list[str]:
    seen = []
    for model_configs in router._cfg.tiers.values():
        for mc in model_configs:
            if mc.provider == provider and mc.model not in seen:
                seen.append(mc.model)
    return seen


@api.get("/providers")
def api_providers():
    """Every configured provider, with its key masked and its key count."""
    router = get_router()
    penalties = router._engine._penalties
    out = []
    for name, pcfg in router._cfg.providers.items():
        keys = list(pcfg.api_keys or [])
        out.append({
            "provider": name,
            "base_url": pcfg.base_url,
            "key_count": len(keys),
            "key_masked": _mask(keys[0] if keys else None),
            "has_key": bool(keys),
            "configured_models": _configured_models(router, name),
            "quarantined": penalties.is_quarantined(name, "*"),
            "quarantine_reason": penalties.quarantine_reason(name, "*"),
        })
    return {"providers": out}


@api.post("/providers/test")
async def api_test_key(request: Request):
    """Check a key the user just typed, before it is saved anywhere.

    Body: {"base_url": "...", "api_key": "...", "provider": "optional name"}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid body"}, status_code=400)

    base_url = (body.get("base_url") or "").strip()
    if not base_url:
        return JSONResponse({"error": "base_url is required"}, status_code=400)

    result = await probe_key(base_url, body.get("api_key"),
                             timeout=get_router()._cfg.probe_timeout_seconds)
    payload = result.as_dict()

    # If this names a provider we already route to, say which of its
    # configured models the provider no longer lists - the dead ones.
    provider = body.get("provider")
    if provider and result.ok:
        configured = _configured_models(get_router(), provider)
        payload["configured_models"] = configured
        payload["stale_models"] = stale_models(configured, result.models)
    return payload


@api.post("/providers/{provider}/test")
async def api_retest_provider(provider: str):
    """Re-check a provider using its stored key, without retyping it."""
    router = get_router()
    pcfg = router._cfg.providers.get(provider)
    if pcfg is None:
        return JSONResponse({"error": f"unknown provider {provider!r}"},
                            status_code=404)

    keys = list(pcfg.api_keys or [])
    result = await probe_key(pcfg.base_url, keys[0] if keys else None,
                             timeout=router._cfg.probe_timeout_seconds)
    payload = result.as_dict()
    payload["provider"] = provider

    configured = _configured_models(router, provider)
    payload["configured_models"] = configured
    payload["stale_models"] = stale_models(configured, result.models) if result.ok else []

    # A key that works again should not stay sidelined by a stale quarantine.
    if result.ok:
        router._engine._penalties.clear_quarantine(provider, "*")
    return payload


@api.get("/tiers/{tier}/availability")
def api_tier_availability(tier: str):
    """Per-model account of what this tier can and cannot do right now.

    Answers "why didn't it use model X" without reading log files.
    """
    router = get_router()
    if tier not in router._cfg.tiers:
        return JSONResponse({"error": f"unknown tier {tier!r}"}, status_code=404)
    models = router._engine.explain_unavailable(tier)
    return {
        "tier": tier,
        "available_count": sum(1 for m in models if m["available"]),
        "total": len(models),
        "models": models,
    }



# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    get_router()  # fail fast on a broken config, at startup not mid-request
    yield
    if state.router is not None:
        state.router.close()
        state.router = None


def create_app(config_path: str | None = None) -> FastAPI:
    state.config_path = config_path
    state.router = None

    app = FastAPI(title="flexrouter", version="2.2.0", lifespan=lifespan)

    # Registered *before* add_middleware(CORSMiddleware) below, on purpose:
    # Starlette wraps middleware in registration order, so whatever is added
    # via add_middleware() after this ends up outermost, running first on the
    # way in and last on the way out. That makes CORSMiddleware see every
    # request before the guard does - so it answers a CORS preflight (an
    # OPTIONS carrying Access-Control-Request-Method) itself, without the
    # guard ever running - and it also gets to add
    # Access-Control-Allow-Origin to the guard's own 401 response on the way
    # back out. The reverse order (guard added after CORSMiddleware) made the
    # guard outermost instead: it 401'd preflights before CORSMiddleware ever
    # saw them, and its 401s left the response without CORS headers, breaking
    # every browser-based client of /v1 the moment a key was set.
    #
    # The explicit `method != "OPTIONS"` skip below is a second, independent
    # layer, not a substitute for the ordering above: it protects a bare
    # OPTIONS request that carries no Origin/Access-Control-Request-Method
    # (so CORSMiddleware treats it as an ordinary request and passes it
    # through rather than answering it). That is still not a bypass, because
    # no /v1 route defines an OPTIONS handler - every one is GET or POST - so
    # such a request reaches no handler that reads or changes anything; it
    # falls through to Starlette's routing, which answers 405 Method Not
    # Allowed. Nothing that can carry request data ever skips the check.
    @app.middleware("http")
    async def _guard_v1(request: Request, call_next):
        if request.url.path.startswith("/v1/") and request.method != "OPTIONS":
            denied = _check_token(request)
            if denied is not None:
                return denied
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    @app.get("/models")
    async def list_models_bare():
        """Same listing as /v1/models, at the bare path some clients expect
        (e.g. GPT4All's own default port for this, 4891). Outside /v1, so
        it is not behind the auth_token guard - same precedent as the
        dashboard's /api routes (ADR 0009): the service binds to
        127.0.0.1 only. The dashboard's own Models page lives at
        /models_catalog, not here - see render.py's _page_href.
        """
        return {"object": "list", "data": _model_entries(get_router())}

    app.include_router(v1)
    app.include_router(api)
    app.include_router(dashboard_pages)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
