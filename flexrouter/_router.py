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
from flexrouter.bench import BENCH_INTERVAL_SECONDS, check_response_rates
from flexrouter.client import AsyncClient, RateLimitError, ProviderError
from flexrouter.config import FlexConfig, load_config
from flexrouter import errors, redact
from flexrouter.decider import NullDecider, build_decider
from flexrouter.engine import RoutingEngine, RouteResult
from flexrouter.error_brain import ErrorBrain
from flexrouter.events import EventLogger
from flexrouter.exceptions import RouterBusy, RouterError
from flexrouter.failover import Verdict, caller_budget_detail, decide_failover
from flexrouter.health_history import HealthHistory
from flexrouter.hooks import HookRunner, HookContext
from flexrouter.key_state import KeyStateStore
from flexrouter.keys import allows
from flexrouter.model_facts import ModelFactsStore
from flexrouter.quota import QuotaTracker
from flexrouter.rate_limits import RateLimitStore
from flexrouter.reasoning import ReasoningStreamSplitter
from flexrouter.recovery import PenaltyBox
from flexrouter.redact import scrub
from flexrouter.refresh import refresh_config, refresh_known_model_ids
from flexrouter.sampler import PassiveSampler
from flexrouter.scheduler import RoundRobinCounters, key_quota_ok, pick_key
from flexrouter.traces import TraceWriter, new_trace_id

logger = logging.getLogger(__name__)

KEY_BUSY_POLL_SECONDS = 0.05

# §2's "stopping at about 30s total": a bucket call tries every model it can
# reach, not a fixed count, but gives up rather than running forever against
# a bucket that keeps rotating through models that all fail.
FAILOVER_BUDGET_SECONDS = 30.0


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


def _tag_attempts(exc: BaseException, attempts: list[dict]) -> BaseException:
    """Carry the per-attempt record on the exception app.py raises to the
    caller, so error.flexrouter.attempts[] can be built from the real thing
    instead of a copy that could drift from it."""
    exc.attempts = list(attempts)  # type: ignore[attr-defined]
    return exc


class LocalRouter:
    def __init__(self, config_path: Optional[str] = None) -> None:
        from flexrouter import home

        path = Path(config_path) if config_path else home.config_path()
        self._config_path = path
        self._cfg: FlexConfig = load_config(path if config_path else None)
        self._rate_limit_store = RateLimitStore(self._cfg.state_dir)
        errors.set_max_length(self._cfg.error_max_length)
        self._register_known_identifiers()
        self._quota_tracker = QuotaTracker(self._cfg.state_dir)
        self._events = EventLogger(self._cfg.state_dir)
        self._penalties = PenaltyBox(
            self._cfg.penalty_base_seconds, self._cfg.penalty_max_seconds,
            state_dir=self._cfg.state_dir, on_event=self._events.record,
            quarantine_seconds=self._cfg.quarantine_seconds,
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
        self._error_brain = ErrorBrain(
            self._cfg.state_dir, self._build_decider(),
            confidence_threshold=self._cfg.decider.confidence_threshold,
            contested_statuses=self._cfg.decider.contested_statuses,
            rule_prior_confidence=self._cfg.decider.rule_prior_confidence)
        self._model_facts = ModelFactsStore(self._cfg.state_dir)
        self._run_startup_catalogue_refresh()
        self._sampler = PassiveSampler(
            self._engine.health_snapshot, self._history.record,
            self._cfg.sample_interval_seconds)
        self._sampler.start()
        self._bench_sampler = PassiveSampler(
            lambda: check_response_rates(self._cfg.state_dir, self._penalties),
            lambda _: None,
            BENCH_INTERVAL_SECONDS)
        self._bench_sampler.start()
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
        return (f"Every model in bucket {tier!r} is quarantined - this will "
                f"not clear on its own:\n  {joined}")

    def _register_known_identifiers(self) -> None:
        """Let error text keep the provider and model names these settings use."""
        redact.set_enabled(self._cfg.redact_errors)
        names: set[str] = set(self._cfg.providers)
        for models in self._cfg.tiers.values():
            for m in models:
                names.update({m.provider, m.model, f"{m.provider}/{m.model}"})
        redact.set_known_identifiers(names)
        from flexrouter import service_keys
        secrets = {r.secret for p in self._cfg.providers.values() for r in p.keys}
        secrets.update(service_keys.resolve(name) for name in service_keys.SERVICES)
        redact.set_known_secrets(secrets)

    async def _classify(self, text: str, status: Optional[int], route, trace_id: str,
                        exc: Optional[BaseException] = None):
        return await asyncio.to_thread(
            self._error_brain.classify, text, status,
            provider=route.provider, model=route.model, trace_id=trace_id,
            body=getattr(exc, "body", None))

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
        trace_id: Optional[str] = None,
        **kwargs,
    ) -> dict:
        self._maybe_hot_reload()

        # Run hooks
        ctx = HookContext(messages=messages, hooks=self._cfg.hooks, vision=vision)
        ctx = self._hooks.run(ctx)
        vision = ctx.vision
        estimated_tokens = ctx.estimated_tokens

        # A name with a "/" means one specific model was named, not a bucket
        # (see _engine_for). §1: naming a model means you want that model -
        # busy or broken fails immediately, with no fallback to another one.
        is_pinned = "/" in tier

        # A caller (app.py) that already needs this ID for a response header
        # before the call returns passes its own; otherwise one is minted
        # here, same as always.
        trace_id = trace_id or new_trace_id()
        request_started = time.monotonic()
        skipped = [
            {"provider": s["provider"], "model": s["model"],
             "reason": s["reason"], "detail": s["detail"]}
            for s in self._engine_for(tier).explain_unavailable(tier, estimated_tokens, vision)
            if not s["available"]
        ]
        attempts: list[dict] = []
        # Time spent waiting for a slot to free up since the last attempt,
        # attributed to whichever attempt comes next - so a caller reading
        # error.flexrouter.attempts[].waited_ms can tell "the model answered
        # slowly" apart from "the router made us wait first". There is no
        # more per-attempt backoff sleep to account for here (§2: no
        # sleeping) - only the wait for bucket capacity below can add to it.
        pending_wait_ms = 0
        attempt = 0

        trace_written = False

        def _write_trace(ok: bool, answered_by: Optional[dict] = None,
                         tokens: Optional[dict] = None) -> None:
            nonlocal trace_written
            trace_written = True
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

        # Total time spent waiting for bucket capacity to free up (the "route
        # is None" branch below), as opposed to wall-clock time. Real per-call
        # overhead (config loads, classification, disk writes) must not count
        # against the ~30s budget - only deliberate waiting does, so a
        # request that never has to wait can retry every model in the bucket
        # without an unrelated timing fluke cutting it off early.
        total_waited_seconds = 0.0

        try:
            while True:
                if not is_pinned and total_waited_seconds >= FAILOVER_BUDGET_SECONDS:
                    _write_trace(ok=False)
                    raise _tag_attempts(RouterBusy(
                        f"Tried every model in bucket {tier!r} for "
                        f"about {int(FAILOVER_BUDGET_SECONDS)}s without success"), attempts)

                route, key_id = await self._route_with_key(tier, estimated_tokens, vision, session_id)

                if route is None:
                    if last_auth_error is not None:
                        _write_trace(ok=False)
                        raise _tag_attempts(last_auth_error, attempts)
                    blocked = self._quarantine_block_reason(tier)
                    if blocked:
                        _write_trace(ok=False)
                        raise _tag_attempts(RouterBusy(blocked), attempts)
                    if is_pinned or not wait:
                        _write_trace(ok=False)
                        raise _tag_attempts(
                            RouterBusy(f"All models in bucket {tier!r} are unavailable"), attempts)
                    secs = self._engine_for(tier).seconds_until_available(tier)
                    wait_for = max(secs, 1.0)
                    await asyncio.sleep(wait_for)
                    pending_wait_ms += int(wait_for * 1000)
                    total_waited_seconds += wait_for
                    continue

                waited_ms, pending_wait_ms = pending_wait_ms, 0

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
                        request_id=trace_id,
                    )
                    self._history.record(self._engine.health_snapshot())
                    verdict = await self._classify(str(exc), 429, route, trace_id, exc)
                    if vision and verdict.verdict in ("bad_request", "model_gone") and \
                            verdict.confidence >= self._error_brain.confidence_threshold:
                        self._model_facts.record_contradicting_failure(
                            route.provider, route.model, "vision", trace_id)
                    attempts.append({"n": attempt + 1, "provider": route.provider,
                                     "model": route.model, "status": 429,
                                     "provider_message": str(exc), "key_id": key_id,
                                     "verdict": verdict.verdict, "waited_ms": waited_ms,
                                     "ms": int((time.monotonic() - start) * 1000)})
                    attempt += 1
                    # §1: a pinned model that's busy fails now, with no
                    # fallback - retrying it would just hit the same 429.
                    if is_pinned:
                        _write_trace(ok=False)
                        raise _tag_attempts(exc, attempts)
                    continue
                except RouterError as exc:
                    self._handle_auth_failure(route, exc, key_id)
                    last_auth_error = exc
                    self._audit.log(
                        tier=tier, provider=route.provider, model=route.model,
                        prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        status="auth_error",
                        request_id=trace_id,
                    )
                    self._history.record(self._engine.health_snapshot())
                    verdict = await self._classify(str(exc), 401, route, trace_id, exc)
                    if vision and verdict.verdict in ("bad_request", "model_gone") and \
                            verdict.confidence >= self._error_brain.confidence_threshold:
                        self._model_facts.record_contradicting_failure(
                            route.provider, route.model, "vision", trace_id)
                    attempts.append({"n": attempt + 1, "provider": route.provider,
                                     "model": route.model, "status": None,
                                     "provider_message": str(exc), "key_id": key_id,
                                     "verdict": verdict.verdict, "waited_ms": waited_ms,
                                     "ms": int((time.monotonic() - start) * 1000)})
                    attempt += 1
                    if is_pinned:
                        _write_trace(ok=False)
                        raise _tag_attempts(exc, attempts)
                    continue
                except ProviderError as exc:
                    self._audit.log(
                        tier=tier, provider=route.provider, model=route.model,
                        prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        status="error",
                        request_id=trace_id,
                    )
                    self._history.record(self._engine.health_snapshot())
                    verdict = await self._classify(str(exc), exc.status_code, route, trace_id, exc)
                    if vision and verdict.verdict in ("bad_request", "model_gone") and \
                            verdict.confidence >= self._error_brain.confidence_threshold:
                        self._model_facts.record_contradicting_failure(
                            route.provider, route.model, "vision", trace_id)
                    attempts.append({"n": attempt + 1, "provider": route.provider,
                                     "model": route.model, "status": exc.status_code,
                                     "provider_message": str(exc), "key_id": key_id,
                                     "verdict": verdict.verdict, "waited_ms": waited_ms,
                                     "ms": int((time.monotonic() - start) * 1000)})
                    attempt += 1
                    if is_pinned:
                        self._handle_provider_error(route, exc)
                        _write_trace(ok=False)
                        raise _tag_attempts(exc, attempts)
                    action = decide_failover(
                        exc.status_code,
                        message_too_long=verdict.verdict == "message_too_long")
                    if action is Verdict.RETURN:
                        # A genuinely bad request is the caller's fault, not
                        # this model's - no penalty, and no more models tried.
                        _write_trace(ok=False)
                        raise _tag_attempts(exc, attempts)
                    self._handle_provider_error(route, exc)
                    if action is Verdict.NEXT_BIGGER_CONTEXT:
                        estimated_tokens = max(
                            estimated_tokens,
                            self._context_window_of(route.provider, route.model) + 1)
                    continue
                finally:
                    if key_id is not None:
                        self._key_states.end_request(route.provider, key_id)

                latency_ms = int((time.monotonic() - start) * 1000)
                usage = result.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                total_tokens = usage.get("total_tokens", 0)

                # The streaming path (agenerate_stream, below) has always
                # detected an empty completion and retried the next provider.
                # This non-streaming path never did: chat() only raises if
                # choices[0].message.content is *missing*, not if it's an
                # empty string, so a 200 OK with nothing in it sailed through
                # as a recorded success and was handed straight to the
                # caller. Since every OpenAI-compatible client defaults to
                # stream:false, this was the path most callers actually hit -
                # the live symptom (empty replies with nothing in the Error
                # brain to explain them) was this gap, not the streaming one.
                message = (result.get("choices") or [{}])[0].get("message") or {}
                content = message.get("content") or ""
                tool_calls = message.get("tool_calls") or []
                finish_reason = (result.get("choices") or [{}])[0].get("finish_reason")
                if not content.strip() and not tool_calls:
                    budget_detail = caller_budget_detail(finish_reason, kwargs.get("max_tokens"))
                    if budget_detail is not None:
                        empty_detail = budget_detail
                    else:
                        empty_detail = (
                            f"empty response (no content, no tool calls); "
                            f"finish_reason={finish_reason!r}, usage={usage!r}"
                        )
                        logger.warning(
                            "empty completion, retrying next provider",
                            extra={
                                "event": "router.chat.empty_completion",
                                "provider": route.provider, "model": route.model,
                                "attempt": attempt + 1,
                                "finish_reason": finish_reason,
                            },
                        )
                        self._engine.penalize(route.provider, route.model)
                        self._events.record(
                            route.provider, route.model, "server_error",
                            detail=empty_detail,
                            penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
                    self._audit.log(
                        tier=tier, provider=route.provider, model=route.model,
                        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, cost_usd=0.0,
                        latency_ms=latency_ms, status="empty_response",
                        request_id=trace_id,
                    )
                    self._history.record(self._engine.health_snapshot())
                    verdict = await self._classify(empty_detail, None, route, trace_id)
                    if vision and verdict.verdict in ("bad_request", "model_gone") and \
                            verdict.confidence >= self._error_brain.confidence_threshold:
                        self._model_facts.record_contradicting_failure(
                            route.provider, route.model, "vision", trace_id)
                    attempts.append({"n": attempt + 1, "provider": route.provider,
                                     "model": route.model, "status": None,
                                     "provider_message": empty_detail, "key_id": key_id,
                                     "verdict": verdict.verdict, "waited_ms": waited_ms,
                                     "ms": int((time.monotonic() - start) * 1000)})
                    attempt += 1
                    if is_pinned:
                        _write_trace(ok=False)
                        raise _tag_attempts(
                            RouterBusy(f"{route.provider}/{route.model}: {empty_detail}"), attempts)
                    continue

                self._engine.record_request(route.provider, route.model, total_tokens)
                self._quota_tracker.record(route.provider, route.model, total_tokens)
                if key_id is not None:
                    self._key_states.mark_success(route.provider, key_id, total_tokens, latency_ms)
                    self._quota_tracker.record_key(route.provider, key_id, total_tokens)
                self._audit.log(
                    tier=tier,
                    provider=route.provider,
                    model=route.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_usd=self._cost_of(route.provider, route.model,
                                           prompt_tokens, completion_tokens),
                    latency_ms=latency_ms,
                    status="ok",
                    request_id=trace_id,
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
        finally:
            # A caller that gives up (a deadline, a closed connection) cancels
            # this mid-wait; the request must still show up as failed.
            if not trace_written:
                _write_trace(ok=False)

    async def agenerate_stream(
        self,
        messages: list[dict],
        tier: str,
        vision: bool = False,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        **kwargs,
    ) -> AsyncIterator[StreamEvent]:
        self._maybe_hot_reload()

        # Run hooks
        ctx = HookContext(messages=messages, hooks=self._cfg.hooks, vision=vision)
        ctx = self._hooks.run(ctx)
        vision = ctx.vision
        estimated_tokens = ctx.estimated_tokens

        is_pinned = "/" in tier
        # §2: no fixed retry count drives failover any more - a bucket call
        # tries every model it can reach until it succeeds or the ~30s
        # budget runs out. max_attempts is kept only as an informational
        # figure on the events a stream consumer sees (e.g. "attempt 2/4"),
        # not as a loop bound.
        max_attempts = self._cfg.retry.retries + 1

        trace_id = trace_id or new_trace_id()
        request_started = time.monotonic()
        skipped = [
            {"provider": s["provider"], "model": s["model"],
             "reason": s["reason"], "detail": s["detail"]}
            for s in self._engine_for(tier).explain_unavailable(tier, estimated_tokens, vision)
            if not s["available"]
        ]
        attempts: list[dict] = []
        attempt = 0
        # See agenerate()'s identical field: only deliberate waiting for
        # bucket capacity counts against the ~30s failover budget.
        total_waited_seconds = 0.0

        trace_written = False

        def _write_trace(ok: bool, answered_by: Optional[dict] = None,
                         tokens: Optional[dict] = None,
                         ms_to_first_token: Optional[int] = None) -> None:
            nonlocal trace_written
            trace_written = True
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

        try:
            while True:
                if not is_pinned and total_waited_seconds >= FAILOVER_BUDGET_SECONDS:
                    _write_trace(ok=False)
                    raise _tag_attempts(RouterBusy(
                        f"Tried every model in bucket {tier!r} for "
                        f"about {int(FAILOVER_BUDGET_SECONDS)}s without success"), attempts)

                route, key_id = await self._route_with_key(tier, estimated_tokens, vision, session_id)

                if route is None:
                    # Unlike the sync path this loop has no wait=False escape, so
                    # without the quarantine check it sleeps forever on a bucket
                    # that can never come back — the reported hang, via the
                    # streaming route a chat client actually uses.
                    blocked = self._quarantine_block_reason(tier)
                    if blocked:
                        _write_trace(ok=False)
                        raise _tag_attempts(RouterBusy(blocked), attempts)
                    if is_pinned:
                        _write_trace(ok=False)
                        raise _tag_attempts(
                            RouterBusy(f"All models in bucket {tier!r} are unavailable"), attempts)
                    secs = self._engine_for(tier).seconds_until_available(tier)
                    wait_for = max(secs, 1.0)
                    await asyncio.sleep(wait_for)
                    total_waited_seconds += wait_for
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
                        request_id=trace_id,
                    )
                    self._history.record(self._engine.health_snapshot())
                    verdict = await self._classify("empty stream response", None, route, trace_id)
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
                    attempt += 1
                    if is_pinned:
                        _write_trace(ok=False)
                        raise _tag_attempts(
                            RouterBusy(f"{route.provider}/{route.model}: empty stream response"),
                            attempts)
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
                        request_id=trace_id,
                    )
                    self._history.record(self._engine.health_snapshot())
                    verdict = await self._classify(str(exc), 429, route, trace_id, exc)
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
                    attempt += 1
                    if is_pinned:
                        _write_trace(ok=False)
                        raise _tag_attempts(exc, attempts)
                    continue
                except RouterError as exc:
                    self._handle_auth_failure(route, exc, key_id)
                    self._audit.log(
                        tier=tier, provider=route.provider, model=route.model,
                        prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        status="auth_error",
                        request_id=trace_id,
                    )
                    self._history.record(self._engine.health_snapshot())
                    verdict = await self._classify(str(exc), 401, route, trace_id, exc)
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
                    attempt += 1
                    if is_pinned:
                        _write_trace(ok=False)
                        raise _tag_attempts(exc, attempts)
                    continue
                except ProviderError as exc:
                    self._audit.log(
                        tier=tier, provider=route.provider, model=route.model,
                        prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        status="error",
                        request_id=trace_id,
                    )
                    self._history.record(self._engine.health_snapshot())
                    verdict = await self._classify(str(exc), exc.status_code, route, trace_id, exc)
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
                    attempt += 1
                    if is_pinned:
                        self._handle_provider_error(route, exc)
                        _write_trace(ok=False)
                        raise _tag_attempts(exc, attempts)
                    action = decide_failover(
                        exc.status_code,
                        message_too_long=verdict.verdict == "message_too_long")
                    if action is Verdict.RETURN:
                        _write_trace(ok=False)
                        raise _tag_attempts(exc, attempts)
                    self._handle_provider_error(route, exc)
                    if action is Verdict.NEXT_BIGGER_CONTEXT:
                        estimated_tokens = max(
                            estimated_tokens,
                            self._context_window_of(route.provider, route.model) + 1)
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
                finish_reason: Optional[str] = None
                # Some providers (Gemma-style) put reasoning inline in
                # `content` as `<think>`/`<thought>` tags instead of a
                # separate field - a tag boundary has no relation to a chunk
                # boundary, so this buffers across chunks for this attempt.
                inline_reasoning = ReasoningStreamSplitter()

                def _events_for(sc) -> list:
                    nonlocal has_tool_calls, first_token_at, finish_reason
                    events: list = []
                    if sc.finish_reason:
                        finish_reason = sc.finish_reason
                    if first_token_at is None and (sc.content or sc.reasoning or sc.tool_call_delta):
                        first_token_at = time.monotonic()
                    if sc.content:
                        content_part, reasoning_part = inline_reasoning.feed(sc.content)
                        if content_part:
                            accumulated.append(content_part)
                            events.append(DeltaEvent(text=content_part))
                        if reasoning_part:
                            events.append(ReasoningDeltaEvent(text=reasoning_part))
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

                    # A response cut off by max_tokens mid-thought never
                    # gets its closing tag - whatever's still buffered is
                    # the tail of the reasoning or content, not garbage.
                    flush_content, flush_reasoning = inline_reasoning.flush()
                    if flush_content:
                        accumulated.append(flush_content)
                        any_yielded = True
                        yield DeltaEvent(text=flush_content)
                    if flush_reasoning:
                        any_yielded = True
                        yield ReasoningDeltaEvent(text=flush_reasoning)
                except BaseException as exc:
                    verdict = await self._classify(str(exc), getattr(exc, "status_code", None), route, trace_id, exc)
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
                    raise _tag_attempts(exc, attempts)

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
                    # The real diagnosis, not just the fact of emptiness: a
                    # `length` finish_reason with completion_tokens pinned at
                    # (or near) the request's max_tokens means the model spent
                    # its whole budget on reasoning and never reached an
                    # answer — a caller-side budget problem, not a provider
                    # fault. `content_filter` means the provider blocked it.
                    # Both used to collapse into the same generic string,
                    # which is why the Error brain could never say why.
                    empty_detail = (
                        f"empty response (no content, no tool calls); "
                        f"finish_reason={finish_reason!r}, usage={final_usage!r}"
                    )
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
                            detail="empty response after partial output - " + empty_detail,
                            penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
                        self._audit.log(
                            tier=tier, provider=route.provider, model=route.model,
                            prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                            latency_ms=latency_ms, status="empty_response",
                            request_id=trace_id,
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
                        raise _tag_attempts(RouterError(
                            f"{route.provider}/{route.model}: empty completion after partial "
                            f"output - {empty_detail}"), attempts)

                    budget_detail = caller_budget_detail(finish_reason, kwargs.get("max_tokens"))
                    if budget_detail is not None:
                        empty_detail = budget_detail
                    else:
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
                            detail=empty_detail,
                            penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
                    self._audit.log(
                        tier=tier, provider=route.provider, model=route.model,
                        prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                        latency_ms=latency_ms, status="empty_response",
                        request_id=trace_id,
                    )
                    self._history.record(self._engine.health_snapshot())
                    verdict = await self._classify(empty_detail, None, route, trace_id)
                    if vision and verdict.verdict in ("bad_request", "model_gone") and \
                            verdict.confidence >= self._error_brain.confidence_threshold:
                        self._model_facts.record_contradicting_failure(
                            route.provider, route.model, "vision", trace_id)
                    attempts.append({"n": attempt + 1, "provider": route.provider,
                                     "model": route.model, "status": None,
                                     "provider_message": empty_detail,
                                     "key_id": key_id,
                                     "verdict": verdict.verdict,
                                     "ms": int((time.monotonic() - start) * 1000)})
                    yield AttemptFailedEvent(
                        attempt=attempt + 1, max_attempts=max_attempts,
                        provider=route.provider, model=route.model, reason="provider_error",
                        detail=empty_detail,
                    )
                    attempt += 1
                    if is_pinned:
                        _write_trace(ok=False)
                        raise _tag_attempts(
                            RouterBusy(f"{route.provider}/{route.model}: {empty_detail}"), attempts)
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
                self._quota_tracker.record(route.provider, route.model, total_tokens)
                if key_id is not None:
                    self._key_states.mark_success(route.provider, key_id, total_tokens, latency_ms)
                    self._quota_tracker.record_key(route.provider, key_id, total_tokens)
                self._audit.log(
                    tier=tier,
                    provider=route.provider,
                    model=route.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_usd=self._cost_of(route.provider, route.model,
                                           prompt_tokens, completion_tokens),
                    latency_ms=latency_ms,
                    status="ok",
                    request_id=trace_id,
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
        finally:
            # A caller that gives up (a deadline, a closed connection) cancels
            # this mid-wait; the request must still show up as failed.
            if not trace_written:
                _write_trace(ok=False)

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

    async def _route_with_key(
        self, tier: str, estimated_tokens: int, vision: bool, session_id: Optional[str],
    ) -> tuple[Optional[RouteResult], Optional[str]]:
        """engine.select() plus a key, waiting out keys that are merely busy.

        A key at its concurrency cap frees up as soon as an in-flight request
        gets its first chunk back, so it is queued for here rather than
        treated as a failure: no penalty, and no retry spent on it.
        """
        while True:
            route = self._engine_for(tier).select(tier, estimated_tokens, vision, session_id)
            if route is None:
                return None, None
            route_with_key, key_id, busy = self._pick_key(route)
            if not busy:
                return route_with_key, key_id
            await asyncio.sleep(KEY_BUSY_POLL_SECONDS)

    def _pick_key(self, route: RouteResult) -> tuple[Optional[RouteResult], Optional[str], bool]:
        """Override a RouteResult's api_key with a real, stateful choice.

        engine.py already picked *which model* — this picks *which key*,
        from real per-key state instead of blind rotation (ADR 0011).
        Returns (None, None, True) when a usable key is only at its
        concurrency cap, and (None, None, False) when every configured key
        for this provider is unavailable, signalling that back to engine.py
        through PenaltyBox primitives it already reads, never by teaching it
        anything new.
        """
        provider_cfg = self._cfg.providers.get(route.provider)
        candidates = provider_cfg.keys if provider_cfg else []
        if not candidates:
            return route, None, False  # keyless provider (e.g. local Ollama) — unchanged

        model_id = f"{route.provider}/{route.model}"
        chosen = pick_key(
            candidates, route.provider, model_id, provider_cfg.key_strategy,
            self._key_states, self._cfg.key_concurrency_cap,
            counters=self._round_robin if provider_cfg.key_strategy == "round_robin" else None,
            quota_tracker=self._quota_tracker,
        )
        if chosen is None:
            # A key that's allowed and not benched/cooling but still didn't
            # get picked is blocked by one of two very different things: the
            # concurrency cap (frees up in seconds, as soon as an in-flight
            # request on it returns — worth a short poll) or its own quota
            # (frees up whenever that window rolls over, possibly a whole
            # day — polling every KEY_BUSY_POLL_SECONDS for that would just
            # spin). Sort candidates into those two buckets instead of
            # treating every non-benched key as "busy" the same way.
            live_allowed = [r for r in candidates
                            if allows(r, model_id) and self._key_states.is_available(route.provider, r.id)]
            quota_blocked = [r for r in live_allowed
                             if not key_quota_ok(self._quota_tracker, route.provider, r)]
            if len(quota_blocked) < len(live_allowed):
                return None, None, True  # at least one is only cap-limited
            key_ids = [r.id for r in candidates]
            if self._key_states.all_benched_or_disabled(route.provider, key_ids):
                self._penalties.quarantine_provider(
                    route.provider, "every configured key is benched")
            else:
                wait = self._key_states.min_seconds_until_available(route.provider, key_ids)
                if quota_blocked:
                    quota_wait = min(
                        self._quota_tracker.key_seconds_until_available(route.provider, r.id, r.quotas)
                        for r in quota_blocked)
                    wait = quota_wait if wait == float("inf") else min(wait, quota_wait)
                if wait != float("inf"):
                    self._penalties.penalize_short(
                        route.provider, route.model, int(max(wait, 30.0)))
            return None, None, False

        return dataclasses.replace(route, api_key=chosen.secret), chosen.id, False

    def reload(self) -> None:
        # Load first, assign after: a settings file that has gone bad
        # must leave the router exactly as it was, not half swapped.
        cfg = load_config(self._config_path)
        self._cfg = cfg
        self._register_known_identifiers()
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
        self._error_brain = ErrorBrain(
            self._cfg.state_dir, self._build_decider(),
            confidence_threshold=self._cfg.decider.confidence_threshold,
            contested_statuses=self._cfg.decider.contested_statuses,
            rule_prior_confidence=self._cfg.decider.rule_prior_confidence)
        self._model_facts = ModelFactsStore(self._cfg.state_dir)

    def _build_decider(self):
        """Spec 4's classifier, or nothing. The key comes from service_keys
        (masked keys.json, env fallback) rather than from settings -- same
        reason auth_token is kept out of ALLOWED_FIELDS."""
        from flexrouter import service_keys
        return build_decider(self._cfg.decider, service_keys.resolve("decider"))

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
        self._bench_sampler.stop()
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

    def _context_window_of(self, provider: str, model: str) -> int:
        for models in self._cfg.tiers.values():
            for m in models:
                if m.provider == provider and m.model == model:
                    return m.context_window
        return 0

    def _price_of(self, provider: str, model: str):
        """The model's per-million-token prices, or `(None, None)`.

        A model can sit in several buckets; the prices are a property of the
        model at the provider, so the first match answers for all of them.
        """
        for models in self._cfg.tiers.values():
            for m in models:
                if m.provider == provider and m.model == model:
                    return m.price_in, m.price_out
        return None, None

    def _cost_of(self, provider: str, model: str,
                 prompt_tokens: int, completion_tokens: int) -> float:
        """What this attempt cost, in USD, as far as anyone has said.

        Zero when the model is unpriced - but the dashboard distinguishes
        "no priced traffic at all" from "priced, and it came to nothing",
        so a zero here never gets reported as "free".
        """
        price_in, price_out = self._price_of(provider, model)
        if price_in is None and price_out is None:
            return 0.0
        return (
            (prompt_tokens / 1_000_000) * (price_in or 0.0)
            + (completion_tokens / 1_000_000) * (price_out or 0.0)
        )

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

        grill-decisions.md §12 splits discovery in two. *Reading* each
        provider's real model list always runs here, regardless of
        `auto_add_models` - it only caches IDs for `catalogue.is_real`/
        `did_you_mean` and flags configured IDs the provider no longer
        lists (`state/unknown_configured_models.json`, for Session 8). It
        writes nothing to a bucket. *Auto-add* - the full catalogue refresh
        that stages new models into `catalog_pending.json` for the owner to
        accept - stays behind `auto_add_models` (default off, renamed from
        `experimental_model_discovery` 2026-09-23): the owner adds models by
        hand, and staging every configured provider's entire catalogue on
        every single startup is not something that should happen without
        being asked for.
        """
        import concurrent.futures

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(
                    refresh_known_model_ids,
                    str(self._config_path), self._cfg.state_dir,
                ).result()
        except Exception as e:
            logger.error(
                "Startup model-ID read failed; continuing without it: %s",
                f"{type(e).__name__}: {e}")

        if not self._cfg.auto_add_models:
            return

        from flexrouter import service_keys

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                result = executor.submit(
                    refresh_config, str(self._config_path), self._cfg.state_dir,
                    service_keys.resolve("aa"),
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
