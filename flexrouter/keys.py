"""Credentials. The only place flexrouter stores a secret it was given.

Secrets never leave this module in full except to the provider client: the
HTTP API and the CLI both show mask() output only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from flexrouter import home
from flexrouter.store import harden, read_json, write_json


@dataclass
class KeyRecord:
    id: str
    secret: str
    label: str = ""
    added_at: str = ""
    weight: int = 1
    allow_models: list[str] = field(default_factory=lambda: ["*"])
    enabled: bool = True
    source: str = "keys"  # keys | env | inline

    def public(self) -> dict:
        """Everything about this key except the secret itself."""
        data = asdict(self)
        data.pop("secret")
        data["masked"] = mask(self.secret)
        return data


def mask(secret: str) -> str:
    """The only form of a secret that may reach a human or an HTTP response.

    Below four characters there is nothing left to show without showing the
    whole thing, so nothing is shown.
    """
    if not secret:
        return ""
    return f"…{secret[-4:]}" if len(secret) >= 4 else "…"


def allows(record: KeyRecord, model_id: str) -> bool:
    """Whether this key may be used for this model (fnmatch globs)."""
    patterns = record.allow_models or ["*"]
    return any(fnmatch(model_id, p) for p in patterns)


def _path(path: Path | str | None) -> Path:
    return Path(path) if path else home.keys_path()


def load_keys(path: Path | str | None = None) -> dict[str, list[KeyRecord]]:
    raw = read_json(_path(path), default={})
    out: dict[str, list[KeyRecord]] = {}
    for provider, entries in (raw or {}).items():
        records = []
        for e in entries or []:
            records.append(KeyRecord(
                id=e.get("id", ""),
                secret=e.get("secret", ""),
                label=e.get("label", ""),
                added_at=e.get("added_at", ""),
                weight=int(e.get("weight", 1)),
                allow_models=list(e.get("allow_models") or ["*"]),
                enabled=bool(e.get("enabled", True)),
                source=e.get("source", "keys"),
            ))
        out[provider] = records
    return out


def save_keys(mapping: dict[str, list[KeyRecord]],
              path: Path | str | None = None) -> None:
    target = _path(path)
    raw = {p: [asdict(r) for r in records] for p, records in mapping.items()}
    write_json(target, raw)
    harden(target)


def _next_id(existing: list[KeyRecord], provider: str) -> str:
    """Next `<provider>-<n>` id, collision-proof against any add/remove
    history. Derived from the highest existing numeric suffix for this
    provider, not the surviving count — a record whose id doesn't match
    the `<provider>-<n>` shape (e.g. hand-imported) is simply ignored."""
    prefix = f"{provider}-"
    highest = 0
    for r in existing:
        if r.id.startswith(prefix):
            suffix = r.id[len(prefix):]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1}"


def add_key(provider: str, secret: str, label: str = "",
            path: Path | str | None = None) -> KeyRecord:
    mapping = load_keys(path)
    existing = mapping.setdefault(provider, [])
    record = KeyRecord(
        id=_next_id(existing, provider),
        secret=secret,
        label=label,
        added_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    existing.append(record)
    save_keys(mapping, path)
    return record


def remove_key(provider: str, key_id: str, path: Path | str | None = None) -> bool:
    mapping = load_keys(path)
    records = mapping.get(provider, [])
    kept = [r for r in records if r.id != key_id]
    if len(kept) == len(records):
        return False
    mapping[provider] = kept
    save_keys(mapping, path)
    return True
