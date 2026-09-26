"""One status per model (grill-decisions.md §3).

Replaces five mechanisms that each held a piece of the same fact: the penalty
box (doubling backoff), quarantine (24h), provider quarantine, the 7-day
auto-bench, and the "no speed data" skip. A model is now exactly one of:

    ready       will be used
    busy        rate limit or overload; clears on its own (provider's
                retry-after, else 60s, never doubling)
    struggling  fails in weird ways (empty replies, bad output); ~1 hour
    needs_you   wrong ID / not on plan / no balance / key rejected; no timer
    off         turned off by the owner (reported by the facts layer: a
                disabled model is dropped from the live config entirely)

Each carries one plain `reason` sentence, an `until` (None when it doesn't
clear on its own), and at most one `action`.

Keys have their own status in key_state.py, using the same words.

Persisted to state/status.json. On first start the old quarantine.json and
penalties.json are migrated and set aside, so nothing is stranded.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from flexrouter.store import read_json, write_json

READY = "ready"
BUSY = "busy"
STRUGGLING = "struggling"
NEEDS_YOU = "needs_you"
OFF = "off"

STATUSES = (READY, BUSY, STRUGGLING, NEEDS_YOU, OFF)

BUSY_DEFAULT_SECONDS = 60
STRUGGLING_SECONDS = 60 * 60

# Model slot for a provider-wide status. No real model id is "*".
PROVIDER_WILDCARD = "*"

# Actions a row can offer. "use:<model>" is the did-you-mean fix (Session 7).
TRY_NOW = "try_now"
RETRY = "retry"
REMOVE = "remove"
TURN_ON = "turn_on"
REPLACE_KEY = "replace_key"


@dataclass
class Status:
    value: str = READY
    reason: str = ""
    until: Optional[float] = None
    action: Optional[str] = None
    # A machine word for what happened: rate_limited, overloaded,
    # unreachable, empty_replies, gone, not_on_plan, balance_empty, bad_key,
    # no_provider, off. Pages group and word rows by it.
    kind: str = ""
    since: Optional[float] = None
    status_code: Optional[int] = None
    # The provider's own text, unmangled, for the click-through detail.
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def seconds_left(self, now: Optional[float] = None) -> Optional[int]:
        if self.until is None:
            return None
        now = time.time() if now is None else now
        return max(0, math.ceil(self.until - now))


def provider_label(provider: str) -> str:
    """"Google", not "googleai": the preset's label, first word."""
    try:
        from flexrouter import presets
        preset = presets.shipped().get(provider)
    except Exception:  # noqa: BLE001 - a label is never worth a crash
        preset = None
    if preset and preset.label:
        return preset.label.split()[0]
    return provider


# ---- What a failure means ---------------------------------------------------

_RETRY_PATTERNS = (
    re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"'),
    re.compile(r"retry (?:in|after) (\d+(?:\.\d+)?)\s*s", re.I),
)
_LIMIT_ZERO = (
    re.compile(r"\blimit:\s*0\b"),
    re.compile(r'"quotaValue"\s*:\s*"0"'),
)


def parse_retry_after(body, headers: Optional[Mapping] = None) -> Optional[int]:
    """Seconds until the provider says to come back, or None if it didn't say.

    Reads the Retry-After header, the x-ratelimit-reset-* headers, and the
    body (Google puts `"retryDelay": "40s"` and "Please retry in 40.5s" there).
    """
    from flexrouter.headers import _parse_duration_ms

    norm = {str(k).lower(): v for k, v in dict(headers or {}).items()}
    candidates: list[float] = []
    ra = norm.get("retry-after")
    if ra is not None:
        try:
            candidates.append(float(ra))
        except ValueError:
            pass
    for name in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        ms = _parse_duration_ms(norm.get(name))
        if ms is not None:
            candidates.append(ms / 1000)
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    text = body or ""
    for pattern in _RETRY_PATTERNS:
        m = pattern.search(text)
        if m:
            candidates.append(float(m.group(1)))
            break
    positive = [c for c in candidates if c > 0]
    return math.ceil(max(positive)) if positive else None


def busy_seconds(retry_after: Optional[float]) -> int:
    """The provider's own figure when it gave one, else 60s. Never doubles."""
    if retry_after and retry_after > 0:
        return int(math.ceil(retry_after))
    return BUSY_DEFAULT_SECONDS


def is_limit_zero(body) -> bool:
    """A 429 whose quota is zero: waiting will never help ("Not on your plan")."""
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    return any(p.search(body or "") for p in _LIMIT_ZERO)


@dataclass
class Failure:
    value: str
    kind: str
    reason: str
    action: Optional[str] = None


def classify_failure(status_code: Optional[int], provider: str, body="") -> Failure:
    """What a provider failure makes the model, by status code alone.

    §2/§3: busy things (429, 5xx, no answer at all) are Busy and clear on
    their own; broken things (402/403/404/410, a 429 with a zero quota) are
    Needs you, straight away and with no timer. 400s are not decided here:
    the failover policy returns them to the caller, and Session 7's error
    brain decides the ones it can.
    """
    name = provider_label(provider)
    if status_code == 429:
        if is_limit_zero(body):
            return Failure(NEEDS_YOU, "not_on_plan",
                           f"Not on your {name} plan (its quota is 0).", RETRY)
        return Failure(BUSY, "rate_limited", "Too many requests")
    if status_code == 402:
        return Failure(NEEDS_YOU, "balance_empty", f"Your {name} balance is empty.", RETRY)
    if status_code == 403:
        return Failure(NEEDS_YOU, "not_on_plan", f"Not on your {name} plan.", RETRY)
    if status_code in (404, 410):
        return Failure(NEEDS_YOU, "gone", f"{name} doesn't know this name.", REMOVE)
    if status_code is None:
        return Failure(BUSY, "unreachable", f"Couldn't reach {name}")
    return Failure(BUSY, "overloaded", f"{name} overloaded")


# ---- The store --------------------------------------------------------------

class StatusStore:
    """Per-(provider, model) status, persisted when given a state_dir.

    Emits 'busy' / 'struggling' / 'needs_you' / 'recovered' through
    `on_event(provider, model, event_type, seconds)`.
    """

    def __init__(self, state_dir: Optional[str] = None,
                 on_event: Optional[Callable[[str, str, str, int], None]] = None) -> None:
        self._on_event = on_event
        self._dir = Path(state_dir) if state_dir else None
        self._path = self._dir / "status.json" if self._dir else None
        self._entries: dict[str, Status] = {}
        self._load()

    # -- persistence --

    def _load(self) -> None:
        if not self._path:
            return
        if self._path.exists():
            raw = read_json(self._path, default={})
            for k, v in (raw if isinstance(raw, dict) else {}).items():
                try:
                    self._entries[k] = Status(**v)
                except TypeError:
                    continue
        self._migrate_old_files()

    def _migrate_old_files(self) -> None:
        """Carry the old quarantine.json / penalties.json over, once.

        - a quarantine (404/402/403, or a provider whose keys all failed)
          becomes Needs you, with no timer;
        - an "auto-benched" quarantine is dropped: those were mostly Google's
          own overload, which is only ever Busy now;
        - a penalty becomes an ordinary Busy, capped at 60s from now (the
          doubling that made them long is gone).
        The old files are renamed, so this runs once and nothing is stranded.
        """
        assert self._dir is not None
        quarantine = self._dir / "quarantine.json"
        penalties = self._dir / "penalties.json"
        if not quarantine.exists() and not penalties.exists():
            return
        now = time.time()
        changed = False

        q = read_json(quarantine, default={}) if quarantine.exists() else {}
        for key, entry in (q if isinstance(q, dict) else {}).items():
            if not isinstance(entry, dict) or key in self._entries:
                continue
            reason = str(entry.get("reason", ""))
            if reason.startswith("auto-benched"):
                continue
            provider, _, model = key.partition("/")
            if model == PROVIDER_WILDCARD:
                self._entries[key] = Status(
                    NEEDS_YOU, f"{provider_label(provider)} rejected every key.",
                    None, REPLACE_KEY, "bad_key", now, 401, reason)
            else:
                m = re.match(r"\s*(\d{3})\b", reason)
                code = int(m.group(1)) if m else None
                f = classify_failure(code, provider, reason)
                if f.value != NEEDS_YOU:
                    f = Failure(NEEDS_YOU, "broken", "Kept failing before v2.3.", RETRY)
                self._entries[key] = Status(
                    NEEDS_YOU, f.reason, None, f.action, f.kind, now, code, reason)
            changed = True

        p = read_json(penalties, default={}) if penalties.exists() else {}
        for key, entry in (p if isinstance(p, dict) else {}).items():
            if not isinstance(entry, dict) or key in self._entries:
                continue
            try:
                until = float(entry.get("until", 0))
            except (TypeError, ValueError):
                continue
            if until <= now:
                continue
            self._entries[key] = Status(
                BUSY, "Backing off after a failure", min(until, now + BUSY_DEFAULT_SECONDS),
                None, "rate_limited", now)
            changed = True

        for old in (quarantine, penalties):
            if old.exists():
                try:
                    old.replace(old.with_name(old.name + ".migrated-v2.3"))
                except OSError:
                    pass
        if changed:
            self._save()
        elif not self._path.exists():
            self._save()

    def _save(self) -> None:
        if not self._path:
            return
        write_json(self._path, {k: v.to_dict() for k, v in self._entries.items()})

    def _emit(self, provider: str, model: str, event: str, secs: int = 0) -> None:
        if self._on_event:
            self._on_event(provider, model, event, secs)

    @staticmethod
    def _key(provider: str, model: str) -> str:
        return f"{provider}/{model}"

    # -- reads --

    def _live(self, key: str, now: float) -> Optional[Status]:
        """The entry if it still holds. An expired Busy/Struggling reads as
        ready without writing: rendering a page must never touch disk."""
        s = self._entries.get(key)
        if s is None:
            return None
        if s.until is not None and now >= s.until:
            return None
        return s

    def get(self, provider: str, model: str, now: Optional[float] = None) -> Status:
        now = time.time() if now is None else now
        if model != PROVIDER_WILDCARD:
            wide = self._live(self._key(provider, PROVIDER_WILDCARD), now)
            if wide is not None and wide.value == NEEDS_YOU:
                return wide
        return self._live(self._key(provider, model), now) or Status()

    def provider_status(self, provider: str, now: Optional[float] = None) -> Status:
        now = time.time() if now is None else now
        return self._live(self._key(provider, PROVIDER_WILDCARD), now) or Status()

    def is_usable(self, provider: str, model: str, now: Optional[float] = None) -> bool:
        return self.get(provider, model, now).value == READY

    def all(self, now: Optional[float] = None) -> dict[str, Status]:
        """Every entry that still holds, keyed "provider/model"."""
        now = time.time() if now is None else now
        return {k: s for k in list(self._entries) if (s := self._live(k, now)) is not None}

    # -- writes --

    def _set(self, provider: str, model: str, status: Status, event: str) -> None:
        key = self._key(provider, model)
        prev = self._entries.get(key)
        # Keep "since" across repeats of the same trouble, so a page can say
        # how long it has been going on rather than when it last happened.
        if prev is not None and prev.value == status.value and prev.kind == status.kind \
                and prev.since is not None:
            status.since = prev.since
        self._entries[key] = status
        self._save()
        secs = 0 if status.until is None else max(0, int(status.until - time.time()))
        self._emit(provider, model, event, secs)

    def set_busy(self, provider: str, model: str, seconds: float, reason: str, *,
                 kind: str = "rate_limited", status_code: Optional[int] = None,
                 detail: str = "", now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        current = self._live(self._key(provider, model), now)
        if current is not None and current.value == NEEDS_YOU:
            return  # a broken model stays broken; a 429 on the side changes nothing
        self._set(provider, model, Status(
            BUSY, reason, now + max(1.0, float(seconds)), None, kind, now, status_code, detail),
            "busy")

    def set_struggling(self, provider: str, model: str, reason: str, *,
                       kind: str = "empty_replies", seconds: float = STRUGGLING_SECONDS,
                       detail: str = "", now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        current = self._live(self._key(provider, model), now)
        if current is not None and current.value == NEEDS_YOU:
            return
        self._set(provider, model, Status(
            STRUGGLING, reason, now + seconds, TRY_NOW, kind, now, None, detail), "struggling")

    def set_needs_you(self, provider: str, model: str, reason: str, *, kind: str,
                      action: Optional[str], status_code: Optional[int] = None,
                      detail: str = "", now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self._set(provider, model, Status(
            NEEDS_YOU, reason, None, action, kind, now, status_code, detail), "needs_you")

    def set_provider_needs_you(self, provider: str, reason: str, *, kind: str = "bad_key",
                               action: Optional[str] = REPLACE_KEY,
                               status_code: Optional[int] = None, detail: str = "",
                               now: Optional[float] = None) -> None:
        self.set_needs_you(provider, PROVIDER_WILDCARD, reason, kind=kind, action=action,
                           status_code=status_code, detail=detail, now=now)

    def record_failure(self, provider: str, model: str, status_code: Optional[int],
                       body="", *, retry_after: Optional[float] = None,
                       detail: str = "") -> Failure:
        """Apply classify_failure() and return what it decided."""
        f = classify_failure(status_code, provider, body)
        if f.value == NEEDS_YOU:
            self.set_needs_you(provider, model, f.reason, kind=f.kind, action=f.action,
                               status_code=status_code, detail=detail)
        else:
            secs = busy_seconds(retry_after)
            reason = f"{f.reason} · back in {secs}s" if f.kind != "unreachable" else f.reason
            self.set_busy(provider, model, secs, f.reason, kind=f.kind,
                          status_code=status_code, detail=detail)
            f = Failure(f.value, f.kind, reason, f.action)
        return f

    def clear(self, provider: str, model: str) -> None:
        if self._entries.pop(self._key(provider, model), None) is not None:
            self._save()
            self._emit(provider, model, "recovered", 0)

    def clear_provider(self, provider: str) -> None:
        self.clear(provider, PROVIDER_WILDCARD)

    def forget(self, match: Callable[[str], bool]) -> None:
        """Drop every entry whose key `match` accepts (model_reset)."""
        gone = [k for k in self._entries if match(k)]
        for k in gone:
            del self._entries[k]
        if gone:
            self._save()
