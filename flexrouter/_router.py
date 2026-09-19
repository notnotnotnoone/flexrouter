from __future__ import annotations
import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Literal, Optional

from flexrouter.audit import AuditLogger
from flexrouter.client import AsyncClient, RateLimitError, ProviderError
from flexrouter.config import FlexConfig, load_config
from flexrouter.engine import RoutingEngine
from flexrouter.events import EventLogger
from flexrouter.exceptions import RouterBusy, RouterError
from flexrouter.health_history import HealthHistory
from flexrouter.hooks import HookRunner, HookContext
from flexrouter.quota import QuotaTracker
from flexrouter.rate_limits import RateLimitStore
from flexrouter.recovery import PenaltyBox
from flexrouter.sampler import PassiveSampler

logger = logging.getLogger(__name__)


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
    detail: str = ""


@dataclass
class DeltaEvent:
    text: str


@dataclass
class ReasoningDeltaEvent:
    text: str


@dataclass
class ToolCallDeltaEvent:
    index: int
    id: Optional[str]
    name: Optional[str]
    arguments: Optional[str]


@dataclass
class DoneEvent:
    result: dict


StreamEvent = AttemptEvent | AttemptFailedEvent | DeltaEvent | ReasoningDeltaEvent | ToolCallDeltaEvent | DoneEvent


class FlexRouter:
    def __init__(self, config_path: Optional[str] = None) -> None:
        from flexrouter import home

        path = Path(config_path) if config_path else home.config_path()
        self._config_path = path
        self._cfg: FlexConfig = load_config(path if config_path else None)
        self._rate_limit_store = RateLimitStore(self._cfg.state_dir)
        self._quota_tracker = QuotaTracker(self._cfg.state_dir)
        self._events = EventLogger(self._cfg.state_dir)
        self._penalties = PenaltyBox(
            self._cfg.penalty_base_seconds, self._cfg.penalty_max_seconds,
            state_dir=self._cfg.state_dir, on_event=self._events.record,
        )
        self._engine = RoutingEngine(
            self._cfg, rate_limit_store=self._rate_limit_store, penalties=self._penalties,
            quota_tracker=self._quota_tracker)
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

    def _quarantine_block_reason(self, tier: str) -> str | None:
        """Explain a tier that is empty only because everything is quarantined.

        Waiting is the right answer to a rate limit — a slot opens shortly.
        It is the wrong answer to a model the provider deleted, which will
        still be deleted in 24 hours. Without this check, wait=True sleeps on
        a tier that cannot recover, which looks exactly like a hang.
        """
        models = self._cfg.tiers.get(tier) or []
        if not models:
            return None
        if not all(self._penalties.is_quarantined(m.provider, m.model)
                   for m in models):
            return None

        lines: list[str] = []
        for m in models:
            reason = self._penalties.quarantine_reason(m.provider, m.model) or "unavailable"
            line = f"{m.provider}/{m.model}: {reason.splitlines()[0][:120]}"
            if line not in lines:
                lines.append(line)
        shown = lines[:5]
        if len(lines) > len(shown):
            shown.append(f"...and {len(lines) - len(shown)} more")
        joined = ("\n  ").join(shown)
        return (f"Every model in tier {tier!r} is quarantined - this will "
                f"not clear on its own:\n  {joined}")

    def _handle_auth_failure(self, route, exc) -> None:
        """Sideline a whole provider after it rejects our credentials.

        Auth failures used to abort the entire request. That made one expired
        key look like total breakage: every tier containing that provider
        failed, even when the other providers in the tier were healthy. The
        key is a property of the provider, so quarantine the provider and let
        the retry loop rotate to a different one.
        """
        self._penalties.quarantine_provider(route.provider, str(exc))
        self._events.record(
            route.provider, route.model, "server_error",
            detail=f"provider quarantined (auth): {exc}")

    def _handle_provider_error(self, route, exc) -> None:
        """Sideline a route after a provider error, and record why.

        A permanent status (the provider saying the model is gone) quarantines
        the route instead of penalizing it — otherwise the backoff loop retries
        a deleted model every 30 seconds until the heat death of the universe.
        """
        if exc.is_provider_wide:
            self._penalties.quarantine_provider(route.provider, str(exc))
            self._events.record(
                route.provider, route.model, "server_error",
                detail=f"provider quarantined: {exc}")
            return
        if exc.is_permanent:
            self._penalties.quarantine(route.provider, route.model, str(exc))
            self._events.record(
                route.provider, route.model, "server_error",
                detail=f"quarantined: {exc}")
            return
        self._engine.penalize(route.provider, route.model)
        self._events.record(
            route.provider, route.model, "server_error",
            detail=str(exc),
            penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))

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

        # If credentials are what emptied the tier, the caller needs to hear
        # that rather than a generic "everything is busy" — one is a config
        # problem they must fix, the other resolves itself in 30 seconds.
        last_auth_error: RouterError | None = None

        for attempt in range(retries + 1):
            route = self._engine.select(tier, estimated_tokens, vision, session_id)

            if route is None:
                if last_auth_error is not None:
                    raise last_auth_error
                blocked = self._quarantine_block_reason(tier)
                if blocked:
                    raise RouterBusy(blocked)
                if not wait:
                    raise RouterBusy(f"All models in tier {tier!r} are unavailable")
                secs = self._engine.seconds_until_available(tier)
                await asyncio.sleep(max(secs, 1.0))
                continue

            start = time.monotonic()
            try:
                result = await self._client.chat(route, messages, **kwargs)
            except RateLimitError as exc:
                self._engine.penalize(route.provider, route.model)
                self._events.record(
                    route.provider, route.model, "rate_limited",
                    detail=str(exc),
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
            except RouterError as exc:
                self._handle_auth_failure(route, exc)
                last_auth_error = exc
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="auth_error",
                )
                self._history.record(self._engine.health_snapshot())
                # No backoff: a rejected key won't un-reject in two seconds.
                if attempt == retries:
                    raise
                continue
            except ProviderError as exc:
                self._handle_provider_error(route, exc)
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
            self._quota_tracker.record(route.provider, route.model)
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
                # Unlike the sync path this loop has no wait=False escape, so
                # without the quarantine check it sleeps forever on a tier
                # that can never come back — the reported hang, via the
                # streaming route a chat client actually uses.
                blocked = self._quarantine_block_reason(tier)
                if blocked:
                    raise RouterBusy(blocked)
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
                # Empty stream — no content at all. Treat as a provider
                # error so the retry loop rotates to the next model.
                self._engine.penalize(route.provider, route.model)
                self._events.record(
                    route.provider, route.model, "server_error",
                    detail="empty stream response",
                    penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="empty_response",
                )
                self._history.record(self._engine.health_snapshot())
                yield AttemptFailedEvent(
                    attempt=attempt + 1, max_attempts=max_attempts,
                    provider=route.provider, model=route.model, reason="provider_error",
                    detail="empty stream response",
                )
                if attempt == retries:
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue
            except RateLimitError as exc:
                self._engine.penalize(route.provider, route.model)
                self._events.record(
                    route.provider, route.model, "rate_limited",
                    detail=str(exc),
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
                    detail=str(exc),
                )
                if attempt == retries:
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue
            except RouterError as exc:
                self._handle_auth_failure(route, exc)
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="auth_error",
                )
                self._history.record(self._engine.health_snapshot())
                yield AttemptFailedEvent(
                    attempt=attempt + 1, max_attempts=max_attempts,
                    provider=route.provider, model=route.model,
                    reason="provider_error", detail=str(exc),
                )
                if attempt == retries:
                    raise
                continue
            except ProviderError as exc:
                self._handle_provider_error(route, exc)
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
                    detail=str(exc),
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
            final_usage: dict = {}
            has_tool_calls = False
            # key -> {"id": ..., "name": ..., "arguments": ...}. Keyed on the
            # chunk's own "id" rather than "index": per the OpenAI streaming
            # tool_calls spec, a non-empty id always marks the *start* of a
            # new logical call (continuation chunks omit it), which holds
            # even for providers that don't bump "index" per call. Gemini's
            # OpenAI-compat endpoint (live-verified) sends every call in a
            # multi-call turn at index 0, each with a unique id and its
            # *complete* arguments in one chunk — keying on index alone
            # merged them into one entry and concatenated their JSON into an
            # unparseable blob.
            tool_calls_by_key: dict[str, dict] = {}
            tool_call_order: list[str] = []
            last_key_by_index: dict[int, str] = {}

            def _events_for(sc) -> list:
                nonlocal has_tool_calls
                events: list = []
                if sc.content:
                    accumulated.append(sc.content)
                    events.append(DeltaEvent(text=sc.content))
                if sc.reasoning:
                    events.append(ReasoningDeltaEvent(text=sc.reasoning))
                if sc.tool_call_delta:
                    has_tool_calls = True
                    tc = sc.tool_call_delta
                    idx = tc.get("index", 0)
                    call_id = tc.get("id")
                    if call_id:
                        key = call_id
                        if key not in tool_calls_by_key:
                            tool_calls_by_key[key] = {"id": call_id, "name": "", "arguments": ""}
                            tool_call_order.append(key)
                        last_key_by_index[idx] = key
                    else:
                        key = last_key_by_index.get(idx)
                        if key is None:
                            # No id and no prior chunk at this index — start
                            # a fresh entry rather than dropping the chunk.
                            key = f"idx:{idx}"
                            tool_calls_by_key[key] = {"id": "", "name": "", "arguments": ""}
                            tool_call_order.append(key)
                            last_key_by_index[idx] = key
                    entry = tool_calls_by_key[key]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        entry["name"] = fn["name"]
                    if fn.get("arguments"):
                        entry["arguments"] += fn["arguments"]
                    events.append(ToolCallDeltaEvent(
                        index=idx,
                        id=tc.get("id"),
                        name=fn.get("name"),
                        arguments=fn.get("arguments"),
                    ))
                if sc.usage:
                    final_usage.update(sc.usage)
                return events

            if first_chunk is not None:
                for ev in _events_for(first_chunk):
                    yield ev

            async for sc in stream:
                for ev in _events_for(sc):
                    yield ev

            latency_ms = int((time.monotonic() - start) * 1000)
            full_text = "".join(accumulated)

            logger.info(
                "stream accumulation complete",
                extra={
                    "event": "router.stream.accumulated",
                    "provider": route.provider, "model": route.model,
                    "attempt": attempt + 1, "max_attempts": max_attempts,
                    "full_text_len": len(full_text),
                    "has_tool_calls": has_tool_calls,
                    "tool_call_count": len(tool_calls_by_key),
                    "tool_calls_raw": tool_calls_by_key,
                },
            )

            # A stream that ends with no content and no tool calls has shown
            # the consumer nothing (no DeltaEvent, no ToolCallDeltaEvent —
            # both are only yielded when accumulated/has_tool_calls end up
            # non-empty), so it's safe to retry against the next provider
            # exactly like the pre-first-chunk failures above, instead of
            # treating "we received at least one chunk" as an irreversible
            # commit. Without this, a model that spends its whole token
            # budget on reasoning (nothing usable back) looks identical to a
            # real success and the caller is left with a silent empty reply.
            if not full_text and not has_tool_calls:
                logger.warning(
                    "empty completion, retrying next provider",
                    extra={
                        "event": "router.stream.empty_completion",
                        "provider": route.provider, "model": route.model,
                        "attempt": attempt + 1, "max_attempts": max_attempts,
                    },
                )
                self._engine.penalize(route.provider, route.model)
                self._events.record(
                    route.provider, route.model, "server_error",
                    detail="empty response (no content, no tool calls)",
                    penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=latency_ms, status="empty_response",
                )
                self._history.record(self._engine.health_snapshot())
                yield AttemptFailedEvent(
                    attempt=attempt + 1, max_attempts=max_attempts,
                    provider=route.provider, model=route.model, reason="provider_error",
                    detail="empty response (no content, no tool calls)",
                )
                if attempt == retries:
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue

            # Assemble tool calls in first-seen order
            assembled_tool_calls = []
            for key in tool_call_order:
                tc = tool_calls_by_key[key]
                assembled_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    },
                })

            if assembled_tool_calls:
                logger.info(
                    "committing stream with tool calls",
                    extra={
                        "event": "router.stream.tool_calls_committed",
                        "provider": route.provider, "model": route.model,
                        "attempt": attempt + 1, "max_attempts": max_attempts,
                        "tool_calls": [
                            {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}
                            for tc in assembled_tool_calls
                        ],
                    },
                )

            # Build result with content + tool_calls
            msg: dict = {"role": "assistant", "content": full_text}
            if assembled_tool_calls:
                msg["tool_calls"] = assembled_tool_calls

            result = {
                "choices": [{"message": msg}],
                "usage": final_usage,
            }
            usage = result.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)

            self._engine.record_request(route.provider, route.model, total_tokens)
            self._quota_tracker.record(route.provider, route.model)
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
        self._quota_tracker = QuotaTracker(self._cfg.state_dir)
        self._engine.update_config(self._cfg)
        self._engine._rate_limit_store = self._rate_limit_store
        self._engine._quota_tracker = self._quota_tracker
        self._client._rate_limit_store = self._rate_limit_store
        self._audit = AuditLogger(self._cfg.state_dir)
        self._history = HealthHistory(self._cfg.state_dir, self._cfg.health_history_days)

    def remaining_capacity(self, tier: str) -> dict[str, dict]:
        """For every model in `tier` currently in the running for selection, return
        {"provider/model": {"rpm_remaining": int, "tpm_remaining": int}} — how much of that
        model's per-minute allowance is still free right now. Read-only: does not affect
        routing/selection, does not consume any allowance itself. Raises KeyError for an
        unknown tier, same as generate()/agenerate().
        """
        self._maybe_hot_reload()
        return self._engine.remaining_capacity(tier)

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
