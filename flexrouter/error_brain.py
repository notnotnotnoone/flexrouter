"""state/error_brain.json: what an unfamiliar provider error means, learned
once and remembered forever (spec §4a).

Built-in rules run first and never reach a Decider: unambiguous status
codes and a small set of known substrings. Only genuinely unrecognized text
is fingerprinted, checked against what's already been learned, and — on a
real miss — handed to the configured Decider (flexrouter/decider.py).

This module classifies; it does not decide what the router does about the
classification. The router's existing quarantine/penalize/bench logic
(built across Stages 2-4, already reviewed) is untouched — see ADR 0012.
"""
from __future__ import annotations

import json
import re
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flexrouter.decider import Decider, ErrorVerdict
from flexrouter.redact import scrub, scrub_body
from flexrouter.store import harden, read_json, write_json

_PERMANENT_ISH = {404: "model_gone", 410: "model_gone"}
_STATUS_RULES = {
    401: "bad_key", 403: "bad_key",
    402: "needs_payment",
    404: "model_gone", 410: "model_gone",
    429: "too_fast",
}

# Codes providers overload. A 429 means "slow down" most of the time and "you
# are out of credits" the rest of the time; 400 and 403 carry a real cause in
# the body just as often. Answering those at confidence 1.0 ends the matter and
# the router retries into a wall. For these three only, the rule becomes a
# prior the classifier may overturn -- and only when a classifier is actually
# configured, so an install without one behaves exactly as it always did.
_OVERLOADED_STATUSES = frozenset({400, 403, 429})
_RULE_PRIOR_CONFIDENCE = 0.6

_TOO_LONG_SUBSTRINGS = (
    "context length", "maximum context", "too long", "context_length_exceeded",
)


def classify_by_rule(text: str, status: Optional[int]) -> Optional[ErrorVerdict]:
    """The instant, unambiguous cases. None means "genuinely unrecognized"."""
    if status is not None:
        verdict = _STATUS_RULES.get(status)
        if verdict:
            return ErrorVerdict(verdict=verdict, source="rule", confidence=1.0)
        if status == 400:
            return ErrorVerdict(verdict="bad_request", source="rule", confidence=1.0)
        if status >= 500:
            return ErrorVerdict(verdict="their_end_temporary", source="rule", confidence=1.0)

    lowered = text.lower()
    if any(s in lowered for s in _TOO_LONG_SUBSTRINGS):
        return ErrorVerdict(verdict="message_too_long", source="rule", confidence=0.9)

    return None


_DIGITS = re.compile(r"\d+")
_WHITESPACE = re.compile(r"\s+")


def fingerprint(text: str) -> str:
    normalized = _DIGITS.sub("#", text.lower())
    normalized = _WHITESPACE.sub(" ", normalized).strip()
    return normalized[:200]


@dataclass
class ErrorBrainEntry:
    verdict: str
    source: str
    confidence: float
    seen: int
    first_at: str
    last_at: str
    sample: str
    flagged_for_review: bool = False
    # The latest occurrence in full: its HTTP status and the provider's whole
    # response body, scrubbed but never clipped.
    status: Optional[int] = None
    raw: str = ""
    # "provider/model" -> how often it hit this error.
    where: dict = field(default_factory=dict)
    # The last RECENT_KEPT occurrences, newest first.
    recent: list = field(default_factory=list)
    # What the classifier said the last time it was asked about this error.
    decision: Optional[dict] = None


RECENT_KEPT = 20
DECIDER_LOG = "decider_calls.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ErrorBrain:
    def __init__(self, state_dir: str, decider: Decider,
                confidence_threshold: float = 0.80,
                contested_statuses: tuple[int, ...] = tuple(_OVERLOADED_STATUSES),
                rule_prior_confidence: float = _RULE_PRIOR_CONFIDENCE) -> None:
        self._path = Path(state_dir) / "error_brain.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._decider = decider
        # Classification is offloaded to a worker thread so a slow classifier
        # cannot stall the event loop, which means two requests can now reach
        # a novel error at the same moment. Everything that touches _entries or
        # writes the file goes through this. Reentrant because _record saves.
        self._lock = threading.RLock()
        self._threshold = confidence_threshold
        # Per-instance rather than module constants: both are settings now.
        self.contested_statuses = tuple(contested_statuses)
        self.rule_prior_confidence = rule_prior_confidence
        self._entries: dict[str, ErrorBrainEntry] = {
            k: ErrorBrainEntry(**v) for k, v in read_json(self._path, default={}).items()
        }

    @property
    def confidence_threshold(self) -> float:
        return self._threshold

    def _save(self) -> None:
        with self._lock:
            snapshot = {k: asdict(v) for k, v in self._entries.items()}
            write_json(self._path, snapshot)
            harden(self._path)

    def classify(self, text: str, status: Optional[int], now: Optional[str] = None, *,
                 provider: Optional[str] = None, model: Optional[str] = None,
                 trace_id: Optional[str] = None, body: Optional[str] = None) -> ErrorVerdict:
        clean = scrub(text or "")
        now = now or _now_iso()
        seen = {"at": now, "provider": provider, "model": model, "status": status,
                "trace_id": trace_id, "raw": scrub_body(body) if body else ""}

        rule = classify_by_rule(clean, status)
        if rule is not None and not self._is_contestable(status):
            self._record(fingerprint(clean), rule, clean, now, seen)
            return rule

        fp = fingerprint(clean)
        with self._lock:
            existing = self._entries.get(fp)
            if existing is not None:
                existing.seen += 1
                existing.last_at = now
                self._note(existing, seen)
                self._save()
                return ErrorVerdict(existing.verdict, existing.source, existing.confidence)

        asked = self._decider.classify_error(clean, status)
        verdict = asked
        if rule is not None and asked.confidence <= self.rule_prior_confidence:
            # The classifier is no surer than the prior, so the rule stands --
            # at its own full confidence, not the prior's. The prior exists to
            # decide who wins, not to weaken the answer that did.
            verdict = rule
        decision = None
        if asked.insight is not None:
            decision = {**_scrubbed(asked.insight), "at": now, "verdict": asked.verdict,
                        "confidence": asked.confidence,
                        "rule_said": rule.verdict if rule else None,
                        "used": verdict.verdict,
                        "overturned_rule": bool(rule and verdict is asked
                                                and asked.verdict != rule.verdict)}
            self._log_decider_call({**decision, "error_text": clean, "status": status,
                                    "error_provider": provider, "error_model": model,
                                    "trace_id": trace_id})
        self._record(fp, verdict, clean, now, seen, decision)
        return verdict

    def _log_decider_call(self, row: dict) -> None:
        with self._lock:
            path = self._path.parent / DECIDER_LOG
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            harden(path)

    def decider_calls(self, limit: int = 200) -> list[dict]:
        """The most recent classifier calls, newest first."""
        path = self._path.parent / DECIDER_LOG
        if not path.exists():
            return []
        with path.open(encoding="utf-8", errors="replace") as f:
            lines = deque(f, maxlen=limit)
        rows = []
        for line in reversed(lines):
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows

    @staticmethod
    def _note(entry: ErrorBrainEntry, seen: dict) -> None:
        where = f"{seen['provider']}/{seen['model']}" if seen.get("provider") else None
        if where:
            entry.where[where] = entry.where.get(where, 0) + 1
        if seen.get("status") is not None:
            entry.status = seen["status"]
        if seen.get("raw"):
            entry.raw = seen["raw"]
        occurrence = {k: seen[k] for k in ("at", "provider", "model", "status", "trace_id")}
        entry.recent = [occurrence, *entry.recent][:RECENT_KEPT]

    def _is_contestable(self, status: Optional[int]) -> bool:
        """Whether a rule's answer for this status is open to challenge.

        False unless a classifier is configured: NullDecider always answers
        "unknown" at zero confidence, so contesting a rule with it could only
        ever waste a lookup and drag a settled verdict's stored confidence
        down past the review threshold, filling the review queue on an install
        that never asked for a classifier.
        """
        if status not in self.contested_statuses:
            return False
        return getattr(self._decider, "configured", True)

    def correct(self, fp: str, verdict: str) -> None:
        """The owner's own answer for one learned error.

        Stored as `source="manual"` at full confidence, which `_record`
        already refuses to overwrite: once corrected by hand, no rule or
        classifier ever changes this entry again - only its counters move.
        """
        from flexrouter.decider import VERDICTS
        if verdict not in VERDICTS:
            raise ValueError(f"{verdict!r} is not a verdict; choose one of {', '.join(VERDICTS)}")
        with self._lock:
            entry = self._entries[fp]  # KeyError for an unknown entry, on purpose
            entry.verdict = verdict
            entry.source = "manual"
            entry.confidence = 1.0
            entry.flagged_for_review = False
            self._save()

    def _record(self, fp: str, verdict: ErrorVerdict, sample: str, now: str,
                seen: Optional[dict] = None, decision: Optional[dict] = None) -> None:
      with self._lock:
        existing = self._entries.get(fp)
        if existing is not None and existing.source == "manual":
            # Manual entries are never overwritten — only bookkeeping moves.
            existing.seen += 1
            existing.last_at = now
            if seen:
                self._note(existing, seen)
            if decision:
                existing.decision = decision
            self._save()
            return
        if existing is not None:
            existing.seen += 1
            existing.last_at = now
            existing.verdict = verdict.verdict
            existing.source = verdict.source
            existing.confidence = verdict.confidence
            existing.flagged_for_review = verdict.confidence < self._threshold
            self._entries[fp] = existing
        else:
            existing = self._entries[fp] = ErrorBrainEntry(
                verdict=verdict.verdict, source=verdict.source, confidence=verdict.confidence,
                seen=1, first_at=now, last_at=now, sample=sample,
                flagged_for_review=verdict.confidence < self._threshold,
            )
        if seen:
            self._note(existing, seen)
        if decision:
            existing.decision = decision
        self._save()


def _scrubbed(insight: dict) -> dict:
    """A classifier's insight with its free-text fields scrubbed for storage."""
    return {k: scrub_body(v) if isinstance(v, str) and k in ("error", "reply") else v
            for k, v in insight.items()}
