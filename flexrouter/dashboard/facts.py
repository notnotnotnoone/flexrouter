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
from flexrouter.store import read_json


@dataclass
class BrokenItem:
    pile: str  # "you" | "service"
    kind: str
    provider: str
    detail: str
    reason: str
    since: Optional[str] = None
    until: Optional[float] = None


@dataclass
class ProviderSummary:
    name: str
    base_url: str
    key_count: int
    keys_live: int
    keys_cooling: int
    keys_parked: int
    models_total: int
    quarantined: bool
    quarantine_reason: Optional[str]
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
    quarantined: bool
    quarantine_reason: Optional[str]
    configured_models: list
    models_alive: list
    models_gone: list
    keys: list  # list[KeyDetail]


def _models_of(router, provider: str) -> list:
    """Every configured model belonging to one provider, across all buckets."""
    seen = []
    for model_configs in router._cfg.tiers.values():
        for mc in model_configs:
            if mc.provider == provider and mc.model not in seen:
                seen.append(mc.model)
    return seen


def provider_summaries(router, now: Optional[float] = None) -> list[ProviderSummary]:
    penalties = router._engine._penalties
    states = router._key_states
    resolved_now = now if now is not None else time.time()
    out = []
    for name, pcfg in router._cfg.providers.items():
        records = list(pcfg.keys or [])
        live = cooling = parked = 0
        for record in records:
            if not record.enabled:
                parked += 1
                continue
            # A read, not a call to is_available(): that method mutates and
            # persists a cooling key whose cooldown has elapsed (it flips the
            # stored status to "live"). Rendering a page must never write to
            # disk, so the same expiry check is done here without saving it.
            state = states.get(name, record.id, now)
            expired_cooldown = (
                state.status == "cooling"
                and state.until is not None
                and resolved_now >= state.until
            )
            if state.status == "live" or expired_cooldown:
                live += 1
            elif state.status == "cooling":
                cooling += 1
            else:
                parked += 1
        key_count = len(records)

        quarantined = penalties.is_quarantined(name, "*")
        reason = penalties.quarantine_reason(name, "*")
        if quarantined:
            state = "bad"
        elif key_count == 0:
            state = "bad"
        elif live == 0 and cooling == 0:
            # Nothing usable and nothing due to recover on its own.
            state = "bad"
        elif live == 0 or cooling or parked:
            state = "warn"
        else:
            state = "ok"

        out.append(ProviderSummary(
            name=name,
            base_url=pcfg.base_url,
            key_count=key_count,
            keys_live=live,
            keys_cooling=cooling,
            keys_parked=parked,
            models_total=len(_models_of(router, name)),
            quarantined=quarantined,
            quarantine_reason=reason,
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

    penalties = router._engine._penalties
    states = router._key_states
    resolved_now = now if now is not None else time.time()

    summary = next((s for s in provider_summaries(router, now) if s.name == provider), None)

    configured = _models_of(router, provider)
    provider_down = penalties.is_quarantined(provider, "*")
    alive, gone = [], []
    for model in configured:
        if provider_down or penalties.is_quarantined(provider, model):
            gone.append(model)
        else:
            alive.append(model)

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
            status=state.status,
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
        quarantined=provider_down,
        quarantine_reason=penalties.quarantine_reason(provider, "*"),
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
    state: str  # "available" | "gone"
    why: str


def models(router) -> list[ModelRow]:
    """Every model configured in any bucket, once each, with what's been
    learned about it from real traffic layered on top of what the owner
    declared in settings.
    """
    penalties = router._engine._penalties
    facts_store = router._model_facts
    by_key: dict[tuple[str, str], ModelRow] = {}

    for tier_name, model_configs in router._cfg.tiers.items():
        for mc in model_configs:
            key = (mc.provider, mc.model)
            if key in by_key:
                by_key[key].buckets.append(tier_name)
                continue
            mf = facts_store.get(mc.provider, mc.model)
            available = not (
                penalties.is_quarantined(mc.provider, mc.model)
                or penalties.is_quarantined(mc.provider, "*")
                or penalties.is_penalized(mc.provider, mc.model)
            )
            reason = "" if available else (
                penalties.quarantine_reason(mc.provider, mc.model)
                or penalties.quarantine_reason(mc.provider, "*")
                or "temporarily set aside after a recent failure"
            )
            by_key[key] = ModelRow(
                provider=mc.provider, model=mc.model, buckets=[tier_name],
                score=mc.score, rpm=mc.rpm, tpm=mc.tpm,
                context_window=mc.context_window,
                vision_configured=mc.vision,
                vision=mf.vision, tools=mf.tools, reasoning=mf.reasoning,
                learned_context=mf.context.value if mf.context else None,
                size_class=mf.size_class.value if mf.size_class else None,
                price_in=mc.price_in, price_out=mc.price_out,
                state="available" if available else "gone",
                why=reason,
            )
    return sorted(by_key.values(), key=lambda r: (r.provider, r.model))


@dataclass
class BucketModelRow:
    provider: str
    model: str
    score: int
    available: bool
    in_the_running: bool
    reason: Optional[str]
    detail: str


@dataclass
class Bucket:
    name: str
    models: list  # BucketModelRow, best score first


def buckets(router) -> list[Bucket]:
    """Every bucket, and for each of its models: would it actually be
    picked right now, and if not, why not.

    `explain_unavailable()` (flexrouter/engine.py) is the engine's own
    account of this - re-walking the same `_skip_reason()` `select()`
    uses - so this can never disagree with what a real request would do
    (Stage 8 roadmap ruling R8). The one thing added here is "in the
    running": the engine picks randomly among everything within 20% of
    the top score, so being available is not the same as being a live
    candidate.
    """
    out = []
    for name in router._cfg.tiers:
        rows = router._engine.explain_unavailable(name)
        available_scores = [r["score"] for r in rows if r["available"]]
        threshold = max(available_scores) * 0.8 if available_scores else None

        bucket_rows = [
            BucketModelRow(
                provider=r["provider"], model=r["model"], score=r["score"],
                available=r["available"],
                in_the_running=bool(
                    r["available"] and threshold is not None and r["score"] >= threshold
                ),
                reason=r["reason"], detail=r["detail"],
            )
            for r in rows
        ]
        bucket_rows.sort(key=lambda row: row.score, reverse=True)
        out.append(Bucket(name=name, models=bucket_rows))
    return out


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


_QUOTA_WINDOWS = {"rpd": ("day", 86400), "rph": ("hour", 3600)}


def _quota_usage(router, provider: str, model: str, quotas: dict, now: float) -> list[dict]:
    """The owner's own caps on one model, with what has been used of each.

    `frees_at` is when the next request becomes allowed again - only set
    when the cap is actually reached. These windows roll (the last 24
    hours, not "today"), so a full cap frees up one request at a time as
    the oldest request in the window ages out.
    """
    stamps = sorted(router._quota_tracker._state.get(f"{provider}/{model}", []))
    out = []
    for q_type, limit in quotas.items():
        if q_type not in _QUOTA_WINDOWS or not limit:
            continue
        word, seconds = _QUOTA_WINDOWS[q_type]
        in_window = [t for t in stamps if t > now - seconds]
        used = len(in_window)
        frees_at = None
        if used >= limit:
            frees_at = in_window[used - limit] + seconds
        out.append({"window": word, "used": used, "limit": int(limit),
                    "frees_at": frees_at, "source": "yours"})
    return sorted(out, key=lambda lim: 0 if lim["window"] == "day" else 1)


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
                if lim["window"] == "day":
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
    """The two piles: what only the owner can fix, and what the service is
    already handling on its own.

    Every entry here is a read of state some other part of the router
    already maintains - a provider-wide quarantine, a benched or cooling
    key, an error the classifier wasn't confident about. Nothing here is a
    new source of truth, and nothing here writes.
    """
    resolved_now = now if now is not None else time.time()
    penalties = router._engine._penalties
    states = router._key_states

    needs_you: list[BrokenItem] = []
    handling_itself: list[BrokenItem] = []

    for name, pcfg in router._cfg.providers.items():
        provider_down = penalties.is_quarantined(name, "*")
        if provider_down:
            needs_you.append(BrokenItem(
                pile="you", kind="provider_down", provider=name, detail="",
                reason=penalties.quarantine_reason(name, "*") or "",
            ))

        for model in _models_of(router, name):
            if penalties.is_quarantined(name, model) and not provider_down:
                handling_itself.append(BrokenItem(
                    pile="service", kind="model_set_aside", provider=name,
                    detail=model, reason=penalties.quarantine_reason(name, model) or "",
                ))

        for record in (pcfg.keys or []):
            if not record.enabled:
                continue
            state = states.get(name, record.id, resolved_now)
            label = record.label or record.id
            if state.status == "benched":
                needs_you.append(BrokenItem(
                    pile="you", kind="key_benched", provider=name,
                    detail=label, reason=state.reason,
                ))
            elif state.status == "cooling":
                still_cooling = state.until is not None and resolved_now < state.until
                if still_cooling:
                    handling_itself.append(BrokenItem(
                        pile="service", kind="key_cooling", provider=name,
                        detail=label, reason=state.reason, until=state.until,
                    ))

    for entry in router._error_brain._entries.values():
        if entry.flagged_for_review:
            needs_you.append(BrokenItem(
                pile="you", kind="unclear_error", provider="", detail=entry.sample,
                reason=f'guessed "{entry.verdict}", only {entry.confidence:.0%} sure',
                since=entry.first_at,
            ))

    return {"needs_you": needs_you, "handling_itself": handling_itself}


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
    penalties = router._engine._penalties

    models_total = 0
    models_available = 0
    for name in router._cfg.providers:
        for model in _models_of(router, name):
            models_total += 1
            if not (penalties.is_quarantined(name, model)
                    or penalties.is_quarantined(name, "*")
                    or penalties.is_penalized(name, model)):
                models_available += 1

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
            "live": sum(s.keys_live for s in summaries),
            "cooling": sum(s.keys_cooling for s in summaries),
            "parked": sum(s.keys_parked for s in summaries),
        },
        "models": {"total": models_total, "available": models_available},
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
