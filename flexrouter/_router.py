from __future__ import annotations
import asyncio
import dataclasses
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Literal, Optional

from flexrouter.audit import AuditLogger
from flexrouter.client import AsyncClient, RateLimitError, ProviderError
from flexrouter.config import FlexConfig, load_config
from flexrouter.decider import NullDecider
from flexrouter.engine import RoutingEngine, RouteResult
from flexrouter.error_brain import ErrorBrain
from flexrouter.events import EventLogger
from flexrouter.exceptions import RouterBusy, RouterError
from flexrouter.health_history import HealthHistory
from flexrouter.hooks import HookRunner, HookContext
from flexrouter.key_state import KeyStateStore
from flexrouter.model_facts import ModelFactsStore
from flexrouter.quota import QuotaTracker
from flexrouter.rate_limits import RateLimitStore
from flexrouter.recovery import PenaltyBox
from flexrouter.redact import scrub
from flexrouter.refresh import refresh_config
from flexrouter.sampler import PassiveSampler
from flexrouter.scheduler import RoundRobinCounters, pick_key
from flexrouter.traces import TraceWriter, new_trace_id

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
    raw: dict = field(default_factory=dict)
    """The provider's own delta dictionary, unmodified.

    The four fields above are a lossy reading of it, kept because DoneEvent
    assembly and existing library callers use them. Anything forwarding this
    to a client must forward `raw`: LiteLLM's tool-call drops come from
    re-parsing, and the four fields are exactly that re-parse.
    """


@dataclass
class DoneEvent:
    result: dict


StreamEvent = AttemptEvent | AttemptFailedEvent | DeltaEvent | ReasoningDeltaEvent | ToolCallDeltaEvent | DoneEvent


class LocalRouter:
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
        self._pin_engine = self._build_pin_engine()
        self._audit = AuditLogger(self._cfg.state_dir)
        self._history = HealthHistory(self._cfg.state_dir, self._cfg.health_history_days)
        self._traces = TraceWriter(self._cfg.state_dir)
        self._key_states = KeyStateStore(self._cfg.state_dir)
        self._round_robin = RoundRobinCounters()
        self._error_brain = ErrorBrain(self._cfg.state_dir, NullDecider())
        self._model_facts = ModelFactsStore(self._cfg.state_dir)
        self._run_startup_catalogue_refresh()
        self._sampler = PassiveSampler(
            self._engine.health_snapshot, self._history.record,
            self._cfg.sample_interval_seconds)
        self._sampler.start()
        self._client = AsyncClient(rate_limit_store=self._rate_limit_store)
        self._hooks = HookRunner()
        self._loop = asyncio.new_event_loop()
        self._loop_lock = threading.Lock()
        self._known_mtimes: dict[Path, tuple] = {}
        self._last_mtime: tuple = self._newest_mtime()
        self._reload_error: str | None = None

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

    def _handle_auth_failure(self, route, exc, key_id: Optional[str]) -> None:
        """Sideline the key that was rejected, not the whole provider.

        Stage 4's whole point (spec fault 5): a rejected key used to abort
        every route through its provider, even routes using a different,
        perfectly good key. Now only that one key is benched. The provider
        itself is only quarantined when every configured key for it has
        become benched or disabled (ruling 2/4) — a fact about the account,
        not about one credential.

        The reason is scrubbed here, at the write site, for the same reason
        it always has been: this text is persisted and later served
        unauthenticated by /api/* and /v1/models.
        """
        reason = scrub(str(exc))
        provider_cfg = self._cfg.providers.get(route.provider)
        if key_id is None or not provider_cfg or not provider_cfg.keys:
            # No key identity to bench (keyless provider, or the failure
            # happened before a key was ever chosen) — fall back to the
            # pre-Stage-4 behaviour rather than silently doing nothing.
            self._penalties.quarantine_provider(route.provider, reason)
            self._events.record(
                route.provider, route.model, "server_error",
                detail=f"provider quarantined (auth): {reason}")
            return
        self._key_states.mark_benched(route.provider, key_id, reason)
        key_ids = [r.id for r in provider_cfg.keys]
        if self._key_states.all_benched_or_disabled(route.provider, key_ids):
            self._penalties.quarantine_provider(route.provider, reason)
            self._events.record(
                route.provider, route.model, "server_error",
                detail=f"provider quarantined (auth, every key benched): {reason}")
        else:
            self._events.record(
                route.provider, route.model, "server_error",
                detail=f"key benched (auth): {reason}")

    def _handle_provider_error(self, route, exc) -> None:
        """Sideline a route after a provider error, and record why.

        A permanent status (the provider saying the model is gone) quarantines
        the route instead of penalizing it — otherwise the backoff loop retries
        a deleted model every 30 seconds until the heat death of the universe.

        Scrubbed at the write site for the same reason as _handle_auth_failure:
        a provider error's text is built from the provider's own response body,
        and this reason is persisted and then served unauthenticated.
        """
        reason = scrub(str(exc))
        if exc.is_provider_wide:
            self._penalties.quarantine_provider(route.provider, reason)
            self._events.record(
                route.provider, route.model, "server_error",
                detail=f"provider quarantined: {reason}")
            return
        if exc.is_permanent:
            self._penalties.quarantine(route.provider, route.model, reason)
            self._events.record(
                route.provider, route.model, "server_error",
                detail=f"quarantined: {reason}")
            return
        self._engine.penalize(route.provider, route.model)
        self._events.record(
            route.provider, route.model, "server_error",
            detail=reason,
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

        trace_id = new_trace_id()
        request_started = time.monotonic()
        skipped = [
            {"provider": s["provider"], "model": s["model"],
             "reason": s["reason"], "detail": s["detail"]}
            for s in self._engine_for(tier).explain_unavailable(tier, estimated_tokens, vision)
            if not s["available"]
        ]
        attempts: list[dict] = []

        def _write_trace(ok: bool, answered_by: Optional[dict] = None,
                         tokens: Optional[dict] = None) -> None:
            self._traces.write({
                "id": trace_id,
                "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds") + "Z",
                "asked": {
                    "bucket": tier, "stream": False,
                    "needs": ["vision"] if vision else [],
                    "approx_input_tokens": estimated_tokens,
                },
                "skipped": skipped,
                "attempts": attempts,
                "answered_by": answered_by,
                "tokens": tokens or {"in": 0, "out": 0},
                "cost_usd": 0.0,
                "ms_total": int((time.monotonic() - request_started) * 1000),
                "ms_to_first_token": None,
                "ok": ok,
            })

        # If credentials are what emptied the tier, the caller needs to hear
        # that rather than a generic "everything is busy" — one is a config
        # problem they must fix, the other resolves itself in 30 seconds.
        last_auth_error: RouterError | None = None

        for attempt in range(retries + 1):
            route = self._engine_for(tier).select(tier, estimated_tokens, vision, session_id)
            key_id: Optional[str] = None
            if route is not None:
                route, key_id = self._pick_key(route)

            if route is None:
                if last_auth_error is not None:
                    _write_trace(ok=False)
                    raise last_auth_error
                blocked = self._quarantine_block_reason(tier)
                if blocked:
                    _write_trace(ok=False)
                    raise RouterBusy(blocked)
                if not wait:
                    _write_trace(ok=False)
                    raise RouterBusy(f"All models in tier {tier!r} are unavailable")
                secs = self._engine_for(tier).seconds_until_available(tier)
                await asyncio.sleep(max(secs, 1.0))
                continue

            start = time.monotonic()
            if key_id is not None:
                self._key_states.begin_request(route.provider, key_id)
            try:
                result = await self._client.chat(route, messages, **kwargs)
            except RateLimitError as exc:
                self._engine.penalize(route.provider, route.model)
                if key_id is not None:
                    self._key_states.mark_cooling(
                        route.provider, key_id,
                        self._penalties.penalty_seconds(route.provider, route.model),
                        "too_fast")
                self._events.record(
                    route.provider, route.model, "rate_limited",
                    detail=scrub(str(exc)),
                    penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="rate_limited",
                )
                self._history.record(self._engine.health_snapshot())
                verdict = self._error_brain.classify(str(exc), 429)
                if vision and verdict.verdict in ("bad_request", "model_gone") and \
                        verdict.confidence >= self._error_brain.confidence_threshold:
                    self._model_facts.record_contradicting_failure(
                        route.provider, route.model, "vision", trace_id)
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": 429,
                                 "provider_message": str(exc), "key_id": key_id,
                                 "verdict": verdict.verdict,
                                 "ms": int((time.monotonic() - start) * 1000)})
                if attempt == retries:
                    _write_trace(ok=False)
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue
            except RouterError as exc:
                self._handle_auth_failure(route, exc, key_id)
                last_auth_error = exc
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="auth_error",
                )
                self._history.record(self._engine.health_snapshot())
                verdict = self._error_brain.classify(str(exc), 401)
                if vision and verdict.verdict in ("bad_request", "model_gone") and \
                        verdict.confidence >= self._error_brain.confidence_threshold:
                    self._model_facts.record_contradicting_failure(
                        route.provider, route.model, "vision", trace_id)
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": None,
                                 "provider_message": str(exc), "key_id": key_id,
                                 "verdict": verdict.verdict,
                                 "ms": int((time.monotonic() - start) * 1000)})
                # No backoff: a rejected key won't un-reject in two seconds.
                if attempt == retries:
                    _write_trace(ok=False)
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
                verdict = self._error_brain.classify(str(exc), exc.status_code)
                if vision and verdict.verdict in ("bad_request", "model_gone") and \
                        verdict.confidence >= self._error_brain.confidence_threshold:
                    self._model_facts.record_contradicting_failure(
                        route.provider, route.model, "vision", trace_id)
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": exc.status_code,
                                 "provider_message": str(exc), "key_id": key_id,
                                 "verdict": verdict.verdict,
                                 "ms": int((time.monotonic() - start) * 1000)})
                if attempt == retries:
                    _write_trace(ok=False)
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue
            finally:
                if key_id is not None:
                    self._key_states.end_request(route.provider, key_id)

            latency_ms = int((time.monotonic() - start) * 1000)
            usage = result.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)

            self._engine.record_request(route.provider, route.model, total_tokens)
            self._quota_tracker.record(route.provider, route.model)
            if key_id is not None:
                self._key_states.mark_success(route.provider, key_id, total_tokens, latency_ms)
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
            if vision:
                self._model_facts.record_success(route.provider, route.model, "vision")
            _write_trace(
                ok=True,
                answered_by={"provider": route.provider, "model": route.model, "key_id": key_id},
                tokens={"in": prompt_tokens, "out": completion_tokens},
            )
            return result

        _write_trace(ok=False)
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

        trace_id = new_trace_id()
        request_started = time.monotonic()
        skipped = [
            {"provider": s["provider"], "model": s["model"],
             "reason": s["reason"], "detail": s["detail"]}
            for s in self._engine_for(tier).explain_unavailable(tier, estimated_tokens, vision)
            if not s["available"]
        ]
        attempts: list[dict] = []

        def _write_trace(ok: bool, answered_by: Optional[dict] = None,
                         tokens: Optional[dict] = None,
                         ms_to_first_token: Optional[int] = None) -> None:
            self._traces.write({
                "id": trace_id,
                "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds") + "Z",
                "asked": {
                    "bucket": tier, "stream": True,
                    "needs": ["vision"] if vision else [],
                    "approx_input_tokens": estimated_tokens,
                },
                "skipped": skipped,
                "attempts": attempts,
                "answered_by": answered_by,
                "tokens": tokens or {"in": 0, "out": 0},
                "cost_usd": 0.0,
                "ms_total": int((time.monotonic() - request_started) * 1000),
                "ms_to_first_token": ms_to_first_token,
                "ok": ok,
            })

        for attempt in range(retries + 1):
            route = self._engine_for(tier).select(tier, estimated_tokens, vision, session_id)
            key_id: Optional[str] = None
            if route is not None:
                route, key_id = self._pick_key(route)

            if route is None:
                # Unlike the sync path this loop has no wait=False escape, so
                # without the quarantine check it sleeps forever on a tier
                # that can never come back — the reported hang, via the
                # streaming route a chat client actually uses.
                blocked = self._quarantine_block_reason(tier)
                if blocked:
                    _write_trace(ok=False)
                    raise RouterBusy(blocked)
                secs = self._engine_for(tier).seconds_until_available(tier)
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
            if key_id is not None:
                self._key_states.begin_request(route.provider, key_id)
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
                verdict = self._error_brain.classify("empty stream response", None)
                if vision and verdict.verdict in ("bad_request", "model_gone") and \
                        verdict.confidence >= self._error_brain.confidence_threshold:
                    self._model_facts.record_contradicting_failure(
                        route.provider, route.model, "vision", trace_id)
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": None,
                                 "provider_message": "empty stream response",
                                 "key_id": key_id,
                                 "verdict": verdict.verdict,
                                 "ms": int((time.monotonic() - start) * 1000)})
                yield AttemptFailedEvent(
                    attempt=attempt + 1, max_attempts=max_attempts,
                    provider=route.provider, model=route.model, reason="provider_error",
                    detail="empty stream response",
                )
                if attempt == retries:
                    _write_trace(ok=False)
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue
            except RateLimitError as exc:
                self._engine.penalize(route.provider, route.model)
                if key_id is not None:
                    self._key_states.mark_cooling(
                        route.provider, key_id,
                        self._penalties.penalty_seconds(route.provider, route.model),
                        "too_fast")
                self._events.record(
                    route.provider, route.model, "rate_limited",
                    detail=scrub(str(exc)),
                    penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="rate_limited",
                )
                self._history.record(self._engine.health_snapshot())
                verdict = self._error_brain.classify(str(exc), 429)
                if vision and verdict.verdict in ("bad_request", "model_gone") and \
                        verdict.confidence >= self._error_brain.confidence_threshold:
                    self._model_facts.record_contradicting_failure(
                        route.provider, route.model, "vision", trace_id)
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": 429,
                                 "provider_message": str(exc), "key_id": key_id,
                                 "verdict": verdict.verdict,
                                 "ms": int((time.monotonic() - start) * 1000)})
                yield AttemptFailedEvent(
                    attempt=attempt + 1, max_attempts=max_attempts,
                    provider=route.provider, model=route.model, reason="rate_limited",
                    detail=str(exc),
                )
                if attempt == retries:
                    _write_trace(ok=False)
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue
            except RouterError as exc:
                self._handle_auth_failure(route, exc, key_id)
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="auth_error",
                )
                self._history.record(self._engine.health_snapshot())
                verdict = self._error_brain.classify(str(exc), 401)
                if vision and verdict.verdict in ("bad_request", "model_gone") and \
                        verdict.confidence >= self._error_brain.confidence_threshold:
                    self._model_facts.record_contradicting_failure(
                        route.provider, route.model, "vision", trace_id)
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": None,
                                 "provider_message": str(exc), "key_id": key_id,
                                 "verdict": verdict.verdict,
                                 "ms": int((time.monotonic() - start) * 1000)})
                yield AttemptFailedEvent(
                    attempt=attempt + 1, max_attempts=max_attempts,
                    provider=route.provider, model=route.model,
                    reason="provider_error", detail=str(exc),
                )
                if attempt == retries:
                    _write_trace(ok=False)
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
                verdict = self._error_brain.classify(str(exc), exc.status_code)
                if vision and verdict.verdict in ("bad_request", "model_gone") and \
                        verdict.confidence >= self._error_brain.confidence_threshold:
                    self._model_facts.record_contradicting_failure(
                        route.provider, route.model, "vision", trace_id)
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": exc.status_code,
                                 "provider_message": str(exc), "key_id": key_id,
                                 "verdict": verdict.verdict,
                                 "ms": int((time.monotonic() - start) * 1000)})
                yield AttemptFailedEvent(
                    attempt=attempt + 1, max_attempts=max_attempts,
                    provider=route.provider, model=route.model, reason="provider_error",
                    detail=str(exc),
                )
                if attempt == retries:
                    _write_trace(ok=False)
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue
            finally:
                if key_id is not None:
                    self._key_states.end_request(route.provider, key_id)

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
            first_token_at: Optional[float] = None

            def _events_for(sc) -> list:
                nonlocal has_tool_calls, first_token_at
                events: list = []
                if first_token_at is None and (sc.content or sc.reasoning or sc.tool_call_delta):
                    first_token_at = time.monotonic()
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
                        raw=tc,
                    ))
                if sc.usage:
                    final_usage.update(sc.usage)
                return events

            # Whether *anything* — content, reasoning, or a tool-call
            # fragment — has already reached the consumer on this attempt.
            # Once true, this attempt is committed: an empty completion from
            # here on is a failed answer, not a reason to try another
            # provider, because the caller's screen may already be showing
            # what was sent.
            any_yielded = False

            try:
                if first_chunk is not None:
                    for ev in _events_for(first_chunk):
                        any_yielded = True
                        yield ev

                async for sc in stream:
                    for ev in _events_for(sc):
                        any_yielded = True
                        yield ev
            except BaseException as exc:
                verdict = self._error_brain.classify(str(exc), getattr(exc, "status_code", None))
                if vision and verdict.verdict in ("bad_request", "model_gone") and \
                        verdict.confidence >= self._error_brain.confidence_threshold:
                    self._model_facts.record_contradicting_failure(
                        route.provider, route.model, "vision", trace_id)
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model,
                                 "status": getattr(exc, "status_code", None),
                                 "provider_message": str(exc), "key_id": key_id,
                                 "verdict": verdict.verdict,
                                 "ms": int((time.monotonic() - start) * 1000)})
                _write_trace(
                    ok=False,
                    ms_to_first_token=(
                        int((first_token_at - start) * 1000) if first_token_at else None),
                )
                raise

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
            # the consumer nothing *usable*. If it also never yielded a
            # single event (no DeltaEvent, no ReasoningDeltaEvent, no
            # ToolCallDeltaEvent — `any_yielded` is False), it's safe to
            # retry against the next provider exactly like the
            # pre-first-chunk failures above, instead of treating "we
            # received at least one chunk" as an irreversible commit.
            # Without this, a model that spends its whole token budget on
            # reasoning (nothing usable back) looks identical to a real
            # success and the caller is left with a silent empty reply.
            #
            # But if something *was* already yielded — reasoning text is the
            # live case — the caller may already be showing it, and
            # switching providers now would be exactly the silent mid-answer
            # model swap streaming is not allowed to do. That case falls
            # through to the `any_yielded` branch below instead of retrying.
            if not full_text and not has_tool_calls:
                if any_yielded:
                    logger.warning(
                        "empty completion after partial output, failing "
                        "without retry",
                        extra={
                            "event": "router.stream.empty_completion_after_partial",
                            "provider": route.provider, "model": route.model,
                            "attempt": attempt + 1, "max_attempts": max_attempts,
                        },
                    )
                    # Declining to retry this request is not the same as
                    # declining to remember: the penalty box is what keeps
                    # this model from being picked first on the *next*
                    # request, and that isn't conditional on retrying within
                    # this one. Same penalize/record calls as the retry path
                    # below, just without the retry.
                    self._engine.penalize(route.provider, route.model)
                    self._events.record(
                        route.provider, route.model, "server_error",
                        detail="empty response after partial output (no content, no tool calls)",
                        penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
                    self._audit.log(
                        tier=tier, provider=route.provider, model=route.model,
                        prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                        latency_ms=latency_ms, status="empty_response",
                    )
                    self._history.record(self._engine.health_snapshot())
                    # No except clause below catches this — it propagates
                    # straight out of the generator, same as any other
                    # post-commit failure. app.py renders it as the
                    # well-formed failure chunk, not a bare error object,
                    # because content/reasoning already went out.
                    _write_trace(
                        ok=False,
                        ms_to_first_token=(
                            int((first_token_at - start) * 1000) if first_token_at else None),
                    )
                    raise RouterError(
                        f"{route.provider}/{route.model}: empty completion "
                        "after partial output (no content, no tool calls)")

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
                verdict = self._error_brain.classify(
                    "empty response (no content, no tool calls)", None)
                if vision and verdict.verdict in ("bad_request", "model_gone") and \
                        verdict.confidence >= self._error_brain.confidence_threshold:
                    self._model_facts.record_contradicting_failure(
                        route.provider, route.model, "vision", trace_id)
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": None,
                                 "provider_message": "empty response (no content, no tool calls)",
                                 "key_id": key_id,
                                 "verdict": verdict.verdict,
                                 "ms": int((time.monotonic() - start) * 1000)})
                yield AttemptFailedEvent(
                    attempt=attempt + 1, max_attempts=max_attempts,
                    provider=route.provider, model=route.model, reason="provider_error",
                    detail="empty response (no content, no tool calls)",
                )
                if attempt == retries:
                    _write_trace(ok=False)
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
            if key_id is not None:
                self._key_states.mark_success(route.provider, key_id, total_tokens, latency_ms)
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
            if vision:
                self._model_facts.record_success(route.provider, route.model, "vision")
            _write_trace(
                ok=True,
                answered_by={"provider": route.provider, "model": route.model, "key_id": key_id},
                tokens={"in": prompt_tokens, "out": completion_tokens},
                ms_to_first_token=(
                    int((first_token_at - start) * 1000) if first_token_at else None),
            )
            yield DoneEvent(result=result)
            return

        _write_trace(ok=False)
        raise RouterBusy(f"All models in tier {tier!r} are unavailable after {retries} retries")

    def _build_pin_engine(self) -> RoutingEngine:
        """A second engine whose buckets are one model each, named
        "provider/model".

        This exists because a request may name one specific model instead of a
        bucket, and `RoutingEngine.select` looks its candidates up by bucket
        name in the config it was given. `engine.py` is reused unchanged by
        spec decree, so instead of teaching it about pins we hand a second
        instance a shadow config. Both share the same rate-limit store,
        penalty box and quota tracker, so a pinned call consumes and respects
        exactly the same allowances as a bucket call, and a model quarantined
        through one is quarantined through the other.
        """
        pinned: dict[str, list] = {}
        for model_configs in self._cfg.tiers.values():
            for mc in model_configs:
                pinned.setdefault(f"{mc.provider}/{mc.model}", [mc])
        shadow = dataclasses.replace(self._cfg, tiers=pinned)
        return RoutingEngine(
            shadow,
            rate_limit_store=self._rate_limit_store,
            penalties=self._penalties,
            quota_tracker=self._quota_tracker,
        )

    def _engine_for(self, tier: str) -> RoutingEngine:
        """The engine that knows about `tier`.

        A name with a "/" in it is one specific model; bucket names never
        contain one. See `flexrouter/wire.py`, which is what produces these
        strings from a request.
        """
        return self._pin_engine if "/" in tier else self._engine

    def _pick_key(self, route: RouteResult) -> tuple[Optional[RouteResult], Optional[str]]:
        """Override a RouteResult's api_key with a real, stateful choice.

        engine.py already picked *which model* — this picks *which key*,
        from real per-key state instead of blind rotation (ADR 0011).
        Returns (None, None) when every configured key for this provider is
        currently unavailable, signalling that back to engine.py through
        PenaltyBox primitives it already reads, never by teaching it
        anything new.
        """
        provider_cfg = self._cfg.providers.get(route.provider)
        candidates = provider_cfg.keys if provider_cfg else []
        if not candidates:
            return route, None  # keyless provider (e.g. local Ollama) — unchanged

        model_id = f"{route.provider}/{route.model}"
        chosen = pick_key(
            candidates, route.provider, model_id, provider_cfg.key_strategy,
            self._key_states, self._cfg.key_concurrency_cap,
            counters=self._round_robin if provider_cfg.key_strategy == "round_robin" else None,
        )
        if chosen is None:
            key_ids = [r.id for r in candidates]
            if self._key_states.all_benched_or_disabled(route.provider, key_ids):
                self._penalties.quarantine_provider(
                    route.provider, "every configured key is benched")
            else:
                wait = self._key_states.min_seconds_until_available(route.provider, key_ids)
                if wait != float("inf"):
                    self._penalties.penalize_short(
                        route.provider, route.model, int(max(wait, 30.0)))
            return None, None

        return dataclasses.replace(route, api_key=chosen.secret), chosen.id

    def reload(self) -> None:
        # Load first, assign after: a settings file that has gone bad
        # must leave the router exactly as it was, not half swapped.
        cfg = load_config(self._config_path)
        self._cfg = cfg
        self._rate_limit_store = RateLimitStore(self._cfg.state_dir)
        self._quota_tracker = QuotaTracker(self._cfg.state_dir)
        self._engine.update_config(self._cfg)
        self._engine._rate_limit_store = self._rate_limit_store
        self._engine._quota_tracker = self._quota_tracker
        self._pin_engine = self._build_pin_engine()
        self._client._rate_limit_store = self._rate_limit_store
        self._audit = AuditLogger(self._cfg.state_dir)
        self._history = HealthHistory(self._cfg.state_dir, self._cfg.health_history_days)
        self._traces = TraceWriter(self._cfg.state_dir)
        self._key_states = KeyStateStore(self._cfg.state_dir)
        self._error_brain = ErrorBrain(self._cfg.state_dir, NullDecider())
        self._model_facts = ModelFactsStore(self._cfg.state_dir)

    def remaining_capacity(self, tier: str) -> dict[str, dict]:
        """For every model in `tier` currently in the running for selection, return
        {"provider/model": {"rpm_remaining": int, "tpm_remaining": int}} — how much of that
        model's per-minute allowance is still free right now. Read-only: does not affect
        routing/selection, does not consume any allowance itself. Raises KeyError for an
        unknown tier, same as generate()/agenerate().
        """
        self._maybe_hot_reload()
        return self._engine_for(tier).remaining_capacity(tier)

    def close(self) -> None:
        self._sampler.stop()
        self._loop.close()

    def __enter__(self) -> "LocalRouter":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def _watched_paths(self) -> list[Path]:
        """Every file a running router's configuration can come out of.

        Watching only the settings file would mean watching the one file
        nothing may write: disabling a dead model in the dashboard lands in
        overrides.json, and adding a key lands in keys.json. Neither would
        ever be noticed until a restart.
        """
        from flexrouter import home

        return [self._config_path, home.overrides_path(), home.keys_path()]

    def _newest_mtime(self) -> tuple:
        """A fingerprint of the watched files: per file, its timestamp and size.

        Not a single newest timestamp. Timestamps are only as fine as the
        filesystem's clock, and on Windows two edits can land in the same
        tick — so "newest" could be identical either side of a real change,
        and the change would be missed until something else moved. Keeping
        each file's timestamp separately, alongside its size, catches both a
        same-tick edit that changes the length and a file going backwards in
        time (which `max()` also hid).

        A file we cannot stat is a file that has told us nothing, not a file
        that changed. Deleting or renaming the settings file under a running
        router used to mean "carry on"; reading its absence as a timestamp of
        zero turned it into "reload now", and the reload then failed in the
        middle of a request. So a vanished file keeps the last fingerprint we
        did see for it.
        """
        fingerprint = []
        for path in self._watched_paths():
            try:
                st = path.stat()
            except OSError:
                mark = self._known_mtimes.get(path, (0, 0))
            else:
                mark = (st.st_mtime_ns, st.st_size)
                self._known_mtimes[path] = mark
            fingerprint.append(mark)
        return tuple(fingerprint)

    def _maybe_hot_reload(self) -> None:
        """Pick up a configuration change, but never fail a request for it.

        Settings that cannot be read are a reason to keep serving the
        settings we already have, not a reason to break the call in
        flight. The timestamp is recorded before the attempt so a file
        that stays broken is not retried on every single request; the
        next edit to it moves the timestamp again and we try afresh.
        """
        fingerprint = self._newest_mtime()
        if fingerprint == self._last_mtime:
            return
        self._last_mtime = fingerprint
        try:
            self.reload()
        except Exception as e:
            self._reload_error = f"{type(e).__name__}: {e}"
            logger.error(
                "Could not read the new settings, so flexrouter is "
                "still running on the ones it loaded earlier: %s",
                self._reload_error)
        else:
            self._reload_error = None

    def _run_startup_catalogue_refresh(self) -> None:
        """Once per service start, check what each provider actually has.

        Replaces a daily background timer (owner's call, 2026-09-20): this
        service is started and stopped by hand, so a calendar timer only
        matters for something left running unattended. refresh_config()
        already tolerates one provider being unreachable — it excludes that
        provider from comparison rather than reporting false vanishings.
        This wrapper's only job is making sure nothing about this call can
        stop the service from starting, the same discipline
        _maybe_hot_reload already applies to a bad config reload.

        Run in a fresh worker thread rather than called directly: under
        `flexrouter serve`, `__init__` runs from inside `app.py`'s
        `lifespan()` (an `async def` that calls the sync `get_router()`
        straight from its own body, on the event loop's own thread —
        unlike a plain `def` route handler, which FastAPI already offloads
        to a worker thread for exactly this reason). `refresh_config()`
        calls `asyncio.run()` internally, and `asyncio.run()` cannot be
        called from a thread that already has a running event loop — it
        raises `RuntimeError` every time, which the except below caught,
        but that meant the refresh never actually ran under uvicorn. A
        thread with no event loop of its own can call `asyncio.run()`
        freely regardless of what the calling thread is doing.
        """
        import concurrent.futures
        import os

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                result = executor.submit(
                    refresh_config, str(self._config_path), self._cfg.state_dir,
                    os.environ.get("AA_API_KEY"),
                ).result()

            from flexrouter.store import read_json
            pending_path = Path(self._cfg.state_dir) / "catalog_pending.json"
            pending = read_json(pending_path, default={})
            for ident in result.added:
                provider, _, model = ident.partition("/")
                entry = next(
                    (m for m in pending.get(provider, {}).get("appeared", [])
                     if m.get("model") == model),
                    None,
                )
                if entry:
                    self._model_facts.record_discovered(
                        provider, model, entry.get("context_window"))
        except Exception as e:
            logger.error(
                "Startup catalogue refresh failed; continuing without it: %s",
                f"{type(e).__name__}: {e}")
            return
