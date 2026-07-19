from __future__ import annotations
import asyncio
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Literal, Optional

from flexrouter.audit import AuditLogger
from flexrouter.client import AsyncClient, RateLimitError, ProviderError
from flexrouter.config import FlexConfig, load_config, discover_config
from flexrouter.engine import RoutingEngine
from flexrouter.events import EventLogger
from flexrouter.exceptions import ConfigError, RouterBusy, RouterError
from flexrouter.health_history import HealthHistory
from flexrouter.hooks import HookRunner, HookContext
from flexrouter.rate_limits import RateLimitStore
from flexrouter.recovery import PenaltyBox
from flexrouter.sampler import PassiveSampler


@dataclass
class AttemptEvent:
    attempt: int
    max_attempts: int
    provider: str
    model: str


@dataclass
class AttemptFailedEvent:
    attempt: int
    max_attempts: int
    provider: str
    model: str
    reason: Literal["rate_limited", "provider_error"]


@dataclass
class DeltaEvent:
    text: str


@dataclass
class DoneEvent:
    result: dict


StreamEvent = AttemptEvent | AttemptFailedEvent | DeltaEvent | DoneEvent


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
        self._rate_limit_store = RateLimitStore(self._cfg.state_dir)
        self._events = EventLogger(self._cfg.state_dir)
        self._penalties = PenaltyBox(
            self._cfg.penalty_base_seconds, self._cfg.penalty_max_seconds,
            state_dir=self._cfg.state_dir, on_event=self._events.record,
        )
        self._engine = RoutingEngine(
            self._cfg, rate_limit_store=self._rate_limit_store, penalties=self._penalties)
        self._audit = AuditLogger(self._cfg.state_dir)
        self._history = HealthHistory(self._cfg.state_dir, self._cfg.health_history_days)
        self._sampler = PassiveSampler(
            self._engine.health_snapshot, self._history.record,
            self._cfg.sample_interval_seconds)
        self._sampler.start()
        self._client = AsyncClient(rate_limit_store=self._rate_limit_store)
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
                self._events.record(
                    route.provider, route.model, "rate_limited",
                    penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="rate_limited",
                )
                self._history.record(self._engine.health_snapshot())
                if attempt == retries:
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue
            except RouterError:
                raise
            except ProviderError:
                self._engine.penalize(route.provider, route.model)
                self._events.record(
                    route.provider, route.model, "server_error",
                    penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="error",
                )
                self._history.record(self._engine.health_snapshot())
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
            self._history.record(self._engine.health_snapshot())
            return result

        raise RouterBusy(f"All models in tier {tier!r} are unavailable after {retries} retries")

    async def agenerate_stream(
        self,
        messages: list[dict],
        tier: str,
        vision: bool = False,
        session_id: Optional[str] = None,
        **kwargs,
    ) -> AsyncIterator[StreamEvent]:
        self._maybe_hot_reload()

        # Run hooks
        ctx = HookContext(messages=messages, hooks=self._cfg.hooks, vision=vision)
        ctx = self._hooks.run(ctx)
        vision = ctx.vision
        estimated_tokens = ctx.estimated_tokens

        retries = self._cfg.retry.retries
        backoff = self._cfg.retry.backoff_seconds
        max_attempts = retries + 1

        for attempt in range(retries + 1):
            route = self._engine.select(tier, estimated_tokens, vision, session_id)

            if route is None:
                secs = self._engine.seconds_until_available(tier)
                await asyncio.sleep(max(secs, 1.0))
                continue

            yield AttemptEvent(
                attempt=attempt + 1, max_attempts=max_attempts,
                provider=route.provider, model=route.model,
            )

            start = time.monotonic()
            stream = self._client.stream_chat(route, messages, **kwargs)

            # Pull the first chunk under the same try/except agenerate() uses
            # for its whole request, since stream_chat() only raises
            # RateLimitError/ProviderError/RouterError before its first
            # yield. Once we're past this point without an exception, no
            # further failure may trigger a retry (no retry after commit).
            try:
                first_chunk = await stream.__anext__()
            except StopAsyncIteration:
                first_chunk = None
            except RateLimitError:
                self._engine.penalize(route.provider, route.model)
                self._events.record(
                    route.provider, route.model, "rate_limited",
                    penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="rate_limited",
                )
                self._history.record(self._engine.health_snapshot())
                yield AttemptFailedEvent(
                    attempt=attempt + 1, max_attempts=max_attempts,
                    provider=route.provider, model=route.model, reason="rate_limited",
                )
                if attempt == retries:
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue
            except RouterError:
                raise
            except ProviderError:
                self._engine.penalize(route.provider, route.model)
                self._events.record(
                    route.provider, route.model, "server_error",
                    penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="error",
                )
                self._history.record(self._engine.health_snapshot())
                yield AttemptFailedEvent(
                    attempt=attempt + 1, max_attempts=max_attempts,
                    provider=route.provider, model=route.model, reason="provider_error",
                )
                if attempt == retries:
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue

            # Committed: a delta (or a clean empty stream) arrived with no
            # error. From here on, any failure propagates as a plain
            # exception out of this generator — no except clause below, so
            # nothing here can turn it into a retry.
            accumulated: list[str] = []
            if first_chunk is not None:
                accumulated.append(first_chunk)
                yield DeltaEvent(text=first_chunk)

            async for chunk in stream:
                accumulated.append(chunk)
                yield DeltaEvent(text=chunk)

            latency_ms = int((time.monotonic() - start) * 1000)
            full_text = "".join(accumulated)
            result = {
                "choices": [{"message": {"role": "assistant", "content": full_text}}],
                "usage": {},
            }
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
            self._history.record(self._engine.health_snapshot())
            yield DoneEvent(result=result)
            return

        raise RouterBusy(f"All models in tier {tier!r} are unavailable after {retries} retries")

    def reload(self) -> None:
        self._cfg = load_config(self._config_path)
        self._rate_limit_store = RateLimitStore(self._cfg.state_dir)
        self._engine.update_config(self._cfg)
        self._engine._rate_limit_store = self._rate_limit_store
        self._client._rate_limit_store = self._rate_limit_store
        self._audit = AuditLogger(self._cfg.state_dir)
        self._history = HealthHistory(self._cfg.state_dir, self._cfg.health_history_days)

    def close(self) -> None:
        self._sampler.stop()
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
