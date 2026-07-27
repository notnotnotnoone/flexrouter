from __future__ import annotations
import random
import time
import warnings
from dataclasses import dataclass
from typing import Optional

from flexrouter.config import FlexConfig, ModelConfig
from flexrouter.exceptions import ContextWindowWarning
from flexrouter.recovery import PenaltyBox
from flexrouter.window import SlidingWindow
from flexrouter.budget import DailyBudget


@dataclass
class RouteResult:
    provider: str
    model: str
    api_key: str
    base_url: str
    tier: str
    header_parser: str = "openai_compatible"


class RoutingEngine:
    def __init__(self, cfg: FlexConfig, rate_limit_store=None, penalties: Optional[PenaltyBox] = None,
                 quota_tracker=None) -> None:
        self._cfg = cfg
        self._rate_limit_store = rate_limit_store
        self._quota_tracker = quota_tracker
        self._penalties = penalties if penalties is not None else PenaltyBox(
            cfg.penalty_base_seconds, cfg.penalty_max_seconds)
        self._budget = DailyBudget(cfg.provider_budget)
        self._windows: dict[str, SlidingWindow] = {}
        self._key_counters: dict[str, int] = {}
        # session_id -> (provider, model, last_used: float)
        self._sessions: dict[str, tuple[str, str, float]] = {}
        self._session_ttl = cfg.session_ttl_minutes * 60

        for tier_models in cfg.tiers.values():
            for m in tier_models:
                k = f"{m.provider}/{m.model}"
                if k not in self._windows:
                    self._windows[k] = SlidingWindow(cfg.window_seconds)

    def update_config(self, cfg: FlexConfig) -> None:
        """Hot-reload: update config, add new windows, preserve existing state."""
        self._cfg = cfg
        self._budget = DailyBudget(cfg.provider_budget)
        self._session_ttl = cfg.session_ttl_minutes * 60
        self._penalties.base_seconds = cfg.penalty_base_seconds
        self._penalties.max_seconds = cfg.penalty_max_seconds
        for tier_models in cfg.tiers.values():
            for m in tier_models:
                k = f"{m.provider}/{m.model}"
                if k not in self._windows:
                    self._windows[k] = SlidingWindow(cfg.window_seconds)

    def select(
        self,
        tier: str,
        estimated_tokens: int,
        vision: bool,
        session_id: Optional[str] = None,
    ) -> Optional[RouteResult]:
        models = self._cfg.tiers[tier]  # raises KeyError for unknown tier

        # Session stickiness
        if session_id:
            result = self._try_session(session_id, tier, estimated_tokens, vision)
            if result:
                return result

        candidates = self._score_candidates(models, estimated_tokens, vision)
        if not candidates:
            return None

        result = self._pick(candidates, tier)

        if session_id:
            self._sessions[session_id] = (result.provider, result.model, time.monotonic())

        return result

    def record_request(self, provider: str, model: str, tokens: int) -> None:
        k = f"{provider}/{model}"
        if k in self._windows:
            self._windows[k].record(tokens)

    def record_cost(self, provider: str, cost_usd: float) -> None:
        self._budget.record(provider, cost_usd)

    def penalize(self, provider: str, model: str) -> None:
        self._penalties.penalize(provider, model)

    def penalize_short(self, provider: str, model: str, seconds: int = 30) -> None:
        self._penalties.penalize_short(provider, model, seconds)

    def seconds_until_available(self, tier: str) -> float:
        models = self._cfg.tiers.get(tier, [])
        min_wait = float("inf")
        for m in models:
            if self._penalties.is_penalized(m.provider, m.model):
                until = self._penalties.penalty_until(m.provider, m.model)
                if until:
                    min_wait = min(min_wait, until - time.monotonic())
            elif self._rate_limit_store is not None and self._rate_limit_store.is_exhausted(m.provider, m.model):
                avail = self._rate_limit_store.available_at(m.provider, m.model)
                if avail is not None:
                    min_wait = min(min_wait, avail - time.time())
            elif self._quota_tracker is not None and m.quotas and not self._quota_tracker.is_available(m.provider, m.model, m.quotas):
                min_wait = min(min_wait, self._quota_tracker.seconds_until_available(m.provider, m.model, m.quotas))
            else:
                w = self._windows.get(f"{m.provider}/{m.model}")
                if w:
                    secs = w.seconds_until_available(self._model_rpm(m), self._model_tpm(m))
                    min_wait = min(min_wait, secs)
        return max(0.0, min_wait) if min_wait != float("inf") else 0.0

    def health_snapshot(self) -> dict:
        models: dict[str, dict] = {}
        providers: dict[str, dict] = {}
        seen: set[str] = set()
        for tier_models in self._cfg.tiers.values():
            for m in tier_models:
                key = f"{m.provider}/{m.model}"
                if key in seen:
                    continue
                seen.add(key)
                penalized = self._penalties.is_penalized(m.provider, m.model)
                until = self._penalties.penalty_until(m.provider, m.model)
                w = self._windows.get(key)
                rpm = w.current_rpm() if w else 0
                tpm = w.current_tpm() if w else 0
                models[key] = {
                    "status": "penalized" if penalized else "up",
                    "rpm": rpm,
                    "tpm": tpm,
                    "penalized": penalized,
                    "penalty_until": until,
                    "latency_ewma_ms": None,
                }
                pv = providers.setdefault(m.provider, {"models_up": 0, "models_total": 0})
                pv["models_total"] += 1
                if not penalized:
                    pv["models_up"] += 1
        return {"models": models, "providers": providers}

    def remaining_capacity(self, tier: str) -> dict[str, dict]:
        """For every model in `tier` currently in the running for selection — the same
        top-20%-by-score-among-available pool _pick() would choose from — return
        {"provider/model": {"rpm_remaining": int, "tpm_remaining": int}}, computed from each
        model's configured rpm/tpm limit minus its current SlidingWindow usage.

        Read-only: does not record usage, penalize, or otherwise affect routing/selection.
        Raises KeyError for an unknown tier, same as select(). estimated_tokens=0 is passed to
        _score_candidates so no model is excluded on context-window grounds — this method
        answers "how much room is there," not "would a specific prompt fit."
        """
        models = self._cfg.tiers[tier]  # raises KeyError for unknown tier
        scored = self._score_candidates(models, estimated_tokens=0, vision=False)
        if not scored:
            return {}
        best_score = max(score for score, _ in scored)
        threshold = best_score * 0.8
        pool = [m for score, m in scored if score >= threshold]

        result: dict[str, dict] = {}
        for m in pool:
            key = f"{m.provider}/{m.model}"
            w = self._windows.get(key)
            used_rpm = w.current_rpm() if w else 0
            used_tpm = w.current_tpm() if w else 0
            result[key] = {
                "rpm_remaining": max(0, self._model_rpm(m) - used_rpm),
                "tpm_remaining": max(0, self._model_tpm(m) - used_tpm),
            }
        return result

    # --- internals ---

    def _try_session(
        self, session_id: str, tier: str, estimated_tokens: int, vision: bool
    ) -> Optional[RouteResult]:
        entry = self._sessions.get(session_id)
        if not entry:
            return None
        provider, model, last_used = entry
        # Expire stale sessions
        if time.monotonic() - last_used > self._session_ttl:
            del self._sessions[session_id]
            return None
        # Check pinned model is still available
        models = self._cfg.tiers.get(tier, [])
        pinned = next((m for m in models if m.provider == provider and m.model == model), None)
        if not pinned:
            return None
        if not self._model_available(pinned, estimated_tokens, vision, emit_warning=False):
            return None  # fall through to normal selection
        self._sessions[session_id] = (provider, model, time.monotonic())
        return self._make_result(pinned, tier)

    def _score_candidates(
        self, models: list[ModelConfig], estimated_tokens: int, vision: bool
    ) -> list[tuple[int, ModelConfig]]:
        ctx_skipped = False
        scored = []
        for m in models:
            if vision and not m.vision:
                continue
            if self._penalties.is_penalized(m.provider, m.model):
                continue
            if self._rate_limit_store is not None and self._rate_limit_store.is_exhausted(m.provider, m.model):
                continue
            if self._quota_tracker is not None and m.quotas and not self._quota_tracker.is_available(m.provider, m.model, m.quotas):
                continue
            if not self._budget.is_available(m.provider):
                continue
            if estimated_tokens > 0 and estimated_tokens >= m.context_window:
                ctx_skipped = True
                continue
            w = self._windows.get(f"{m.provider}/{m.model}")
            if w and not w.available(self._model_rpm(m), self._model_tpm(m)):
                continue
            scored.append((m.score, m))

        if ctx_skipped:
            warnings.warn(
                "Some models skipped: estimated token count exceeds their context window.",
                ContextWindowWarning,
                stacklevel=4,
            )

        return scored

    def _pick(self, scored: list[tuple[int, ModelConfig]], tier: str) -> RouteResult:
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score = scored[0][0]
        assert best_score > 0, f"Model scores must be positive (1-100), got {best_score}"
        threshold = best_score * 0.8
        top = [m for score, m in scored if score >= threshold]
        chosen = random.choice(top)
        return self._make_result(chosen, tier)

    def _make_result(self, m: ModelConfig, tier: str) -> RouteResult:
        provider_cfg = self._cfg.providers[m.provider]
        if provider_cfg.api_keys:
            counter = self._key_counters.get(m.provider, 0)
            api_key = provider_cfg.api_keys[counter % len(provider_cfg.api_keys)]
            self._key_counters[m.provider] = counter + 1
        else:
            api_key = ""  # local providers (Ollama)
        return RouteResult(
            provider=m.provider,
            model=m.model,
            api_key=api_key,
            base_url=provider_cfg.base_url,
            tier=tier,
            header_parser=provider_cfg.header_parser,
        )

    def _model_rpm(self, m: ModelConfig) -> int:
        if self._rate_limit_store is not None:
            return self._rate_limit_store.get_rpm(m.provider, m.model, m.rpm)
        return m.rpm

    def _model_tpm(self, m: ModelConfig) -> int:
        if self._rate_limit_store is not None:
            return self._rate_limit_store.get_tpm(m.provider, m.model, m.tpm)
        return m.tpm

    def _model_available(
        self, m: ModelConfig, estimated_tokens: int, vision: bool, emit_warning: bool
    ) -> bool:
        if vision and not m.vision:
            return False
        if self._penalties.is_penalized(m.provider, m.model):
            return False
        if self._rate_limit_store is not None and self._rate_limit_store.is_exhausted(m.provider, m.model):
            return False
        if self._quota_tracker is not None and m.quotas and not self._quota_tracker.is_available(m.provider, m.model, m.quotas):
            return False
        if not self._budget.is_available(m.provider):
            return False
        if estimated_tokens > 0 and estimated_tokens >= m.context_window:
            return False
        w = self._windows.get(f"{m.provider}/{m.model}")
        if w and not w.available(self._model_rpm(m), self._model_tpm(m)):
            return False
        return True
