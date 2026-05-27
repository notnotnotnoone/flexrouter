from __future__ import annotations
import asyncio
import threading
import time
from pathlib import Path
from typing import Optional

from flexrouter.audit import AuditLogger
from flexrouter.client import AsyncClient, RateLimitError, ProviderError
from flexrouter.config import FlexConfig, load_config, discover_config
from flexrouter.engine import RoutingEngine
from flexrouter.exceptions import ConfigError, RouterBusy, RouterError
from flexrouter.hooks import HookRunner, HookContext


class FlexRouter:
    def __init__(self, config_path: Optional[str] = None) -> None:
        if config_path:
            path = Path(config_path)
        else:
            path = discover_config()
        if not path or not path.exists():
            raise ConfigError("No flexrouter.yaml found. Pass path or create ./flexrouter.yaml")

        self._config_path = path
        self._cfg: FlexConfig = load_config(path)
        self._engine = RoutingEngine(self._cfg)
        self._audit = AuditLogger(self._cfg.state_dir)
        self._client = AsyncClient()
        self._hooks = HookRunner()
        self._loop = asyncio.new_event_loop()
        self._loop_lock = threading.Lock()
        try:
            self._last_mtime: float = self._config_path.stat().st_mtime
        except OSError:
            self._last_mtime = 0.0

    def generate(
        self,
        messages: list[dict],
        tier: str,
        wait: bool = True,
        vision: bool = False,
        session_id: Optional[str] = None,
        **kwargs,
    ) -> dict:
        with self._loop_lock:
            return self._loop.run_until_complete(
                self.agenerate(messages, tier, wait=wait, vision=vision, session_id=session_id, **kwargs)
            )

    async def agenerate(
        self,
        messages: list[dict],
        tier: str,
        wait: bool = True,
        vision: bool = False,
        session_id: Optional[str] = None,
        **kwargs,
    ) -> dict:
        self._maybe_hot_reload()

        # Run hooks
        ctx = HookContext(messages=messages, hooks=self._cfg.hooks, vision=vision)
        ctx = self._hooks.run(ctx)
        vision = ctx.vision
        estimated_tokens = ctx.estimated_tokens

        retries = self._cfg.retry.retries
        backoff = self._cfg.retry.backoff_seconds

        for attempt in range(retries + 1):
            route = self._engine.select(tier, estimated_tokens, vision, session_id)

            if route is None:
                if not wait:
                    raise RouterBusy(f"All models in tier {tier!r} are unavailable")
                secs = self._engine.seconds_until_available(tier)
                await asyncio.sleep(max(secs, 1.0))
                continue

            start = time.monotonic()
            try:
                result = await self._client.chat(route, messages, **kwargs)
            except RateLimitError:
                self._engine.penalize(route.provider, route.model)
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="rate_limited",
                )
                if attempt == retries:
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue
            except RouterError:
                raise
            except ProviderError:
                self._engine.penalize(route.provider, route.model)
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="error",
                )
                if attempt == retries:
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue

            latency_ms = int((time.monotonic() - start) * 1000)
            usage = result.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)

            self._engine.record_request(route.provider, route.model, total_tokens)
            self._audit.log(
                tier=tier,
                provider=route.provider,
                model=route.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=0.0,
                latency_ms=latency_ms,
                status="ok",
            )
            return result

        raise RouterBusy(f"All models in tier {tier!r} are unavailable after {retries} retries")

    def reload(self) -> None:
        self._cfg = load_config(self._config_path)
        self._engine.update_config(self._cfg)
        self._audit = AuditLogger(self._cfg.state_dir)

    def close(self) -> None:
        self._loop.close()

    def __enter__(self) -> "FlexRouter":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def _maybe_hot_reload(self) -> None:
        try:
            mtime = self._config_path.stat().st_mtime
            if mtime != self._last_mtime:
                self._last_mtime = mtime
                self.reload()
        except OSError:
            pass
