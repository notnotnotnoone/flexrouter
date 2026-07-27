from __future__ import annotations
import asyncio
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from flexrouter.config import is_probably_chat_model
from flexrouter.onboard import PROVIDERS, discover_models, score_with_aa, build_yaml, _context_window
from flexrouter.rate_limits import RateLimitStore


@dataclass
class RefreshResult:
    timestamp: str
    added: list
    removed: list
    changed: list
    backup_path: str
    provider_errors: list


def _existing(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    return yaml.safe_load(config_path.read_text()) or {}


def _provider_keys(raw: dict) -> dict:
    out = {}
    for name, pcfg in (raw.get("providers") or {}).items():
        keys = (pcfg or {}).get("api_keys", [])
        for k in keys:
            val = k.get("key") if isinstance(k, dict) else k
            if val:
                out[name] = val
                break
    return out


def _old_models(raw: dict) -> dict:
    out = {}
    for models in (raw.get("tiers") or {}).values():
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


def refresh_config(config_path: str, state_dir: str, aa_key: str | None = None) -> RefreshResult:
    cfg_path = Path(config_path)
    raw = _existing(cfg_path)
    provider_keys = _provider_keys(raw)
    old = _old_models(raw)
    store = RateLimitStore(state_dir)

    free, paid, errors = asyncio.run(_discover_all(provider_keys, store))
    free = asyncio.run(score_with_aa(free, aa_key))
    paid = asyncio.run(score_with_aa(paid, aa_key))

    new = {}
    for m in free + paid:
        new[f"{m['_provider']}/{m['id']}"] = {
            "rpm": m.get("rpm"), "tpm": m.get("tpm"), "context_window": _context_window(m),
        }

    added = sorted(k for k in new if k not in old)
    removed = sorted(k for k in old if k not in new)
    changed = []
    for k in sorted(set(old) & set(new)):
        for field_name in ("rpm", "tpm", "context_window"):
            ov, nv = old[k].get(field_name), new[k].get(field_name)
            if ov is not None and nv is not None and ov != nv:
                changed.append({"model": k, "field": field_name, "old": ov, "new": nv})

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    backups = Path(state_dir) / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup_path = backups / f"flexrouter-{ts.replace(':', '').replace('-', '')}.yaml"
    if cfg_path.exists():
        backup_path.write_text(cfg_path.read_text())

    new_yaml = build_yaml(provider_keys, free, paid, PROVIDERS)
    cfg_path.write_text(new_yaml)

    result = RefreshResult(
        timestamp=ts, added=added, removed=removed, changed=changed,
        backup_path=str(backup_path), provider_errors=errors,
    )
    tmp = Path(state_dir) / "last_refresh.json.tmp"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(asdict(result), indent=2))
    os.replace(tmp, Path(state_dir) / "last_refresh.json")
    return result
