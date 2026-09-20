"""The flexrouter daemon: one process, one port, everything on it.

Replaces the two stdlib ``http.server`` instances this used to run (dashboard
on 7352, OpenAI API on 7353). Those shared a single FlexRouter across
ThreadingMixIn worker threads while the router drove one non-thread-safe
asyncio loop via ``run_until_complete`` — so the second overlapping request
either raised "this event loop is already running" or deadlocked. That is not
a race you can win with locks; it needs the server to own the loop, which is
what this does. Endpoints that touch the router are ``async def`` and await it
directly. Nothing here creates an event loop.

Layout on the single port:
    /v1/*     OpenAI-compatible API (what chat clients point at)
    /api/*    dashboard data
    /*        the dashboard SPA
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from flexrouter.dashboard.api import (
    get_config, get_config_validation, get_health_current, get_last_refresh,
    get_logs, get_stats, get_status, get_uptime, post_config, run_refresh,
)
from flexrouter.exceptions import RouterBusy, RouterError
from flexrouter.probe import probe_key, stale_models
from flexrouter.redact import scrub
from flexrouter.wire import bucket_id, model_id, parse_model, resolve

STATIC_DIR = Path(__file__).parent / "dashboard" / "static"

# The library blocks indefinitely when a tier is saturated, which is the right
# default for a script. A server must not: an HTTP client that never gets a
# response is indistinguishable from a crash, and that is how this was
# reported. Cap how long routing may spend looking for a free slot.
ROUTE_TIMEOUT_SECONDS = float(os.environ.get("FLEXROUTER_ROUTE_TIMEOUT", "90"))

_TIMEOUT_HINT = (
    "No model in tier {tier!r} became available within {secs:g}s - every model "
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
        from flexrouter._router import FlexRouter
        state.router = FlexRouter(state.config_path)
    return state.router


def _state_dir() -> str:
    return get_router()._cfg.state_dir


def openai_error(message: str, error_type: str = "server_error",
                 code: str | None = None, status: int = 500) -> JSONResponse:
    # Scrubbed here, at the single exit, rather than at each raise site. A
    # raise site added later would otherwise be a leak nobody notices.
    err: dict = {"message": scrub(message), "type": error_type}
    if code is not None:
        err["code"] = code
    return JSONResponse({"error": err}, status_code=status)


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
    try:
        tier = resolve(parse_model(model), list(router._cfg.tiers.keys()),
                       _best_bucket(router))
    except KeyError as exc:
        return openai_error(str(exc.args[0]), "invalid_request_error",
                            "model_not_found", 404)
    kwargs = _kwargs_from(body)

    if body.get("stream"):
        return StreamingResponse(
            _stream_chat(router, messages, tier, model, kwargs),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        result = await asyncio.wait_for(
            router.agenerate(messages, tier, **kwargs),
            timeout=ROUTE_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, TimeoutError):
        return openai_error(
            _TIMEOUT_HINT.format(tier=tier, secs=ROUTE_TIMEOUT_SECONDS),
            "server_error", "provider_unavailable", 504)
    except RouterBusy as exc:
        return openai_error(str(exc), "server_error", "provider_unavailable", 503)
    except RouterError as exc:
        return openai_error(str(exc), "invalid_request_error", status=400)
    except KeyError:
        # The pin engine raises KeyError for a model that is not in the
        # settings at all. The client asked for something specific by name;
        # say so rather than returning a bare 500.
        return openai_error(
            f"There is no bucket or model named {model!r}.",
            "invalid_request_error", "model_not_found", 404)
    except Exception as exc:  # noqa: BLE001
        return openai_error(str(exc))

    result.setdefault("id", f"chatcmpl-{uuid.uuid4().hex[:29]}")
    result.setdefault("object", "chat.completion")
    result.setdefault("created", int(time.time()))
    result.setdefault("model", model)
    return result


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload) + "\n\n"


def _sse_error(message: str, error_type: str = "server_error",
               code: str | None = None) -> str:
    err: dict = {"message": scrub(message), "type": error_type}
    if code is not None:
        err["code"] = code
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
                       kwargs: dict) -> AsyncIterator[str]:
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
                    code: str | None = None) -> str:
        """One chunk a client can actually parse, carrying the failure.

        A bare {"error": ...} object is what this used to send; a client that
        expects every payload to be a chat.completion.chunk throws on it and
        shows the user nothing, losing the partial answer that already
        arrived. This uses the same envelope as every other chunk, so a client
        that ignores unknown keys renders the partial answer and stops
        cleanly.
        """
        err: dict = {"message": scrub(message), "type": error_type}
        if code is not None:
            err["code"] = code
        return chunk([{"index": 0, "delta": {}, "finish_reason": "error"}],
                     error=err)

    try:
        events = router.agenerate_stream(messages, tier, **kwargs).__aiter__()
        first = True
        emitted = False       # at least one content chunk has gone out
        while True:
            try:
                if first:
                    event = await asyncio.wait_for(
                        events.__anext__(), timeout=ROUTE_TIMEOUT_SECONDS)
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
                # Routing-progress chatter, not output. The bound must stay
                # armed until real content arrives, or a tier that keeps
                # retrying resets the clock forever.
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
        if emitted:
            yield error_chunk(str(exc), "server_error", "provider_unavailable")
        else:
            yield _sse_error(str(exc), "server_error", "provider_unavailable")
        yield "data: [DONE]\n\n"
    except RouterError as exc:
        if emitted:
            yield error_chunk(str(exc), "invalid_request_error")
        else:
            yield _sse_error(str(exc), "invalid_request_error")
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

    result = await probe_key(base_url, body.get("api_key"))
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
    result = await probe_key(pcfg.base_url, keys[0] if keys else None)
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

    app = FastAPI(title="flexrouter", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )
    app.include_router(v1)
    app.include_router(api)

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        """Serve the dashboard, falling back to index.html for SPA routes."""
        if full_path:
            candidate = STATIC_DIR / full_path
            if candidate.is_file():
                try:
                    candidate.resolve().relative_to(STATIC_DIR.resolve())
                except ValueError:
                    return JSONResponse({"error": "not found"}, status_code=404)
                return FileResponse(candidate)
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse(
            {"error": "dashboard not built; run `npm run build` in dashboard/frontend"},
            status_code=503,
        )

    return app
