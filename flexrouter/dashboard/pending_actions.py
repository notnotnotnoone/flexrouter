"""Accept or reject one entry from the pending catalogue tray.

Closes .scratch/v2-stage7-followups/issues/01: overrides.json can now
represent a model config.yaml has never heard of (`overrides.add_model`),
and accepting a vanished model's removal uses the `enabled: false`
mechanism that already exists for a model config.yaml still declares
(flexrouter/overrides.py). Nothing here calls a provider, and nothing
here touches config.yaml - only catalog_pending.json (a scratch record
of the last catalogue refresh) and overrides.json.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from flexrouter import overrides as ov
from flexrouter.store import read_json, write_json


class PendingActionError(Exception):
    """A pending item could not be found or accepted - reported to the
    page, not a crash."""


def _path(state_dir: str) -> Path:
    return Path(state_dir) / "catalog_pending.json"


def _load(state_dir: str) -> dict:
    return read_json(_path(state_dir), default={})


def _save(state_dir: str, data: dict) -> None:
    write_json(_path(state_dir), data)


def _appeared_fields(provider: str, model: str, found: dict) -> dict:
    fields = {
        "provider": provider, "model": model,
        "score": found.get("score") or 50,
        "rpm": found.get("rpm") or 60,
        "tpm": found.get("tpm") or 60000,
    }
    if found.get("context_window"):
        fields["context_window"] = found["context_window"]
    return fields


def accept_appeared(state_dir: str, provider: str, model: str, bucket: str,
                    overrides_path: Optional[Path] = None) -> None:
    data = _load(state_dir)
    bucket_entry = data.get(provider, {})
    appeared = bucket_entry.get("appeared") or []
    found = next((m for m in appeared if m.get("model") == model), None)
    if found is None:
        raise PendingActionError(f"{provider}/{model} is not a pending new model")

    ov.add_model(bucket, _appeared_fields(provider, model, found), overrides_path)

    bucket_entry["appeared"] = [m for m in appeared if m.get("model") != model]
    data[provider] = bucket_entry
    _save(state_dir, data)


def accept_all_appeared(state_dir: str, bucket: str,
                        overrides_path: Optional[Path] = None) -> int:
    """Accept every pending new model, from every provider, into one bucket.

    The per-item accept/reject above stays for picking through changes one
    at a time; this is the bulk path for "just add everything the last
    catalogue check found" - one call to discover every provider with a
    valid key already ran (`refresh_config`) and left its findings in
    `catalog_pending.json`, this only clears the `appeared` side of it.
    """
    data = _load(state_dir)
    count = 0
    for provider, bucket_entry in data.items():
        appeared = bucket_entry.get("appeared") or []
        for m in appeared:
            model = m.get("model")
            if not model:
                continue
            ov.add_model(bucket, _appeared_fields(provider, model, m), overrides_path)
            count += 1
        bucket_entry["appeared"] = []
        data[provider] = bucket_entry
    _save(state_dir, data)
    return count


def reject_appeared(state_dir: str, provider: str, model: str) -> None:
    data = _load(state_dir)
    bucket_entry = data.get(provider, {})
    bucket_entry["appeared"] = [
        m for m in (bucket_entry.get("appeared") or []) if m.get("model") != model
    ]
    data[provider] = bucket_entry
    _save(state_dir, data)


def accept_vanished(state_dir: str, provider: str, model: str,
                    overrides_path: Optional[Path] = None) -> None:
    data = _load(state_dir)
    bucket_entry = data.get(provider, {})
    vanished = bucket_entry.get("vanished") or []
    if model not in vanished:
        raise PendingActionError(f"{provider}/{model} is not a pending vanished model")

    ov.set_override("models", f"{provider}/{model}", "enabled", False, overrides_path)

    bucket_entry["vanished"] = [m for m in vanished if m != model]
    data[provider] = bucket_entry
    _save(state_dir, data)


def reject_vanished(state_dir: str, provider: str, model: str) -> None:
    data = _load(state_dir)
    bucket_entry = data.get(provider, {})
    bucket_entry["vanished"] = [
        m for m in (bucket_entry.get("vanished") or []) if m != model
    ]
    data[provider] = bucket_entry
    _save(state_dir, data)


def accept_changed(state_dir: str, provider: str, model: str, field: str,
                   overrides_path: Optional[Path] = None) -> None:
    data = _load(state_dir)
    bucket_entry = data.get(provider, {})
    changed = bucket_entry.get("changed") or []
    entry = next(
        (c for c in changed if c.get("model") == model and c.get("field") == field), None)
    if entry is None:
        raise PendingActionError(f"{provider}/{model}/{field} is not a pending change")

    ov.check_fields("models", {field: entry["new"]})
    ov.set_override("models", f"{provider}/{model}", field, entry["new"], overrides_path)

    bucket_entry["changed"] = [c for c in changed if c is not entry]
    data[provider] = bucket_entry
    _save(state_dir, data)


def reject_changed(state_dir: str, provider: str, model: str, field: str) -> None:
    data = _load(state_dir)
    bucket_entry = data.get(provider, {})
    bucket_entry["changed"] = [
        c for c in (bucket_entry.get("changed") or [])
        if not (c.get("model") == model and c.get("field") == field)
    ]
    data[provider] = bucket_entry
    _save(state_dir, data)
