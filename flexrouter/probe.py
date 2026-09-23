"""Ask a provider, right now, whether a key works and what it can reach.

The catalogue's `discover_models` swallows every failure and returns an
empty list, so it cannot tell "your key is rejected" apart from "this provider
has no free models" — which is precisely the distinction a key-entry screen
exists to show. This module reports the reason.

It also answers the staleness question as a by-product: once you know which
models a key can actually reach, the models named in flexrouter.yaml that are
absent from that list are the dead ones.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from flexrouter.errors import extract_error_message

DEFAULT_TIMEOUT = 15.0


@dataclass
class ProbeResult:
    ok: bool
    models: list[str] = field(default_factory=list)
    error: str | None = None
    status_code: int | None = None
    latency_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "models": self.models,
            "model_count": len(self.models),
            "error": self.error,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
        }


def _model_ids(payload) -> list[str]:
    """Pull model ids out of an OpenAI-compatible /models response."""
    if isinstance(payload, dict):
        # `data` is the OpenAI shape; `models` is Ollama's. Both are lists
        # of rows whose id lives under `id` or `name`, which the loop below
        # already handles.
        rows = payload.get("data")
        if rows is None:
            rows = payload.get("models", payload)
    else:
        rows = payload
    if not isinstance(rows, list):
        return []
    ids = []
    for row in rows:
        if isinstance(row, dict):
            value = row.get("id") or row.get("name")
            if value:
                ids.append(str(value))
        elif isinstance(row, str):
            ids.append(row)
    return sorted(set(ids))


def _explain_status(status: int, body: str) -> str:
    """Turn a status code into something a person can act on."""
    detail = extract_error_message(body)
    hints = {
        401: "the key was rejected - check for a typo, or generate a new one",
        403: "the key was refused - it may lack permission, or be region-blocked",
        402: "the provider wants payment - check the billing page on their site",
        404: "no model list at this address - check the base URL",
        429: "rate limited right now - the key itself may still be fine",
    }
    hint = hints.get(status)
    if detail and hint:
        return f"{detail} ({hint})"
    if detail:
        return detail
    if hint:
        return f"HTTP {status} - {hint}"
    return f"HTTP {status}"


async def probe_key(base_url: str, api_key: str | None,
                    timeout: float = DEFAULT_TIMEOUT,
                    models_path: str = "/models") -> ProbeResult:
    """Test one credential against one provider and report what it can reach.

    Uses GET /models because it is the cheapest call that proves a key works
    and simultaneously answers "which models does this key actually have".
    A completion request would cost tokens and tell us less.
    """
    url = f"{base_url.rstrip('/')}/{models_path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    started = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        return ProbeResult(
            ok=False,
            error=f"no response within {timeout:g}s - is the address right?",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    except httpx.HTTPError as exc:
        return ProbeResult(
            ok=False,
            error=f"could not reach {base_url} ({type(exc).__name__})",
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    elapsed = int((time.monotonic() - started) * 1000)

    if resp.status_code >= 400:
        return ProbeResult(
            ok=False,
            error=_explain_status(resp.status_code, resp.text),
            status_code=resp.status_code,
            latency_ms=elapsed,
        )

    try:
        payload = resp.json()
    except ValueError:
        return ProbeResult(
            ok=False,
            error="the provider answered, but not with a model list - check the base URL",
            status_code=resp.status_code,
            latency_ms=elapsed,
        )

    return ProbeResult(
        ok=True,
        models=_model_ids(payload),
        status_code=resp.status_code,
        latency_ms=elapsed,
    )


def stale_models(configured: list[str], reachable: list[str]) -> list[str]:
    """Models named in the config that the provider no longer lists.

    This is the cheap half of the dead-model problem: a model absent from
    /models is almost certainly gone. The other half - a model that is listed
    but errors on use - only a real request can catch.
    """
    if not reachable:
        return []
    live = set(reachable)
    return sorted(m for m in configured if m not in live)
