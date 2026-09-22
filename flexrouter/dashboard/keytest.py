"""Sends one real request to prove a key still works.

The Cerebras case is the reason this exists: a model-list lookup
(flexrouter/probe.py) can say a key is fine while every real completion on
it fails with 402. Only a real chat completion catches that (spec §7,
Stage 8 roadmap sub-plan 3). This is a short-lived, direct use of
flexrouter.client.AsyncClient - not routed through engine.py, and its
result is not persisted anywhere. Testing a key is something the owner
asks for by hand; it must not silently change the key's real usage
counters or cooldown state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from flexrouter.client import AsyncClient, ProviderError, RateLimitError
from flexrouter.dashboard.facts import _models_of
from flexrouter.engine import RouteResult
from flexrouter.exceptions import RouterError
from flexrouter.keys import allows


@dataclass
class KeyTestResult:
    ok: bool
    message: str
    status_code: Optional[int] = None


async def test_key(router, provider: str, key_id: str) -> KeyTestResult:
    pcfg = router._cfg.providers.get(provider)
    if pcfg is None:
        return KeyTestResult(ok=False, message=f"no such provider: {provider}")

    record = next((r for r in (pcfg.keys or []) if r.id == key_id), None)
    if record is None:
        return KeyTestResult(ok=False, message=f"no such key: {key_id}")

    model = next((m for m in _models_of(router, provider) if allows(record, m)), None)
    if model is None:
        return KeyTestResult(ok=False, message="no model is configured for this key to test with")

    route = RouteResult(
        provider=provider, model=model, api_key=record.secret,
        base_url=pcfg.base_url, tier="dashboard-test",
        header_parser=pcfg.header_parser,
    )
    client = AsyncClient()
    try:
        await client.chat(route, [{"role": "user", "content": "ping"}], max_tokens=1)
    except RateLimitError:
        return KeyTestResult(ok=False, message="rate limited right now - the key itself may still be fine")
    except RouterError as exc:
        return KeyTestResult(ok=False, message=str(exc))
    except ProviderError as exc:
        return KeyTestResult(ok=False, message=str(exc), status_code=exc.status_code)
    else:
        return KeyTestResult(ok=True, message=f"{model} answered")
    finally:
        await client._client.aclose()
