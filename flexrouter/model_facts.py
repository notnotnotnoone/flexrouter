"""state/model_facts.json: what is actually known about each model's
capabilities, learned from real traffic (spec §4b).

Deliberately separate from engine.py's static ModelConfig.vision — that is
a fact the owner declared in settings and engine.py already acts on today.
This store holds *learned* facts, tagged with where each one came from
(published/observed/guessed/manual), through a lenient state machine that
never jumps straight from "works fine" to "doesn't work": one contradicting
failure only ever casts doubt, never certainty, and any success is
stronger evidence than any failure (ADR 0013).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from flexrouter.store import harden, read_json, write_json


@dataclass
class CapabilityFact:
    status: str = "doubted"  # yes | doubted | no
    source: str = "guessed"  # published | observed | guessed | manual
    confidence: float = 1.0
    strikes: int = 0
    last_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None
    evidence: list[str] = field(default_factory=list)


@dataclass
class SimpleFact:
    value: object
    source: str
    confidence: float = 1.0


@dataclass
class ModelFacts:
    vision: Optional[CapabilityFact] = None
    tools: Optional[CapabilityFact] = None
    reasoning: Optional[CapabilityFact] = None
    context: Optional[SimpleFact] = None
    size_class: Optional[SimpleFact] = None
    provisional_score: Optional[dict] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def record_success(fact: CapabilityFact, now: str) -> CapabilityFact:
    if fact.source == "manual":
        return fact
    return replace(fact, status="yes", strikes=0, last_success_at=now)


def record_contradicting_failure(fact: CapabilityFact, now: str, trace_id: str) -> CapabilityFact:
    if fact.source == "manual":
        return fact
    strikes = fact.strikes + 1
    required = 5 if fact.source == "published" else 3
    status = "no" if strikes >= required else "doubted"
    return replace(fact, status=status, strikes=strikes, last_failure_at=now,
                  evidence=[*fact.evidence, trace_id])


def check_staleness(fact: CapabilityFact, now: str, stale_after_days: int = 30) -> CapabilityFact:
    if fact.status != "no" or fact.source != "observed" or fact.last_failure_at is None:
        return fact
    if _parse(now) - _parse(fact.last_failure_at) > timedelta(days=stale_after_days):
        return replace(fact, status="doubted")
    return fact


_CAPABILITY_FIELDS = {"vision", "tools", "reasoning"}


class ModelFactsStore:
    def __init__(self, state_dir: str) -> None:
        self._path = Path(state_dir) / "model_facts.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._facts: dict[tuple[str, str], ModelFacts] = {}
        for key, raw in read_json(self._path, default={}).items():
            provider, _, model = key.partition("/")
            kwargs = {}
            for cap in _CAPABILITY_FIELDS:
                if raw.get(cap):
                    kwargs[cap] = CapabilityFact(**raw[cap])
            for simple in ("context", "size_class"):
                if raw.get(simple):
                    kwargs[simple] = SimpleFact(**raw[simple])
            if raw.get("provisional_score"):
                kwargs["provisional_score"] = raw["provisional_score"]
            self._facts[(provider, model)] = ModelFacts(**kwargs)

    def _save(self) -> None:
        out = {}
        for (provider, model), facts in self._facts.items():
            entry = {}
            for cap in _CAPABILITY_FIELDS:
                fact = getattr(facts, cap)
                if fact is not None:
                    entry[cap] = asdict(fact)
            for simple in ("context", "size_class"):
                fact = getattr(facts, simple)
                if fact is not None:
                    entry[simple] = asdict(fact)
            if facts.provisional_score is not None:
                entry["provisional_score"] = facts.provisional_score
            out[f"{provider}/{model}"] = entry
        write_json(self._path, out)
        harden(self._path)

    def get(self, provider: str, model: str) -> ModelFacts:
        facts = self._facts.get((provider, model), ModelFacts())
        now = _now_iso()
        changed = False
        updates = {}
        for cap in _CAPABILITY_FIELDS:
            fact = getattr(facts, cap)
            if fact is not None:
                fresh = check_staleness(fact, now)
                if fresh is not fact:
                    updates[cap] = fresh
                    changed = True
        if changed:
            facts = replace(facts, **updates)
            self._facts[(provider, model)] = facts
            self._save()
        return facts

    def ensure_capability(self, provider: str, model: str, capability: str,
                          default_status: str = "doubted", source: str = "guessed") -> CapabilityFact:
        facts = self.get(provider, model)
        existing = getattr(facts, capability)
        if existing is not None:
            return existing
        fresh = CapabilityFact(status=default_status, source=source)
        facts = replace(facts, **{capability: fresh})
        self._facts[(provider, model)] = facts
        self._save()
        return fresh

    def record_success(self, provider: str, model: str, capability: str,
                       now: Optional[str] = None) -> None:
        now = now or _now_iso()
        fact = self.ensure_capability(provider, model, capability)
        updated = record_success(fact, now)
        facts = replace(self.get(provider, model), **{capability: updated})
        self._facts[(provider, model)] = facts
        self._save()

    def record_contradicting_failure(self, provider: str, model: str, capability: str,
                                     trace_id: str, now: Optional[str] = None) -> None:
        now = now or _now_iso()
        fact = self.ensure_capability(provider, model, capability)
        updated = record_contradicting_failure(fact, now, trace_id)
        facts = replace(self.get(provider, model), **{capability: updated})
        self._facts[(provider, model)] = facts
        self._save()

    def record_discovered(self, provider: str, model: str,
                          context_window: Optional[int]) -> None:
        """Called when a catalogue refresh finds a model never seen before.

        Records only the one fact the spec calls "always" published — the
        context window — and never touches an existing context fact:
        the first value seen across restarts is kept, not churned on
        every subsequent refresh that happens to see the same model again.
        Everything else in a full ModelFacts entry (vision/tools/reasoning/
        size_class/provisional_score) needs either real traffic evidence
        (already wired, Stage 6) or a real Decider (still NullDecider,
        resolves to nothing) — this method does not fabricate either.
        """
        if context_window is None:
            return
        facts = self._facts.get((provider, model), ModelFacts())
        if facts.context is not None:
            return
        facts = replace(facts, context=SimpleFact(value=context_window, source="published"))
        self._facts[(provider, model)] = facts
        self._save()
