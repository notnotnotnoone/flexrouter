from __future__ import annotations
import asyncio
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from flexrouter.catalogue import PROVIDERS, discover_models, score_with_aa, _context_window
from flexrouter.config import is_probably_chat_model, resolve_keys
from flexrouter.keys import load_keys, mask
from flexrouter.rate_limits import RateLimitStore
from flexrouter.store import read_json, write_json


@dataclass
class RefreshResult:
    """What a catalogue refresh found — not what it changed.

    config.yaml is hand-written and is never rewritten by refresh (spec §1),
    and overrides.json is untouched until the owner accepts an item. So
    `added`, `removed` and `changed` describe what refresh *found* when it
    compared the provider catalogues against the settings file — new models
    that appeared, old ones that vanished, rate limits or context windows
    that moved — not anything that was applied. The full findings, with
    enough detail that a later stage can apply an accepted item without
    re-querying the provider, are written to `state/catalog_pending.json`
    (see `pending_path`).
    """
    timestamp: str
    added: list
    removed: list
    changed: list
    provider_errors: list
    pending_path: str


def _existing(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    return yaml.safe_load(config_path.read_text()) or {}


def _provider_keys(raw: dict, config_path: Path | None = None) -> dict:
    """One usable credential per provider, resolved the way everything else
    resolves one: a key saved with `flexrouter keys add` first, then the
    environment variable the settings name, then a key typed into the
    settings file (deprecated).

    Reading only inline `api_keys` — which is what this used to do — finds
    nothing on a correct installation, so refresh silently checked nothing
    and reported all clear.
    """
    vault = load_keys()
    out: dict[str, str] = {}
    for name, praw in (raw.get("providers") or {}).items():
        records = resolve_keys(name, praw or {}, vault, config_path)
        for r in records:
            if r.secret:
                out[name] = r.secret
                break
    return out


def _known_secrets(provider_keys: dict) -> list[str]:
    """Every credential value in play, longest first.

    Some providers echo the submitted credential back inside their error
    bodies, and that text is stored in state/last_refresh.json and shown on
    the dashboard. Longest-first so a key that is a prefix of another does
    not scrub the wrong span.
    """
    vault = load_keys()
    secrets = {s for s in provider_keys.values() if s}
    for records in vault.values():
        secrets.update(r.secret for r in records if r.secret)
    return sorted(secrets, key=len, reverse=True)


def _scrub(text: str, secrets: list[str]) -> str:
    """Replace any known credential in provider error text with its mask."""
    out = str(text)
    for secret in secrets:
        if secret and secret in out:
            out = out.replace(secret, mask(secret))
    return out


def _old_models(raw: dict) -> dict:
    """The models the settings file lists. `buckets:` is the preferred
    spelling, `tiers:` the accepted fallback, exactly as load_config reads
    them. Reading only `tiers:` found nothing in a starter home."""
    buckets = raw.get("buckets")
    if buckets is None:
        buckets = raw.get("tiers")
    out = {}
    for models in (buckets or {}).values():
        for m in models or []:
            out[f"{m['provider']}/{m['model']}"] = {
                "rpm": m.get("rpm"), "tpm": m.get("tpm"), "context_window": m.get("context_window"),
            }
    return out


async def _discover_all(provider_keys: dict, store: RateLimitStore):
    pmap = {p.name: p for p in PROVIDERS}
    free, paid, errors = [], [], []
    for name, key in provider_keys.items():
        pdef = pmap.get(name)
        if pdef is None:
            continue
        try:
            models = await discover_models(pdef, key)
        except Exception as exc:
            errors.append({"provider": name, "error": str(exc)})
            continue
        for m in models:
            mid = m.get("id", "")
            if not mid or not is_probably_chat_model(mid):
                continue
            m = {**m, "_provider": name}
            known_rpm = store.get_rpm(name, mid, 0)
            known_tpm = store.get_tpm(name, mid, 0)
            if known_rpm > 0:
                m["rpm"] = known_rpm
            if known_tpm > 0:
                m["tpm"] = known_tpm
            (free if pdef.free else paid).append(m)
    return free, paid, errors


def _new_pending_bucket(ts: str) -> dict:
    return {"checked_at": ts, "appeared": [], "vanished": [], "changed": []}


def refresh_config(config_path: str, state_dir: str, aa_key: str | None = None) -> RefreshResult:
    """Check providers for catalogue changes and record what was found.

    This never touches config.yaml (hand-written, comments must survive
    forever) and never touches overrides.json (nothing is applied until the
    owner accepts it). It only writes two record files under `state_dir`:
    `catalog_pending.json` (the findings, grouped by provider) and
    `last_refresh.json` (this call's `RefreshResult`, for the dashboard).

    A provider whose catalogue call failed is not treated as checked: it is
    left out of the comparison and out of this run's pending entries, and
    whatever an earlier run recorded for it is carried forward untouched.
    """
    cfg_path = Path(config_path)
    raw = _existing(cfg_path)
    provider_keys = _provider_keys(raw, cfg_path)
    store = RateLimitStore(state_dir)

    free, paid, errors = asyncio.run(_discover_all(provider_keys, store))
    free = asyncio.run(score_with_aa(free, aa_key))
    paid = asyncio.run(score_with_aa(paid, aa_key))

    secrets = _known_secrets(provider_keys)
    errors = [{**e, "error": _scrub(e.get("error", ""), secrets)} for e in errors]

    # A provider whose catalogue call failed was not checked. Diffing its
    # configured models against the nothing we got back would report every
    # one of them as vanished, so it is excluded from the comparison
    # entirely — "the provider is down" must never read as "all clear".
    failed = {e["provider"] for e in errors}
    checked = [name for name in provider_keys if name not in failed]
    old = {k: v for k, v in _old_models(raw).items()
           if k.split("/", 1)[0] in checked}

    new = {}
    facts = {}
    for models, is_free in ((free, True), (paid, False)):
        for m in models:
            ident = f"{m['_provider']}/{m['id']}"
            entry = {"rpm": m.get("rpm"), "tpm": m.get("tpm"), "context_window": _context_window(m)}
            new[ident] = entry
            facts[ident] = {**entry, "score": m.get("score"), "free": is_free}

    added = sorted(k for k in new if k not in old)
    removed = sorted(k for k in old if k not in new)
    changed = []
    for k in sorted(set(old) & set(new)):
        for field_name in ("rpm", "tpm", "context_window"):
            ov, nv = old[k].get(field_name), new[k].get(field_name)
            if ov is not None and nv is not None and ov != nv:
                changed.append({"model": k, "field": field_name, "old": ov, "new": nv})

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Grouped by provider, per spec §6 ("Catalogue refresh"): checked_at plus
    # what appeared and what vanished. Every provider we actually checked
    # gets an entry, even with nothing to report, so "when it was checked"
    # is always available. `appeared` entries carry the full model facts
    # (rate limits, context window, score, free/paid) rather than bare model
    # ids, and a `changed` list is added alongside the spec's two fields, so
    # a later stage can apply an accepted item without re-discovering it.
    # What an earlier run found for a provider we did not reach this time is
    # still the best information there is, so it is carried forward rather
    # than dropped. Only a provider we actually checked gets a fresh bucket.
    pending_path = Path(state_dir) / "catalog_pending.json"
    pending: dict[str, dict] = read_json(pending_path, default={}) or {}
    for name in checked:
        pending[name] = _new_pending_bucket(ts)
    for ident in added:
        provider, model_id = ident.split("/", 1)
        bucket = pending.setdefault(provider, _new_pending_bucket(ts))
        bucket["appeared"].append({"model": model_id, **facts[ident]})
    for ident in removed:
        provider, model_id = ident.split("/", 1)
        bucket = pending.setdefault(provider, _new_pending_bucket(ts))
        bucket["vanished"].append(model_id)
    for c in changed:
        provider, model_id = c["model"].split("/", 1)
        bucket = pending.setdefault(provider, _new_pending_bucket(ts))
        bucket["changed"].append({
            "model": model_id, "field": c["field"], "old": c["old"], "new": c["new"],
        })

    write_json(pending_path, pending)

    result = RefreshResult(
        timestamp=ts, added=added, removed=removed, changed=changed,
        provider_errors=errors, pending_path=str(pending_path),
    )
    write_json(Path(state_dir) / "last_refresh.json", asdict(result))
    return result
