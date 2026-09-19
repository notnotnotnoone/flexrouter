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
    err: dict = {"message": message, "type": error_type}
    if code is not None:
        err["code"] = code
    return JSONResponse({"error": err}, status_code=status)


# --------------------------------------------------------------------------
# OpenAI-compatible API
# --------------------------------------------------------------------------

v1 = APIRouter(prefix="/v1")


@v1.get("/models")
async def list_models():
    router = get_router()
    tiers = router._cfg.tiers
    models: list[dict] = [
        {"id": "auto", "object": "model", "created": 0, "owned_by": "flexrouter"}
    ]

    for tier_name, model_configs in tiers.items():
        providers = sorted({mc.provider for mc in model_configs})
        models.append({
            "id": f"auto-{tier_name}",
            "object": "model",
            "created": 0,
            "owned_by": ", ".join(providers),
        })

    # Every concrete (tier, provider, model) this router can reach, with the
    # routing metadata that explains why it would or wouldn't be chosen.
    # Discovery only — sending one of these back as `model` still resolves via
    # its tier rather than pinning that exact model.
    penalties = router._engine._penalties
    seen: set[tuple[str, str, str]] = set()
    for tier_name, model_configs in tiers.items():
        for mc in model_configs:
            key = (tier_name, mc.provider, mc.model)
            if key in seen:
                continue
            seen.add(key)
            models.append({
                "id": f"{tier_name}::{mc.provider}/{mc.model}",
                "object": "model",
                "created": 0,
                "owned_by": mc.provider,
                "flexrouter": {
                    "tier": tier_name,
                    "provider": mc.provider,
                    "model": mc.model,
                    "score": mc.score,
                    "rpm": mc.rpm,
                    "tpm": mc.tpm,
                    "context_window": mc.context_window,
                    "vision": mc.vision,
                    "quarantined": penalties.is_quarantined(mc.provider, mc.model),
                    "quarantine_reason": penalties.quarantine_reason(mc.provider, mc.model),
                },
            })

    return {"object": "list", "data": models}


def _parse_model_to_tier(model: str) -> str:
    if model == "auto":
        return "auto"
    if model.startswith("auto-"):
        return model[len("auto-"):]
    if "::" in model:
        return model.split("::", 1)[0]
    return "default"


def _resolve_tier(router, tier: str) -> str:
    available = list(router._cfg.tiers.keys())
    if not available:
        return "default"
    if tier == "auto":
        best_score = None
        best_tier = available[0]
        for name in available:
            for mc in router._cfg.tiers[name]:
                if best_score is None or mc.score > best_score:
                    best_score, best_tier = mc.score, name
        return best_tier
    return tier if tier in available else available[0]


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

    model = body.get("model", "auto-default")
    router = get_router()
    tier = _resolve_tier(router, _parse_model_to_tier(model))
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
    err: dict = {"message": message, "type": error_type}
    if code is not None:
        err["code"] = code
    return _sse({"error": err})


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

    try:
        events = router.agenerate_stream(messages, tier, **kwargs).__aiter__()
        first = True
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
                yield _sse_error(
                    _TIMEOUT_HINT.format(tier=tier, secs=ROUTE_TIMEOUT_SECONDS),
                    "server_error", "provider_unavailable")
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

            elif isinstance(event, ReasoningDeltaEvent):
                yield chunk([{"index": 0,
                              "delta": {"reasoning_content": event.text},
                              "finish_reason": None}])

            elif isinstance(event, ToolCallDeltaEvent):
                delta: dict = {}
                if event.id is not None:
                    delta["id"] = event.id
                    delta["type"] = "function"
                if event.name is not None:
                    delta["function"] = {"name": event.name}
                if event.arguments is not None:
                    delta.setdefault("function", {})["arguments"] = event.arguments
                yield chunk([{"index": 0,
                              "delta": {"tool_calls": [delta] if delta else []},
                              "finish_reason": None}])

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

        yield _sse_error("No model available", "server_error", "provider_unavailable")
        yield "data: [DONE]\n\n"

    except RouterBusy as exc:
        yield _sse_error(str(exc), "server_error", "provider_unavailable")
        yield "data: [DONE]\n\n"
    except RouterError as exc:
        yield _sse_error(str(exc), "invalid_request_error")
        yield "data: [DONE]\n\n"
    except Exception as exc:  # noqa: BLE001
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
