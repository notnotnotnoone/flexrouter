"""Every machine-made settings change.

config.yaml is hand-written and never rewritten, so the owner's comments
survive permanently (spec §1). Anything the dashboard changes lands here and
is merged over the parsed settings at load time.
"""
from __future__ import annotations

import copy
from pathlib import Path

from flexrouter import home
from flexrouter.store import read_json, write_json

SECTIONS = ("settings", "providers", "models")


def _path(path: Path | str | None) -> Path:
    return Path(path) if path else home.overrides_path()


def load_overrides(path: Path | str | None = None) -> dict:
    return read_json(_path(path), default={}) or {}


def save_overrides(data: dict, path: Path | str | None = None) -> None:
    write_json(_path(path), data)


def set_override(section: str, key: str, field: str | None, value,
                 path: Path | str | None = None) -> None:
    if section not in SECTIONS:
        raise ValueError(f"unknown override section {section!r}")
    data = load_overrides(path)
    if section == "settings":
        data.setdefault("settings", {})[key] = value
    else:
        data.setdefault(section, {}).setdefault(key, {})[field] = value
    save_overrides(data, path)


def clear_override(section: str, key: str, path: Path | str | None = None) -> bool:
    data = load_overrides(path)
    bucket = data.get(section, {})
    if key not in bucket:
        return False
    bucket.pop(key)
    if not bucket:
        data.pop(section, None)
    save_overrides(data, path)
    return True


def _bucket_key(raw: dict) -> str | None:
    if raw.get("buckets"):
        return "buckets"
    if raw.get("tiers"):
        return "tiers"
    return None


def apply_overrides(raw: dict, ov: dict) -> dict:
    """Merge overrides over parsed settings. Shallow, per settings key,
    per provider, and per provider/model. Returns a new dict."""
    merged = copy.deepcopy(raw)
    if not ov:
        return merged

    for key, value in (ov.get("settings") or {}).items():
        merged.setdefault("settings", {})[key] = value

    for name, fields in (ov.get("providers") or {}).items():
        target = merged.setdefault("providers", {}).setdefault(name, {})
        target.update(fields or {})

    model_ov = ov.get("models") or {}
    bkey = _bucket_key(merged)
    if model_ov and bkey:
        for bucket_name, models in list((merged.get(bkey) or {}).items()):
            kept = []
            for entry in models or []:
                ident = f"{entry.get('provider')}/{entry.get('model')}"
                fields = dict(model_ov.get(ident) or {})
                if fields.pop("enabled", True) is False:
                    continue
                entry.update(fields)
                kept.append(entry)
            merged[bkey][bucket_name] = kept

    return merged
