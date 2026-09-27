"""What was said in each request: prompt, reply and reasoning, by request id.

Kept apart from traces.jsonl on purpose (grill-decisions.md §7). A trace is
scrubbed metadata kept for 30 days; this is the conversation itself, kept
for `save_conversations_days` (7 by default) and switched off entirely by
`save_conversations: false`. Only the keys flexrouter itself holds are
masked (redact.mask_known_secrets, an exact match) - anything heuristic
would mangle the very text the owner saves this to read.

One file per UTC day under state/conversations/, so expiry is deleting old
files, never rewriting a live one.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from flexrouter.redact import mask_known_secrets

# About 20 KB per message (§7). Cut on characters, which is close enough.
MAX_CHARS = 20_000
CUT_NOTE = "\n… [cut: longer than 20 KB]"


def _cut(text: str) -> str:
    text = mask_known_secrets(text or "")
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + CUT_NOTE


def _text_of(content) -> str:
    """A message's content as text. Multi-part content (vision) keeps its
    text parts and names what else was there."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if not isinstance(p, dict):
                continue
            if p.get("type") == "text":
                parts.append(p.get("text", ""))
            else:
                parts.append(f"[{p.get('type', 'attachment')}]")
        return "\n".join(parts)
    return "" if content is None else str(content)


class ConversationStore:
    def __init__(self, state_dir: str, enabled: bool = True, days: int = 7) -> None:
        self._dir = Path(state_dir) / "conversations"
        self.enabled = enabled
        self.days = max(1, int(days))

    def save(self, request_id: str, messages: list[dict], reply: str = "",
             reasoning: str = "", now: Optional[datetime] = None) -> None:
        if not self.enabled:
            return
        now = now or datetime.now(timezone.utc)
        entry = {
            "id": request_id,
            "at": now.isoformat(timespec="milliseconds"),
            "messages": [{"role": m.get("role", ""), "content": _cut(_text_of(m.get("content")))}
                         for m in messages or [] if isinstance(m, dict)],
            "reply": _cut(reply),
            "reasoning": _cut(reasoning),
        }
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with (self._dir / f"{now.date().isoformat()}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.compact(now)
        except OSError:
            # Losing a saved conversation must never fail the request.
            return

    def compact(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=self.days)).date()
        if not self._dir.exists():
            return
        for f in self._dir.glob("*.jsonl"):
            try:
                day = date.fromisoformat(f.stem)
            except ValueError:
                continue
            if day < cutoff:
                f.unlink(missing_ok=True)

    def get(self, request_id: str) -> Optional[dict]:
        """The saved conversation for one request, or None. Newest day first."""
        if not self._dir.exists():
            return None
        for f in sorted(self._dir.glob("*.jsonl"), reverse=True):
            try:
                lines = f.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                if request_id not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("id") == request_id:
                    return entry
        return None
