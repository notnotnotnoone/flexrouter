"""Read-only views of a running router, for the dashboard to render.

Every fact here is computed from objects `LocalRouter` already builds. This
module never writes, never calls a provider, and never teaches `engine.py`
anything - the established pattern across ADRs 0009-0014.

It returns plain data. Nothing here knows what HTML is.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from flexrouter.keys import mask
from flexrouter.model_facts import CapabilityFact
from flexrouter.overrides import ALLOWED_FIELDS, load_overrides
from flexrouter import status as st
from flexrouter.store import read_json


@dataclass
class BrokenItem:
    pile: str  # "you" | "service"
    # model_needs_you | provider_needs_you | key_needs_you | model_busy |
    # model_struggling | key_busy | unclear_error
    kind: str
    provider: str
    detail: str
    reason: str
    since: Optional[str] = None
    until: Optional[float] = None
    status: str = ""       # ready | busy | struggling | needs_you | off
    action: Optional[str] = None
    model: str = ""
    provider_text: str = ""  # the provider's own words, for the click-through


@dataclass
class ProviderSummary:
    name: str
    base_url: str
    key_count: int
    keys_ready: int
    keys_busy: int
    keys_need_you: int
    keys_off: int
    models_total: int
    status: str  # the provider-wide status: ready | needs_you
    status_reason: str
    state: str  # "ok" | "warn" | "bad"


@dataclass
class KeyDetail:
    id: str
    label: str
    masked: str
    source: str
    enabled: bool
    weight: int
    allow_models: list
    quotas: dict  # rps/rph/rpd/tps/tph/tpd caps on this key; {} = none set
    status: str
    reason: str
    until: Optional[float]
    consecutive_failures: int
    failures_24h: int
    requests_today: int
    tokens_today: int
    last_used_at: Optional[float]
    active_requests: int
    ema_latency_ms: Optional[float]


@dataclass
class ProviderDetail:
    name: str
    base_url: str
    header_parser: str
    key_strategy: str
    state: str
    status: str  # the provider-wide status: ready | needs_you
    status_reason: str
    configured_models: list
    models_alive: list  # Ready right now
    models_gone: list   # Needs you
    keys: list  # list[KeyDetail]


def _models_of(router, provider: str) -> list:
    """Every configured model belonging to one provider, across all buckets."""
    seen = []
    for model_configs in router._cfg.tiers.values():
        for mc in model_configs:
            if mc.provider == provider and mc.model not in seen:
                seen.append(mc.model)
    return seen


def key_status(state, record, now: float) -> str:
    """A key's one status (§3's words), read without writing.

    Not is_available(): that method persists a busy key whose time is up
    (it flips the stored status to "ready"). Rendering a page must never
    write to disk, so the same expiry check is done here without saving.
    """
    if not record.enabled:
        return st.OFF
    if state.status == st.BUSY and state.until is not None and now >= state.until:
        return st.READY
    return state.status


def provider_summaries(router, now: Optional[float] = None) -> list[ProviderSummary]:
    states = router._key_states
    resolved_now = now if now is not None else time.time()
    out = []
    for name, pcfg in router._cfg.providers.items():
        records = list(pcfg.keys or [])
        counts = {st.READY: 0, st.BUSY: 0, st.NEEDS_YOU: 0, st.OFF: 0}
        for record in records:
            value = key_status(states.get(name, record.id, now), record, resolved_now)
            counts[value if value in counts else st.NEEDS_YOU] += 1
        key_count = len(records)
        ready, busy = counts[st.READY], counts[st.BUSY]

        wide = router._status.provider_status(name, resolved_now)
        if wide.value == st.NEEDS_YOU or key_count == 0:
            state = "bad"
        elif ready == 0 and busy == 0:
            # Nothing usable and nothing due to recover on its own.
            state = "bad"
        elif ready == 0 or busy or counts[st.NEEDS_YOU] or counts[st.OFF]:
            state = "warn"
        else:
            state = "ok"

        out.append(ProviderSummary(
            name=name,
            base_url=pcfg.base_url,
            key_count=key_count,
            keys_ready=ready,
            keys_busy=busy,
            keys_need_you=counts[st.NEEDS_YOU],
            keys_off=counts[st.OFF],
            models_total=len(_models_of(router, name)),
            status=wide.value,
            status_reason=wide.reason,
            state=state,
        ))
    return out


def provider_detail(router, provider: str, now: Optional[float] = None) -> Optional[ProviderDetail]:
    """Everything the Providers & keys detail page shows for one provider.

    Returns None for an unconfigured provider name so the page can 404
    instead of rendering a hole.
    """
    pcfg = router._cfg.providers.get(provider)
    if pcfg is None:
        return None

    states = router._key_states
    resolved_now = now if now is not None else time.time()

    summary = next((s for s in provider_summaries(router, now) if s.name == provider), None)

    configured = _models_of(router, provider)
    wide = router._status.provider_status(provider, resolved_now)
    alive, gone = [], []
    for model in configured:
        value = router._status.get(provider, model, resolved_now).value
        if value == st.READY:
            alive.append(model)
        elif value == st.NEEDS_YOU:
            gone.append(model)

    keys = []
    for record in (pcfg.keys or []):
        state = states.get(provider, record.id, resolved_now)
        keys.append(KeyDetail(
            id=record.id,
            label=record.label,
            masked=mask(record.secret),
            source=record.source,
            enabled=record.enabled,
            weight=record.weight,
            allow_models=list(record.allow_models),
            quotas=dict(record.quotas or {}),
            status=key_status(state, record, resolved_now),
            reason=state.reason,
            until=state.until,
            consecutive_failures=state.consecutive_failures,
            failures_24h=state.failures_24h,
            requests_today=state.requests_today,
            tokens_today=state.tokens_today,
            last_used_at=state.last_used_at,
            active_requests=state.active_requests,
            ema_latency_ms=state.ema_latency_ms,
        ))

    return ProviderDetail(
        name=provider,
        base_url=pcfg.base_url,
        header_parser=pcfg.header_parser,
        key_strategy=pcfg.key_strategy,
        state=summary.state if summary is not None else "bad",
        status=wide.value,
        status_reason=wide.reason,
        configured_models=configured,
        models_alive=alive,
        models_gone=gone,
        keys=keys,
    )


@dataclass
class ModelRow:
    provider: str
    model: str
    buckets: list
    score: int
    rpm: int
    tpm: int
    context_window: int
    vision_configured: bool
    vision: Optional[CapabilityFact]
    tools: Optional[CapabilityFact]
    reasoning: Optional[CapabilityFact]
    learned_context: Optional[int]
    size_class: Optional[str]
    price_in: Optional[float]   # USD per million input tokens; None = not priced
    price_out: Optional[float]  # USD per million output tokens; None = not priced
    tokens_per_second: Optional[float]  # generation speed; None = not recorded
    quotas: dict  # rps/rph/rpd/tps/tph/tpd caps beyond rpm/tpm; {} = none set
    state: str  # the one status: ready | busy | struggling | needs_you | off
    why: str    # its one plain sentence
    requests: int  # lifetime requests logged for this model in audit.csv
    response_rate: Optional[float]  # answered / requests; None = no requests yet


def model_status(router, provider: str, model: str,
                 now: Optional[float] = None) -> "st.Status":
    """A configured model's one status, including the one worked out from
    config: a model whose provider isn't set up needs you (§10)."""
    if provider not in router._cfg.providers:
        return st.Status(st.NEEDS_YOU, f"No {provider} provider set up.", None,
                         st.REMOVE, "no_provider")
    return router._status.get(provider, model, now)


def models(router) -> list[ModelRow]:
    """Every model configured in any bucket, once each, with what's been
    learned about it from real traffic layered on top of what the owner
    declared in settings.
    """
    facts_store = router._model_facts
    by_key: dict[tuple[str, str], ModelRow] = {}

    from flexrouter.dashboard.stats import compute_stats
    per_model_stats = {
        e["model"]: e for e in compute_stats(router._cfg.state_dir)["errors"]["per_model"]
    }

    for tier_name, model_configs in router._cfg.tiers.items():
        for mc in model_configs:
            key = (mc.provider, mc.model)
            if key in by_key:
                by_key[key].buckets.append(tier_name)
                continue
            mf = facts_store.get(mc.provider, mc.model)
            status = model_status(router, mc.provider, mc.model)
            entry = per_model_stats.get(f"{mc.provider}/{mc.model}")
            requests = entry["requests"] if entry else 0
            response_rate = (1.0 - entry["rate"]) if entry else None
            by_key[key] = ModelRow(
                provider=mc.provider, model=mc.model, buckets=[tier_name],
                score=mc.score, rpm=mc.rpm, tpm=mc.tpm,
                context_window=mc.context_window,
                vision_configured=mc.vision,
                vision=mf.vision, tools=mf.tools, reasoning=mf.reasoning,
                learned_context=mf.context.value if mf.context else None,
                size_class=mf.size_class.value if mf.size_class else None,
                price_in=mc.price_in, price_out=mc.price_out,
                tokens_per_second=mc.tokens_per_second,
                quotas=dict(mc.quotas or {}),
                state=status.value,
                why=status.reason,
                requests=requests,
                response_rate=response_rate,
            )
    return sorted(by_key.values(), key=lambda r: (r.provider, r.model))


@dataclass
class BucketModelRow:
    provider: str
    model: str
    score: int
    tokens_per_second: Optional[float]
    available: bool
    in_the_running: bool
    reason: Optional[str]
    detail: str


@dataclass
class Bucket:
    name: str
    strategy: str  # "smartest" (ranks by score) or "fastest" (ranks by tokens_per_second)
    models: list  # BucketModelRow, best-ranked first


def buckets(router) -> list[Bucket]:
    """Every bucket, and for each of its models: would it actually be
    picked right now, and if not, why not.

    `explain_unavailable()` (flexrouter/engine.py) is the engine's own
    account of this - re-walking the same `_skip_reason()` `select()`
    uses - so this can never disagree with what a real request would do
    (Stage 8 roadmap ruling R8). The one thing added here is "in the
    running": the engine picks randomly among everything within 20% of
    the top-ranked value, so being available is not the same as being a
    live candidate. Which value that is depends on the bucket's strategy
    - score for "smartest", tokens_per_second for "fastest" - the same
    choice engine.py._pick() makes.
    """
    out = []
    for name in router._cfg.tiers:
        strategy = router._cfg.bucket_strategy.get(name, "smartest")
        rows = router._engine.explain_unavailable(name)

        def _value(r: dict) -> Optional[float]:
            return r["tokens_per_second"] if strategy == "fastest" else r["score"]

        available_values = [v for r in rows if r["available"] and (v := _value(r)) is not None]
        threshold = max(available_values) * 0.8 if available_values else None

        bucket_rows = [
            BucketModelRow(
                provider=r["provider"], model=r["model"], score=r["score"],
                tokens_per_second=r["tokens_per_second"],
                available=r["available"],
                in_the_running=bool(
                    r["available"] and threshold is not None
                    and _value(r) is not None and _value(r) >= threshold
                ),
                reason=r["reason"], detail=r["detail"],
            )
            for r in rows
        ]
        bucket_rows.sort(key=lambda row: (_value_or_low(row, strategy)), reverse=True)
        out.append(Bucket(name=name, strategy=strategy, models=bucket_rows))
    return out


def _value_or_low(row: "BucketModelRow", strategy: str) -> float:
    value = row.tokens_per_second if strategy == "fastest" else row.score
    return value if value is not None else float("-inf")


def disabled_models() -> list[str]:
    """Every `provider/model` identity currently disabled by an override.

    A disabled model is dropped from `FlexConfig.tiers` entirely
    (flexrouter/overrides.py's `apply_overrides`), so it has no live
    `ModelConfig` to read anything else from - this is only its identity,
    which is all "put it back" needs.
    """
    from flexrouter.overrides import _is_off
    models_ov = load_overrides().get("models", {})
    return sorted(
        ident for ident, fields in models_ov.items()
        if "enabled" in fields and _is_off(fields["enabled"])
    )


def pending_catalogue(router) -> dict:
    """The last catalogue refresh's findings: models that appeared,
    vanished, or changed a tracked field, grouped by provider.

    Read-only tray (Stage 8 roadmap ruling R7) - accept/reject arrives in
    sub-plan 7, once overrides.json can represent adding a model.
    """
    path = Path(router._cfg.state_dir) / "catalog_pending.json"
    return read_json(path, default={})


@dataclass
class RequestRow:
    id: str
    at: str
    bucket: str
    ok: bool
    answered_by: Optional[dict]  # {"provider": ..., "model": ...} or None
    tokens_in: int
    tokens_out: int
    ms_total: int
    skipped_count: int
    # "ok", "failover" (answered after at least one failed attempt) or
    # "failed" (nothing answered).
    outcome: str = "ok"
    attempt_count: int = 0


def _read_traces(router, last: int) -> list[dict]:
    """The last `last` trace entries, oldest first. Unreadable lines are
    skipped rather than breaking the page."""
    path = Path(router._cfg.state_dir) / "traces.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines()[-last:]:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _outcome(entry: dict) -> str:
    if not entry.get("ok"):
        return "failed"
    return "failover" if entry.get("attempts") else "ok"


def request_journey(router, request_id: str, search_last: int = 5000) -> Optional[dict]:
    """One request, told in order: what was passed over before anything was
    tried, each attempt that failed, and what finally answered (or that
    nothing did). Read straight from its trace; nothing is inferred."""
    for entry in reversed(_read_traces(router, search_last)):
        if entry.get("id") != request_id:
            continue
        steps = [{"kind": "skipped", "provider": s.get("provider", ""),
                  "model": s.get("model", ""), "reason": s.get("reason", ""),
                  "detail": s.get("detail", "")}
                 for s in entry.get("skipped") or []]
        steps += [{"kind": "failed", "provider": a.get("provider", ""),
                   "model": a.get("model", ""), "status": a.get("status"),
                   "message": a.get("provider_message") or "",
                   "verdict": a.get("verdict") or "", "ms": a.get("ms")}
                  for a in entry.get("attempts") or []]
        answered = entry.get("answered_by")
        if entry.get("ok") and answered:
            steps.append({"kind": "answered", "provider": answered.get("provider", ""),
                          "model": answered.get("model", ""), "ms": entry.get("ms_total")})
        else:
            steps.append({"kind": "gave_up"})
        tokens = entry.get("tokens") or {}
        return {"id": request_id, "at": entry.get("at", ""),
                "bucket": (entry.get("asked") or {}).get("bucket", ""),
                "ok": bool(entry.get("ok")), "outcome": _outcome(entry),
                "tokens_in": tokens.get("in", 0), "tokens_out": tokens.get("out", 0),
                "ms_total": entry.get("ms_total", 0), "steps": steps}
    return None


def recent_requests(router, limit: int = 50) -> list[RequestRow]:
    """The last `limit` requests, newest first, straight from traces.jsonl -
    the same record engine.py's `explain_unavailable()` already produces on
    every real call (flexrouter/traces.py). Nothing here is computed; this
    only reads and reshapes.
    """
    rows: list[RequestRow] = []
    for entry in _read_traces(router, limit):
        tokens = entry.get("tokens") or {}
        rows.append(RequestRow(
            id=entry.get("id", ""),
            at=entry.get("at", ""),
            bucket=(entry.get("asked") or {}).get("bucket", ""),
            ok=bool(entry.get("ok")),
            answered_by=entry.get("answered_by"),
            tokens_in=tokens.get("in", 0),
            tokens_out=tokens.get("out", 0),
            ms_total=entry.get("ms_total", 0),
            skipped_count=len(entry.get("skipped") or []),
            outcome=_outcome(entry),
            attempt_count=len(entry.get("attempts") or []),
        ))
    rows.reverse()
    return rows


def error_brain_entries(router) -> list:
    """Every learned error kind, entries awaiting review first, most
    recently seen first within each group. A plain read of
    `ErrorBrain._entries` (flexrouter/error_brain.py) - nothing is
    reclassified or written here.
    """
    entries = list(router._error_brain._entries.values())
    entries.sort(key=lambda e: e.last_at, reverse=True)
    entries.sort(key=lambda e: not e.flagged_for_review)
    return entries


@dataclass
class AllowanceRow:
    provider: str
    model: str
    quotas: dict
    quota_ok: bool
    quota_wait_seconds: float
    provider_rate_exhausted: bool
    provider_available_at: Optional[float]


def allowance(router) -> list[AllowanceRow]:
    """Free-tier headroom for every configured model: the owner's own
    request/day and request/hour caps (`QuotaTracker`, flexrouter/quota.py)
    and whatever the provider's own response headers most recently said
    (`RateLimitStore`, flexrouter/rate_limits.py). Money is out of scope
    (Stage 8 roadmap ruling R6) - nothing here tracks cost, because nothing
    in the service records it.
    """
    quota = router._quota_tracker
    rate_store = router._rate_limit_store
    seen = set()
    rows: list[AllowanceRow] = []
    for model_configs in router._cfg.tiers.values():
        for mc in model_configs:
            key = (mc.provider, mc.model)
            if key in seen:
                continue
            seen.add(key)
            quotas = dict(mc.quotas or {})
            quota_ok = quota.is_available(mc.provider, mc.model, quotas) if quotas else True
            wait = quota.seconds_until_available(mc.provider, mc.model, quotas) if quotas else 0.0
            rows.append(AllowanceRow(
                provider=mc.provider, model=mc.model, quotas=quotas,
                quota_ok=quota_ok, quota_wait_seconds=wait,
                provider_rate_exhausted=rate_store.is_exhausted(mc.provider, mc.model),
                provider_available_at=rate_store.available_at(mc.provider, mc.model),
            ))
    return sorted(rows, key=lambda r: (r.provider, r.model))


# word, window seconds, is this a token-count cap (True) or a request-count
# cap (False) - two caps can share the same window word ("day") but count
# different things, so callers must key on (window, unit), not window alone.
_QUOTA_WINDOWS = {
    "rps": ("second", 1, False), "rph": ("hour", 3600, False), "rpd": ("day", 86400, False),
    "tps": ("second", 1, True), "tph": ("hour", 3600, True), "tpd": ("day", 86400, True),
}


def _quota_usage(router, provider: str, model: str, quotas: dict, now: float) -> list[dict]:
    """The owner's own caps on one model, with what has been used of each.

    `frees_at` is when the next request becomes allowed again - only set
    when the cap is actually reached. These windows roll (the last 24
    hours, not "today"), so a full cap frees up one request (or enough
    tokens) at a time as the oldest event in the window ages out.
    """
    events = sorted(router._quota_tracker._state.get(f"{provider}/{model}", []))
    out = []
    for q_type, limit in quotas.items():
        if q_type not in _QUOTA_WINDOWS or not limit:
            continue
        word, seconds, is_token = _QUOTA_WINDOWS[q_type]
        unit = "tokens" if is_token else "requests"
        in_window = [e for e in events if e[0] > now - seconds]
        if is_token:
            used = sum(tokens for _, tokens in in_window)
        else:
            used = len(in_window)
        frees_at = None
        if used >= limit:
            if is_token:
                remaining = used
                for ts, tokens in in_window:
                    remaining -= tokens
                    if remaining < limit:
                        frees_at = ts + seconds
                        break
            else:
                frees_at = in_window[used - limit][0] + seconds
        out.append({"window": word, "unit": unit, "used": used, "limit": int(limit),
                    "frees_at": frees_at, "source": "yours"})
    return sorted(out, key=lambda lim: (0 if lim["window"] == "day" else
                                        1 if lim["window"] == "hour" else 2,
                                        lim["unit"]))


def _provider_says(router, provider: str, model: str, now: float) -> list[dict]:
    """What the provider's own response headers last said is left. The
    provider rarely says out of how many, so no total is invented here."""
    entry = router._rate_limit_store._data.get(f"{provider}/{model}", {})
    out = []
    for what, rem_key, reset_key in (("requests", "remaining_requests", "reset_requests_at"),
                                     ("tokens", "remaining_tokens", "reset_tokens_at")):
        if entry.get(rem_key) is None:
            continue
        reset = entry.get(reset_key)
        out.append({"what": what, "remaining": int(entry[rem_key]),
                    "resets_at": reset if reset and reset > now else None,
                    "source": "provider"})
    return out


def allowance_groups(router, now: Optional[float] = None) -> list[dict]:
    """Every configured model's limits, grouped by provider, providers in
    name order. Each model lists the owner's caps (`limits`) and what the
    provider last said (`provider_says`)."""
    now = time.time() if now is None else now
    by_provider: dict[str, list[dict]] = {}
    seen = set()
    for model_configs in router._cfg.tiers.values():
        for mc in model_configs:
            if (mc.provider, mc.model) in seen:
                continue
            seen.add((mc.provider, mc.model))
            by_provider.setdefault(mc.provider, []).append({
                "model": mc.model,
                "limits": _quota_usage(router, mc.provider, mc.model, dict(mc.quotas or {}), now),
                "provider_says": _provider_says(router, mc.provider, mc.model, now),
                "exhausted_until": router._rate_limit_store.available_at(mc.provider, mc.model),
            })
    return [{"provider": p, "keys": _key_count(router, p),
             "models": sorted(ms, key=lambda m: m["model"])}
            for p, ms in sorted(by_provider.items())]


def _key_count(router, provider: str) -> int:
    pcfg = router._cfg.providers.get(provider)
    if pcfg is None:
        return 0
    return len(getattr(pcfg, "keys", None) or getattr(pcfg, "api_keys", None) or [])


def allowance_stack(router, now: Optional[float] = None) -> dict:
    """Daily headroom summed across every provider - the whole point of
    stacking free tiers, as one number. Only daily caps the owner has set
    are counted: a provider that never said its total cannot be added up."""
    parts = []
    for g in allowance_groups(router, now):
        total = used = 0
        for m in g["models"]:
            for lim in m["limits"]:
                if lim["window"] == "day" and lim["unit"] == "requests":
                    total += lim["limit"]
                    used += min(lim["used"], lim["limit"])
        if total:
            parts.append({"provider": g["provider"], "left": total - used, "total": total})
    return {"parts": parts, "left": sum(p["left"] for p in parts),
            "total": sum(p["total"] for p in parts), "providers": len(parts)}


_SETTINGS_ATTR = {
    "port": "port", "dashboard_port": "port", "state_dir": "state_dir",
    "window_seconds": "window_seconds",
    "penalty_base_seconds": "penalty_base_seconds",
    "penalty_max_seconds": "penalty_max_seconds",
    "session_ttl_minutes": "session_ttl_minutes",
    "sample_interval_seconds": "sample_interval_seconds",
    "health_history_days": "health_history_days",
    "key_concurrency_cap": "key_concurrency_cap",
    "provider_budget": "provider_budget",
    "hooks": "hooks",
    "quarantine_seconds": "quarantine_seconds",
    "probe_timeout_seconds": "probe_timeout_seconds",
    "error_max_length": "error_max_length",
    "unscored_fallback_score": "unscored_fallback_score",
    "auto_add_models": "auto_add_models",
    "experimental_model_discovery": "experimental_model_discovery",
    "redact_errors": "redact_errors",
}

# Nested under cfg.decider rather than sitting flat on FlexConfig, so these
# cannot go through _SETTINGS_ATTR's plain getattr.
_DECIDER_ATTR = {
    "decider_base_url": "base_url",
    "decider_model": "model",
    "decider_timeout_seconds": "timeout_seconds",
    "decider_confidence_threshold": "confidence_threshold",
    "decider_rule_prior_confidence": "rule_prior_confidence",
    "decider_confidence_ceiling": "confidence_ceiling",
    "decider_contested_statuses": "contested_statuses",
}


@dataclass
class SettingsField:
    name: str
    value: object
    overridden: bool


def settings_fields(router) -> list[SettingsField]:
    """Every setting the Settings page may change, with its live value and
    whether that value came from an override or from the owner's own
    settings file. `retries`/`backoff_seconds` read through `router._cfg.retry`
    (flexrouter/config.py folds a `retry_policy` preset into those two
    numbers at load time - there is no separate live value for the preset
    name itself, only whatever override was last set for it, if any).
    """
    overridden = load_overrides().get("settings", {})
    out = []
    for name in sorted(ALLOWED_FIELDS["settings"]):
        if name in overridden:
            value = overridden[name]
        elif name == "retries":
            value = router._cfg.retry.retries
        elif name == "backoff_seconds":
            value = router._cfg.retry.backoff_seconds
        elif name in _DECIDER_ATTR:
            value = getattr(router._cfg.decider, _DECIDER_ATTR[name])
        elif name in _SETTINGS_ATTR:
            value = getattr(router._cfg, _SETTINGS_ATTR[name])
        else:
            value = None
        out.append(SettingsField(name=name, value=value, overridden=name in overridden))
    return out


def provider_editable_fields(router, provider: str) -> Optional[dict]:
    """The provider fields the Settings/Providers write forms may change,
    with the live value and whether it is currently an override.
    """
    pcfg = router._cfg.providers.get(provider)
    if pcfg is None:
        return None
    overridden = load_overrides().get("providers", {}).get(provider, {})
    fields = {}
    for name in sorted(ALLOWED_FIELDS["providers"]):
        fields[name] = {
            "value": overridden.get(name, getattr(pcfg, name, None)),
            "overridden": name in overridden,
        }
    return {"fields": fields, "overridden_at_all": bool(overridden)}


def model_editable_fields(router, provider: str, model: str) -> Optional[dict]:
    """The model fields the Models write form may change, with the live
    value and whether it is currently an override.

    Only reachable for a model that currently appears in a bucket - a
    model disabled by an `enabled: false` override is, by design, dropped
    from `FlexConfig.tiers` entirely (flexrouter/overrides.py), so there
    is nothing here to read a live value from; "put it back" (clearing the
    whole override) is done from the same place the "disable" button is.
    """
    mc = None
    for model_configs in router._cfg.tiers.values():
        mc = next((m for m in model_configs
                   if m.provider == provider and m.model == model), None)
        if mc is not None:
            break
    if mc is None:
        return None

    overridden = load_overrides().get("models", {}).get(f"{provider}/{model}", {})
    live = {
        "score": mc.score, "rpm": mc.rpm, "tpm": mc.tpm,
        "context_window": mc.context_window, "vision": mc.vision,
        "quotas": mc.quotas,
    }
    fields = {
        name: {"value": overridden.get(name, live[name]), "overridden": name in overridden}
        for name in ("score", "rpm", "tpm", "context_window", "vision", "quotas")
    }
    return {"fields": fields, "overridden_at_all": bool(overridden)}


def broken(router, now: Optional[float] = None) -> dict:
    """The two piles: what only the owner can fix (Needs you), and what the
    service is handling on its own (Busy, Struggling).

    Every entry is a read of the one status each model and key has
    (flexrouter/status.py, key_state.py). A model that needs you is in the
    first pile - never shown as fine next to a count of things that need
    you. Nothing here writes.
    """
    resolved_now = now if now is not None else time.time()
    states = router._key_states

    needs_you: list[BrokenItem] = []
    handling_itself: list[BrokenItem] = []

    def _since(ts: Optional[float]) -> Optional[str]:
        if ts is None:
            return None
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")

    providers = list(router._cfg.providers)
    for name in _all_providers(router):
        wide = router._status.provider_status(name, resolved_now) \
            if name in router._cfg.providers else None
        if wide is not None and wide.value == st.NEEDS_YOU:
            needs_you.append(BrokenItem(
                pile="you", kind="provider_needs_you", provider=name, detail="",
                reason=wide.reason, since=_since(wide.since), status=wide.value,
                action=wide.action, provider_text=wide.detail))
            continue

        for model in _models_of(router, name):
            s = model_status(router, name, model, resolved_now)
            if s.value == st.READY:
                continue
            item = BrokenItem(
                pile="you" if s.value == st.NEEDS_YOU else "service",
                kind=f"model_{s.value}", provider=name, detail=model, model=model,
                reason=s.reason, since=_since(s.since), until=s.until,
                status=s.value, action=s.action, provider_text=s.detail)
            (needs_you if s.value == st.NEEDS_YOU else handling_itself).append(item)

        if name not in providers:
            continue
        for record in (router._cfg.providers[name].keys or []):
            if not record.enabled:
                continue
            state = states.get(name, record.id, resolved_now)
            value = key_status(state, record, resolved_now)
            label = record.label or record.id
            if value == st.NEEDS_YOU:
                needs_you.append(BrokenItem(
                    pile="you", kind="key_needs_you", provider=name,
                    detail=label, reason=state.reason, status=value,
                    action=st.REPLACE_KEY))
            elif value == st.BUSY:
                handling_itself.append(BrokenItem(
                    pile="service", kind="key_busy", provider=name,
                    detail=label, reason=state.reason, until=state.until, status=value))

    for entry in router._error_brain._entries.values():
        if entry.flagged_for_review:
            needs_you.append(BrokenItem(
                pile="you", kind="unclear_error", provider="", detail=entry.sample,
                reason=f'guessed "{entry.verdict}", only {entry.confidence:.0%} sure',
                since=entry.first_at,
            ))

    return {"needs_you": needs_you, "handling_itself": handling_itself}


def _all_providers(router) -> list[str]:
    """Configured providers, then any a bucket names that isn't set up."""
    names = list(router._cfg.providers)
    for model_configs in router._cfg.tiers.values():
        for mc in model_configs:
            if mc.provider not in names:
                names.append(mc.provider)
    return names


def status_counts(router, now: Optional[float] = None) -> dict:
    """How many models are in each status, once each - the summary strip.

    Off models are the ones an `enabled: false` override took out of the
    live config; they are counted from overrides.json.
    """
    resolved_now = now if now is not None else time.time()
    counts = {v: 0 for v in st.STATUSES}
    seen: set = set()
    for model_configs in router._cfg.tiers.values():
        for mc in model_configs:
            if (mc.provider, mc.model) in seen:
                continue
            seen.add((mc.provider, mc.model))
            counts[model_status(router, mc.provider, mc.model, resolved_now).value] += 1
    try:
        overridden = load_overrides().get("models", {}) or {}
    except Exception:  # noqa: BLE001 - a count is never worth a crash
        overridden = {}
    from flexrouter.overrides import _is_off
    counts[st.OFF] = sum(
        1 for ident, fields in overridden.items()
        if isinstance(fields, dict) and _is_off(fields.get("enabled", True))
        and tuple(ident.split("/", 1)) not in seen)
    return counts


def overview(router, now: Optional[float] = None,
             summaries: Optional[list[ProviderSummary]] = None,
             broken_data: Optional[dict] = None) -> dict:
    """Everything the front page shows.

    Pass `summaries` when the caller already has a `provider_summaries()`
    result (e.g. because it also renders a table from it) so the page
    doesn't compute the same thing twice against two different clocks,
    which can make a summary tile and a table row disagree about a key
    whose cooldown elapsed in between. `provider_summaries` stays callable
    on its own for callers that only need it.
    """
    if summaries is None:
        summaries = provider_summaries(router, now)

    counts = status_counts(router, now)
    models_total = sum(v for k, v in counts.items() if k != st.OFF)
    models_available = counts[st.READY]

    entries = router._error_brain._entries
    awaiting = sum(1 for e in entries.values() if e.flagged_for_review)

    facts_store = router._model_facts._facts

    if broken_data is None:
        broken_data = broken(router, now)

    return {
        "providers": {
            "total": len(summaries),
            "ok": sum(1 for s in summaries if s.state == "ok"),
            "warn": sum(1 for s in summaries if s.state == "warn"),
            "bad": sum(1 for s in summaries if s.state == "bad"),
        },
        "keys": {
            "total": sum(s.key_count for s in summaries),
            "ready": sum(s.keys_ready for s in summaries),
            "busy": sum(s.keys_busy for s in summaries),
            "needs_you": sum(s.keys_need_you for s in summaries),
            "off": sum(s.keys_off for s in summaries),
        },
        "models": {"total": models_total, "available": models_available,
                   "statuses": counts},
        "learned": {
            "error_kinds": len(entries),
            "awaiting_you": awaiting,
            "models_with_facts": len(facts_store),
        },
        "service": {
            "buckets": list(router._cfg.tiers),
            "state_dir": router._cfg.state_dir,
            "port": router._cfg.port or router._cfg.dashboard_port,
        },
        "needs_you": len(broken_data["needs_you"]),
    }
