"""Saved Chat conversations: one small JSON file each, under state/chats/.

This is the Chat page's history. It is the owner's own text, so only the
keys flexrouter holds are masked (redact.mask_known_secrets, an exact match),
same rule as conversations.py. The oldest chats are dropped past MAX_CHATS.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flexrouter import home
from flexrouter.redact import mask_known_secrets
from flexrouter.store import read_json, write_json

MAX_CHATS = 200
MAX_MESSAGES = 500
_ID = re.compile(r"^[a-z0-9]{6,32}$")


def _dir(root: Optional[Path] = None) -> Path:
    return Path(root) if root else home.state_dir() / "chats"


def new_id() -> str:
    return secrets.token_hex(6)


def valid_id(chat_id: str) -> bool:
    return bool(_ID.match(chat_id or ""))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def title_from(messages: list) -> str:
    for m in messages:
        if m.get("role") == "user" and (m.get("content") or "").strip():
            text = " ".join(m["content"].split())
            return text if len(text) <= 40 else text[:39] + "…"
    return "New chat"


def get(chat_id: str, root: Optional[Path] = None) -> Optional[dict]:
    if not valid_id(chat_id):
        return None
    data = read_json(_dir(root) / f"{chat_id}.json", default=None)
    return data or None


def save(chat_id: str, data: dict, root: Optional[Path] = None) -> dict:
    if not valid_id(chat_id):
        raise ValueError("bad chat id")
    old = get(chat_id, root) or {}
    messages = []
    for m in (data.get("messages") or [])[-MAX_MESSAGES:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        item = {"role": m["role"], "content": mask_known_secrets(str(m.get("content") or ""))}
        for key in ("answered_by", "request_id", "reasoning"):
            if m.get(key):
                item[key] = mask_known_secrets(str(m[key]))
        if isinstance(m.get("compare"), dict):
            item["compare"] = {str(k): mask_known_secrets(str(v))
                               for k, v in m["compare"].items()}
        messages.append(item)
    title = (str(data.get("title") or "").strip() or old.get("title") or "")
    if not title or (title == "New chat" and messages):
        title = title_from(messages)
    doc = {
        "id": chat_id,
        "title": title[:80],
        "created": old.get("created") or _now(),
        "updated": _now(),
        "target": str(data.get("target") or old.get("target") or "auto"),
        "system": str(data.get("system") or ""),
        "messages": messages,
    }
    write_json(_dir(root) / f"{chat_id}.json", doc)
    _prune(root)
    return doc


def rename(chat_id: str, title: str, root: Optional[Path] = None) -> bool:
    doc = get(chat_id, root)
    title = (title or "").strip()[:80]
    if not doc or not title:
        return False
    doc["title"] = title
    write_json(_dir(root) / f"{chat_id}.json", doc)
    return True


def delete(chat_id: str, root: Optional[Path] = None) -> bool:
    if not valid_id(chat_id):
        return False
    path = _dir(root) / f"{chat_id}.json"
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def listing(query: str = "", root: Optional[Path] = None) -> list[dict]:
    """Newest first: id, title, updated, message count. `query` matches the
    title or any message text, case-insensitively."""
    folder = _dir(root)
    if not folder.exists():
        return []
    q = (query or "").strip().lower()
    rows = []
    for path in folder.glob("*.json"):
        doc = read_json(path, default=None)
        if not doc or not valid_id(doc.get("id", "")):
            continue
        if q and q not in doc.get("title", "").lower() and not any(
                q in (m.get("content") or "").lower() for m in doc.get("messages", [])):
            continue
        rows.append({"id": doc["id"], "title": doc.get("title") or "New chat",
                     "updated": doc.get("updated", ""), "n": len(doc.get("messages", []))})
    rows.sort(key=lambda r: r["updated"], reverse=True)
    return rows


def _prune(root: Optional[Path] = None) -> None:
    rows = listing(root=root)
    for row in rows[MAX_CHATS:]:
        delete(row["id"], root)
