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

import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flexrouter.decider import Decider, ErrorVerdict
from flexrouter.redact import scrub
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

    def classify(self, text: str, status: Optional[int], now: Optional[str] = None) -> ErrorVerdict:
        clean = scrub(text or "")
        now = now or _now_iso()

        rule = classify_by_rule(clean, status)
        if rule is not None and not self._is_contestable(status):
            self._record(fingerprint(clean), rule, clean, now)
            return rule

        fp = fingerprint(clean)
        with self._lock:
            existing = self._entries.get(fp)
            if existing is not None:
                existing.seen += 1
                existing.last_at = now
                self._save()
                return ErrorVerdict(existing.verdict, existing.source, existing.confidence)

        verdict = self._decider.classify_error(clean, status)
        if rule is not None and verdict.confidence <= self.rule_prior_confidence:
            # The classifier is no surer than the prior, so the rule stands --
            # at its own full confidence, not the prior's. The prior exists to
            # decide who wins, not to weaken the answer that did.
            verdict = rule
        self._record(fp, verdict, clean, now)
        return verdict

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

    def _record(self, fp: str, verdict: ErrorVerdict, sample: str, now: str) -> None:
      with self._lock:
        existing = self._entries.get(fp)
        if existing is not None and existing.source == "manual":
            # Manual entries are never overwritten — only bookkeeping moves.
            existing.seen += 1
            existing.last_at = now
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
            self._entries[fp] = ErrorBrainEntry(
                verdict=verdict.verdict, source=verdict.source, confidence=verdict.confidence,
                seen=1, first_at=now, last_at=now, sample=sample,
                flagged_for_review=verdict.confidence < self._threshold,
            )
        self._save()
