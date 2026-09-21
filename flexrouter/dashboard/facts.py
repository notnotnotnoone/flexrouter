"""Read-only views of a running router, for the dashboard to render.

Every fact here is computed from objects `LocalRouter` already builds. This
module never writes, never calls a provider, and never teaches `engine.py`
anything - the established pattern across ADRs 0009-0014.

It returns plain data. Nothing here knows what HTML is.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


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


def overview(router, now: Optional[float] = None,
             summaries: Optional[list[ProviderSummary]] = None) -> dict:
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
    }
