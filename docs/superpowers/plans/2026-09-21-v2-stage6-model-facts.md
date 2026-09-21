# flexrouter v2 — Stage 6: Model Facts and the Capability State Machine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. The owner explicitly asked for subagent-driven development and explicitly asked not to be consulted for input through the end of Stage 7 — rule on every ambiguity yourself and record it, exactly as Stages 3-5 did.

**Goal:** `state/model_facts.json`, one entry per `provider/model`, holding what is actually known about each model's capabilities (vision, tools, reasoning) through a deliberately lenient state machine (`yes`/`doubted`/`no`, never straight to `no`, any success resets to `yes`), plus simpler facts (context window, size class, a provisional score) — each fact tagged with where it came from (`published`/`observed`/`guessed`/`manual`).

**Architecture:** Same discipline as Stages 4 and 5: a new, self-contained store and a pure state machine, wired into `_router.py` as **additive evidence recording**, never a routing-decision change. `engine.py` is not touched — its own static `ModelConfig.vision` flag (used by `_skip_reason`) is a separate, existing mechanism and stays exactly as it is; `state/model_facts.json` is new, *learned* knowledge that later stages (the catalogue refresh in Stage 7, the dashboard in Stage 8) will read and eventually use to influence selection. This stage builds the knowledge base and teaches it from real traffic; it does not yet change which model gets picked.

**Tech Stack:** Python 3.11+, pytest, `dataclasses`, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-18-flexrouter-v2-design.md` (§4b, "Model facts")

**Roadmap:** `docs/superpowers/plans/2026-09-18-v2-roadmap.md` (Stage 6)

**Previous stage:** `docs/superpowers/plans/2026-09-21-v2-stage5-error-brain.md`

## Global Constraints

- Python floor is `>=3.11`. Do not raise it.
- **Nothing in this codebase may write `config.yaml`.** This stage writes only to `state/model_facts.json`.
- **Secrets are never returned by any HTTP endpoint, printed by any CLI command, or written to any file.** `state/model_facts.json` never carries provider text at all (no `sample`-shaped field here — only structured facts and trace ids as evidence pointers), so there is no scrub-ordering risk the way Stage 5 had; still, do not add a free-text field to this store without the same scrutiny.
- `FLEXROUTER_HOME` overrides the home root everywhere, with no exceptions.
- **Do not touch `engine.py`, `recovery.py`, `window.py`, `quota.py`, `rate_limits.py`, `errors.py`, `client.py`.** `ModelConfig.vision` (the existing, static, config-declared capability flag `_skip_reason` already reads) is not touched or reinterpreted by this stage's *dynamic*, learned `model_facts.json` — the two are separate mechanisms on purpose (ruling 1).
- **`audit.csv` is retained unchanged.**
- Tests: `pytest`, `asyncio_mode = "auto"` already set. **Do not use `respx` together with FastAPI's `TestClient`.** Endpoint tests monkeypatch `flexrouter.client.AsyncClient.chat` / `.stream_chat` instead.
- **Every `ModelConfig` fixture needs `rpm=60, tpm=60000, context_window=<something big>` explicitly.**
- **Selection tests that need a specific model chosen must use widely separated scores (99 vs 40).**
- **Run pytest synchronously.** Bash tool, `run_in_background` unset (never `true`), `timeout: 600000`.
- `tests/conftest.py` has an autouse fixture pointing `FLEXROUTER_HOME` at a temp directory. Do not remove it.
- **README.md currently holds an unsaved rewrite that is not from any agent.** Do not stage it, do not revert it, do not touch it. Never `git add -A` or `git add .` — name files explicitly.
- **If you ever see a file you're editing change content you didn't just write yourself, stop and report it immediately.**
- The suite must finish at **713 passed, 1 skipped or better** with no ignore flags: `python -m pytest -q`.

## Rulings made while writing this plan

Record these as ADR 0013 in the final task.

1. **`state/model_facts.json` is entirely separate from `engine.py`'s existing static `ModelConfig.vision` flag, and neither reads nor writes the other.** `ModelConfig.vision` is a fact the owner declared in settings, used today by `_skip_reason` to reject a vision request outright. `model_facts.json` is *learned* knowledge, built from real traffic, that later stages will use to refine or corroborate that declaration — not this stage. Connecting the two (e.g. having a learned `no` actually block a request the way the static flag does) is exactly the kind of routing-decision change Stage 5 already established belongs to its own, separately-reviewed task — deferred here for the same reason, filed as a follow-up.
2. **This stage closes the loop Stage 5's ruling 5 explicitly left open.** Stage 5 declined to add a `bad_request` rule to `classify_by_rule` because nothing existed to send that evidence to. `state/model_facts.json` is now that destination, so this stage adds the rule: a `ProviderError` with `status_code == 400` and no more specific match classifies as `bad_request` (`source="rule"`, `confidence=1.0`). `flexrouter/error_brain.py` is not "reused unchanged" — it is this project's own Stage 5 code, open for this kind of additive, spec-anticipated extension.
3. **`ErrorBrain` gains one small public surface, a `confidence_threshold` property**, so `_router.py` can compare a verdict's confidence against the same number `ErrorBrain` itself already uses for `flagged_for_review`, without duplicating the constant. No behavior of `ErrorBrain` changes; this is purely a read accessor.
4. **Only `vision` gets real evidence wiring in this stage.** The spec's capability state machine is general — it applies to `vision`, `tools`, and `reasoning` alike — and the state machine functions this stage builds work for any of the three. But `vision` is the only capability the router currently tracks per-request (`agenerate`/`agenerate_stream`'s existing `vision: bool` parameter, already reflected in the trace's `asked.needs`). `tools` and `reasoning` have no equivalent request-level "did this call actually exercise it" signal anywhere in the codebase yet — inventing one (e.g. detecting `tools=[...]` in `**kwargs`) is a small feature of its own, filed as a follow-up rather than smuggled into this stage's scope.
5. **A failure counts as capability evidence only when**: `vision=True` was requested for this attempt, **and** the classified verdict is `bad_request` or `model_gone`, **and** `verdict.confidence >= self._error_brain.confidence_threshold`. All three conditions must hold — matching the spec's own wording exactly ("a failure counts as evidence only when the error brain returns a verdict of `bad_request` or `model_gone` at or above the confidence threshold, and the request actually exercised that capability").
6. **A success counts as capability evidence whenever `vision=True` was requested and the attempt succeeded** — no confidence gate needed, since a real answer is unambiguous positive evidence (spec: "Any success resets `strikes` to 0 and restores `yes`. Success is stronger evidence than failure.").
7. **`context`, `size_class`, and `provisional_score` are modeled as simple, state-machine-free facts** (`SimpleFact`: `value`, `source`, `confidence`), not run through the yes/doubted/no transition logic — the spec's "capability state machine" section is specifically about the three boolean capabilities; these three are flatter facts with no strikes, no staleness, no override-threshold. Nothing in this stage writes them yet (there is no catalogue refresh or benchmark-ranking feature to produce a value) — they exist on the `ModelFacts` schema so Stage 7/9 can populate them without a schema migration, matching the same "declare the interface now, wire the producer later" pattern Stage 5 used for `describe_model`.
8. **Overriding a `published` fact requires 5 contradicting strikes; overriding anything else (`observed`, `guessed`) requires 3** — matching the spec exactly. A `manual` fact is never overridden by evidence at all; a contradicting result is silently absorbed (the state machine function is a no-op on a manual fact) rather than raising an exception, since nothing in this codebase has a "notice" delivery mechanism yet (that's a dashboard concern, Stage 8) — the absence of a UI to show a notice is not a reason to crash the request path that happened to generate one.
9. **Staleness reversion (`observed` `no` → `doubted` after 30 days) is a pure function, `check_staleness(fact, now)`, called on read** (inside `ModelFactsStore.get`), not by a background timer — this project has no scheduler infrastructure yet, and checking on read is simpler, has no missed-tick failure mode, and is exactly as correct: a fact's staleness only matters at the moment something is about to use it.
10. **`ModelFactsStore` is rebuilt on `LocalRouter.reload()`**, matching the `TraceWriter`/`KeyStateStore`/`ErrorBrain` pattern already established in Stages 3-5.

---

## File Structure

| File | Responsibility |
|---|---|
| `flexrouter/model_facts.py` | **new** — `CapabilityFact`, `SimpleFact`, `ModelFacts` dataclasses; the pure state-machine functions (`record_success`, `record_contradicting_failure`, `check_staleness`); `ModelFactsStore` (persistence). |
| `flexrouter/error_brain.py` | **modify** — one new built-in rule (400 → `bad_request`), one new public property (`confidence_threshold`). |
| `flexrouter/_router.py` | **modify** — `self._model_facts` in `__init__`/`reload`; capability-evidence calls (failure and success) for `vision` in both `agenerate` and `agenerate_stream`. |

---

### Task 1: `ModelFacts`, the capability state machine, and `ModelFactsStore`

**Files:**
- Create: `flexrouter/model_facts.py`
- Modify: `flexrouter/error_brain.py` (the `bad_request` rule, the `confidence_threshold` property)
- Test: `tests/test_model_facts.py`, additions to `tests/test_error_brain.py`

**Interfaces:**
- Consumes: nothing new (`flexrouter.store.read_json`/`write_json`/`harden`, pre-existing).
- Produces:
  - `@dataclass class CapabilityFact: status: str; source: str; confidence: float = 1.0; strikes: int = 0; last_success_at: Optional[str] = None; last_failure_at: Optional[str] = None; evidence: list[str] = field(default_factory=list)` — `status` is `"yes" | "doubted" | "no"`, `source` is `"published" | "observed" | "guessed" | "manual"`.
  - `@dataclass class SimpleFact: value: object; source: str; confidence: float = 1.0`
  - `@dataclass class ModelFacts: vision: Optional[CapabilityFact] = None; tools: Optional[CapabilityFact] = None; reasoning: Optional[CapabilityFact] = None; context: Optional[SimpleFact] = None; size_class: Optional[SimpleFact] = None; provisional_score: Optional[dict] = None`
  - `record_success(fact: CapabilityFact, now: str) -> CapabilityFact` — no-op (returns `fact` unchanged) if `fact.source == "manual"`; otherwise returns a new fact with `status="yes"`, `strikes=0`, `last_success_at=now`.
  - `record_contradicting_failure(fact: CapabilityFact, now: str, trace_id: str) -> CapabilityFact` — no-op if `fact.source == "manual"`; otherwise increments `strikes`, appends `trace_id` to `evidence`, sets `last_failure_at=now`, and sets `status="no"` once `strikes >= (5 if fact.source == "published" else 3)`, else `status="doubted"`.
  - `check_staleness(fact: CapabilityFact, now: str, stale_after_days: int = 30) -> CapabilityFact` — if `fact.status == "no"` and `fact.source == "observed"` and `fact.last_failure_at` is more than `stale_after_days` old, returns a copy with `status="doubted"` (strikes/evidence untouched — the history stays visible); otherwise returns `fact` unchanged.
  - `class ModelFactsStore(state_dir: str)`:
    - `.get(provider: str, model: str) -> ModelFacts` — returns an empty `ModelFacts()` if never seen; applies `check_staleness` to each capability fact before returning (ruling 9).
    - `.ensure_capability(provider: str, model: str, capability: str, default_status: str = "doubted", source: str = "guessed") -> CapabilityFact` — returns the existing fact for `capability` if one exists, else creates and persists a fresh one with the given defaults. Used so evidence-recording never has to special-case "this is the first time we've heard about this model's vision capability."
    - `.record_success(provider: str, model: str, capability: str, now: Optional[str] = None) -> None` — loads (or creates via `ensure_capability`) the fact, applies the module-level `record_success`, persists.
    - `.record_contradicting_failure(provider: str, model: str, capability: str, trace_id: str, now: Optional[str] = None) -> None` — same shape, applies `record_contradicting_failure`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_model_facts.py
from datetime import datetime, timedelta, timezone

from flexrouter.model_facts import (
    CapabilityFact, ModelFactsStore, check_staleness,
    record_contradicting_failure, record_success,
)


def _now(offset_days: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=offset_days)).isoformat(timespec="seconds")


def test_a_success_sets_yes_and_resets_strikes():
    fact = CapabilityFact(status="doubted", source="observed", strikes=2)
    updated = record_success(fact, _now())
    assert updated.status == "yes"
    assert updated.strikes == 0
    assert updated.last_success_at is not None


def test_a_success_never_touches_a_manual_fact():
    fact = CapabilityFact(status="no", source="manual", strikes=9)
    updated = record_success(fact, _now())
    assert updated == fact


def test_first_contradicting_failure_goes_to_doubted_never_straight_to_no():
    fact = CapabilityFact(status="yes", source="observed")
    updated = record_contradicting_failure(fact, _now(), "req_1")
    assert updated.status == "doubted"
    assert updated.strikes == 1
    assert updated.evidence == ["req_1"]


def test_three_strikes_on_an_observed_fact_reaches_no():
    fact = CapabilityFact(status="doubted", source="observed", strikes=2)
    updated = record_contradicting_failure(fact, _now(), "req_3")
    assert updated.status == "no"
    assert updated.strikes == 3


def test_overriding_a_published_fact_needs_five_strikes_not_three():
    fact = CapabilityFact(status="doubted", source="published", strikes=2)
    updated = record_contradicting_failure(fact, _now(), "req_3")
    assert updated.status == "doubted"  # 3 strikes is not enough for a published fact
    assert updated.strikes == 3
    updated = record_contradicting_failure(updated, _now(), "req_4")
    updated = record_contradicting_failure(updated, _now(), "req_5")
    assert updated.status == "no"
    assert updated.strikes == 5


def test_a_contradicting_failure_never_touches_a_manual_fact():
    fact = CapabilityFact(status="yes", source="manual")
    updated = record_contradicting_failure(fact, _now(), "req_1")
    assert updated == fact


def test_an_observed_no_goes_stale_after_thirty_days_and_reverts_to_doubted():
    fact = CapabilityFact(status="no", source="observed", strikes=3, last_failure_at=_now(31))
    updated = check_staleness(fact, _now())
    assert updated.status == "doubted"
    assert updated.strikes == 3  # history preserved


def test_a_recent_observed_no_does_not_go_stale():
    fact = CapabilityFact(status="no", source="observed", strikes=3, last_failure_at=_now(5))
    updated = check_staleness(fact, _now())
    assert updated.status == "no"


def test_a_published_no_does_not_go_stale_the_same_way():
    fact = CapabilityFact(status="no", source="published", strikes=5, last_failure_at=_now(31))
    updated = check_staleness(fact, _now())
    assert updated.status == "no"  # only "observed" ages out, per the spec


def test_store_creates_a_default_doubted_fact_on_first_use(tmp_path):
    store = ModelFactsStore(str(tmp_path))
    fact = store.ensure_capability("openrouter", "m", "vision")
    assert fact.status == "doubted"
    assert fact.source == "guessed"


def test_store_records_success_and_persists(tmp_path):
    store = ModelFactsStore(str(tmp_path))
    store.record_success("openrouter", "m", "vision")
    facts = store.get("openrouter", "m")
    assert facts.vision.status == "yes"

    reloaded = ModelFactsStore(str(tmp_path))
    assert reloaded.get("openrouter", "m").vision.status == "yes"


def test_store_records_contradicting_failure_and_persists(tmp_path):
    store = ModelFactsStore(str(tmp_path))
    for i in range(3):
        store.record_contradicting_failure("openrouter", "m", "vision", f"req_{i}")
    facts = store.get("openrouter", "m")
    assert facts.vision.status == "no"
    assert facts.vision.strikes == 3


def test_store_applies_staleness_on_read(tmp_path):
    store = ModelFactsStore(str(tmp_path))
    stale = CapabilityFact(status="no", source="observed", strikes=3, last_failure_at=_now(45))
    store._facts[("openrouter", "m")] = ModelFactsWithVision(stale)
    facts = store.get("openrouter", "m")
    assert facts.vision.status == "doubted"


def ModelFactsWithVision(vision_fact):
    from flexrouter.model_facts import ModelFacts
    return ModelFacts(vision=vision_fact)
```

Implementer note: the last test reaches into `store._facts` directly to seed a stale fact without going through the normal record-success/record-failure path (there is no other way to construct a 45-day-old fact through the public API in a test). Confirm `ModelFactsStore` stores its in-memory data on an attribute named `_facts`, keyed by `(provider, model)` tuples, mapping to `ModelFacts` instances — keep that shape exactly, since the test depends on it.

Add to `tests/test_error_brain.py` (the existing file from Stage 5):

```python
def test_classify_by_rule_now_covers_bad_request():
    from flexrouter.error_brain import classify_by_rule
    assert classify_by_rule("malformed request body", 400).verdict == "bad_request"


def test_error_brain_exposes_its_confidence_threshold(tmp_path):
    from flexrouter.decider import NullDecider
    from flexrouter.error_brain import ErrorBrain
    brain = ErrorBrain(str(tmp_path), NullDecider(), confidence_threshold=0.8)
    assert brain.confidence_threshold == 0.8
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_model_facts.py -q` and `python -m pytest tests/test_error_brain.py -k "bad_request or confidence_threshold" -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flexrouter.model_facts'`; `AttributeError` on the two new error_brain checks.

- [ ] **Step 3: Write the implementation**

```python
# flexrouter/model_facts.py
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
```

In `flexrouter/error_brain.py`, add the `bad_request` rule to `classify_by_rule` — find the existing `if status is not None:` block that checks `_STATUS_RULES.get(status)` and the `status >= 500` fallback, and add a `400` case right after the `_STATUS_RULES` lookup, before the `>= 500` check:

```python
    if status is not None:
        verdict = _STATUS_RULES.get(status)
        if verdict:
            return ErrorVerdict(verdict=verdict, source="rule", confidence=1.0)
        if status == 400:
            return ErrorVerdict(verdict="bad_request", source="rule", confidence=1.0)
        if status >= 500:
            return ErrorVerdict(verdict="their_end_temporary", source="rule", confidence=1.0)
```

Add a public property to the `ErrorBrain` class, near its `__init__`:

```python
    @property
    def confidence_threshold(self) -> float:
        return self._threshold
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_model_facts.py tests/test_error_brain.py -q`
Expected: PASS, 15 tests (13 new in `test_model_facts.py` + 2 new in `test_error_brain.py`).

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: 713+ passed, 1 skipped. The `bad_request` addition to `classify_by_rule` must not change any existing rule's output — if an existing `error_brain` test asserted `classify_by_rule("...", 400) is None`, that assertion was testing the pre-Stage-6 gap and should now be updated to expect `bad_request`, per this stage's explicit purpose.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/model_facts.py flexrouter/error_brain.py tests/test_model_facts.py tests/test_error_brain.py
git commit -m "feat(facts): model_facts.json and the capability state machine, plus the bad_request rule it enables"
```

---

### Task 2: Wire vision-capability evidence into `agenerate`

**Files:**
- Modify: `flexrouter/_router.py` (`__init__`, `reload`, the three failure branches and the success path inside `agenerate`)
- Test: `tests/test_model_facts_agenerate.py`

**Interfaces:**
- Consumes: `flexrouter.model_facts.ModelFactsStore` (Task 1).
- Produces: `LocalRouter._model_facts: ModelFactsStore` (new attribute).

**Read the current content of `flexrouter/_router.py` in full before editing** — this file now has Stage 5's `verdict = self._error_brain.classify(...)` line in each failure branch; your new evidence-recording call goes immediately after that line, using the `verdict` variable it already produced.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_model_facts_agenerate.py
from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000, vision=True)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _router(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


def test_a_400_on_a_vision_request_records_a_contradicting_failure(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_400(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: malformed image input", status_code=400)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_400)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", vision=True, wait=False)
    except Exception:
        pass

    facts = router._model_facts.get("alpha", "big")
    assert facts.vision.status == "doubted"
    assert facts.vision.strikes == 1


def test_a_non_vision_request_does_not_record_vision_evidence(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_400(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: bad input", status_code=400)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_400)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    facts = router._model_facts.get("alpha", "big")
    assert facts.vision is None


def test_a_successful_vision_request_records_success(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def ok(self, route, messages, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", ok)
    router._model_facts.record_contradicting_failure("alpha", "big", "vision", "req_prior")
    router.generate([{"role": "user", "content": "hi"}], "smart", vision=True)

    facts = router._model_facts.get("alpha", "big")
    assert facts.vision.status == "yes"
    assert facts.vision.strikes == 0


def test_a_500_on_a_vision_request_does_not_count_as_capability_evidence(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_500(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: upstream error", status_code=500)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_500)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", vision=True, wait=False)
    except Exception:
        pass

    facts = router._model_facts.get("alpha", "big")
    assert facts.vision is None  # their_end_temporary is not bad_request/model_gone
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_model_facts_agenerate.py -q`
Expected: FAIL — `router._model_facts` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

Add the import:

```python
from flexrouter.model_facts import ModelFactsStore
```

In `LocalRouter.__init__`, after the line that builds `self._error_brain`, add:

```python
        self._model_facts = ModelFactsStore(self._cfg.state_dir)
```

In `reload()`, after the line that rebuilds `self._error_brain`, add the same line.

In each of `agenerate`'s three failure branches, immediately after the existing `verdict = self._error_brain.classify(...)` line, add:

```python
                if vision and verdict.verdict in ("bad_request", "model_gone") and \
                        verdict.confidence >= self._error_brain.confidence_threshold:
                    self._model_facts.record_contradicting_failure(
                        route.provider, route.model, "vision", trace_id)
```

(identical three-line block in all three branches — `RateLimitError`, `RouterError`, `ProviderError` — right after their own `verdict = ...` line, before the existing `attempts.append`.)

On the success path, right before the existing `_write_trace(ok=True, ...)` call, add:

```python
            if vision:
                self._model_facts.record_success(route.provider, route.model, "vision")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_model_facts_agenerate.py -q`
Expected: PASS, 4 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: 728+ passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/_router.py tests/test_model_facts_agenerate.py
git commit -m "feat(facts): record vision-capability evidence from agenerate"
```

---

### Task 3: Wire vision-capability evidence into `agenerate_stream`

Mirrors Task 2. `self._model_facts` already exists after Task 2.

**Files:**
- Modify: `flexrouter/_router.py` (`agenerate_stream` only)
- Test: `tests/test_model_facts_agenerate_stream.py`

**Interfaces:**
- Consumes: `self._model_facts` (Task 2).
- Produces: no new names.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_model_facts_agenerate_stream.py
from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError, StreamChunk
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000, vision=True)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _router(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


async def test_a_400_on_a_streamed_vision_request_records_a_contradicting_failure(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_400(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: malformed image input", status_code=400)
        yield  # pragma: no cover

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", always_400)
    try:
        async for _ in router.agenerate_stream(
                [{"role": "user", "content": "hi"}], "smart", vision=True):
            pass
    except Exception:
        pass

    facts = router._model_facts.get("alpha", "big")
    assert facts.vision.status == "doubted"


async def test_a_successful_streamed_vision_request_records_success(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def ok(self, route, messages, **kwargs):
        yield StreamChunk(content="hi")
        yield StreamChunk(usage={"total_tokens": 1})

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", ok)
    router._model_facts.record_contradicting_failure("alpha", "big", "vision", "req_prior")
    async for _ in router.agenerate_stream([{"role": "user", "content": "hi"}], "smart", vision=True):
        pass

    facts = router._model_facts.get("alpha", "big")
    assert facts.vision.status == "yes"
```

Implementer note: check this repo's established convention for driving `agenerate_stream` in a test (`asyncio_mode = "auto"` in `pyproject.toml`) — match whatever `tests/test_error_brain_agenerate_stream.py` (Stage 5) already does rather than inventing new boilerplate.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_model_facts_agenerate_stream.py -q`
Expected: FAIL — no capability evidence recorded from the streaming path yet.

- [ ] **Step 3: Write the implementation**

In each of `agenerate_stream`'s failure branches whose verdict can plausibly be `bad_request` or `model_gone` (in practice: `ProviderError`, and the post-commit `except BaseException` handler — the empty-stream and empty-completion branches always classify with `status=None`, which `classify_by_rule` never maps to either verdict, so adding the check there is harmless but will simply never fire; add it uniformly anyway for consistency with `agenerate`'s pattern and so a future rule change is automatically covered), immediately after each branch's existing `verdict = self._error_brain.classify(...)` line, add:

```python
                if vision and verdict.verdict in ("bad_request", "model_gone") and \
                        verdict.confidence >= self._error_brain.confidence_threshold:
                    self._model_facts.record_contradicting_failure(
                        route.provider, route.model, "vision", trace_id)
```

Apply this identically to all six failure branches in `agenerate_stream` (empty-stream, `RateLimitError`, `RouterError`, `ProviderError`, the post-commit `except BaseException`, and the post-commit empty-no-partial-output retry branch) for uniformity with `agenerate`'s three-branch treatment — do not special-case which branches "can" produce the right verdict; the condition itself already guards correctness.

On the success path, right before the existing `_write_trace(ok=True, ...)` call near the end of the method, add:

```python
            if vision:
                self._model_facts.record_success(route.provider, route.model, "vision")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_model_facts_agenerate_stream.py -q`
Expected: PASS, 2 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: 730+ passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/_router.py tests/test_model_facts_agenerate_stream.py
git commit -m "feat(facts): record vision-capability evidence from agenerate_stream"
```

---

### Task 4: End-to-end proof, docs, and the ADR

**Files:**
- Test: `tests/test_model_facts_e2e.py`
- Modify: `CONTEXT.md`
- Create: `docs/adr/0013-model-facts-are-learned-separately-from-declared-config.md`

**Interfaces:**
- Consumes: everything from Tasks 1-3.
- Produces: no new names.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_model_facts_e2e.py
import json

from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000, vision=True)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def test_a_real_400_on_a_vision_request_through_the_http_surface_is_recorded_as_evidence(
        tmp_path, monkeypatch):
    async def boom(self, route, messages, **kwargs):
        from flexrouter.client import ProviderError
        raise ProviderError("alpha rejected the image", status_code=400)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", boom)
    client = _client(tmp_path, monkeypatch)
    client.post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})

    facts_path = tmp_path / "state" / "model_facts.json"
    assert facts_path.exists()
```

Implementer note: `vision` is not part of the OpenAI chat-completions wire format, so this test cannot set it through the HTTP request body — check `flexrouter/app.py`'s `chat_completions` handler for how (or whether) `vision` reaches `router.agenerate` at all today. If nothing in the current HTTP surface ever sets `vision=True` (a real possibility — this may be a library-only parameter, not yet wired into the wire protocol), adjust this test to assert what's actually true: that the state directory and `model_facts.json` machinery works end-to-end via `LocalRouter` directly (matching Task 2/3's own tests), and note in your report that `vision` is not reachable from the HTTP surface yet if that's what you find — this is a fact about existing, pre-Stage-6 code, not something to fix here.

- [ ] **Step 2: Run the test to verify it passes or reveals the vision-reachability question above**

Run: `python -m pytest tests/test_model_facts_e2e.py -q`
Expected: PASS, possibly after the adjustment described in the implementer note.

- [ ] **Step 3: Update `CONTEXT.md`**

Add a glossary entry after "Error brain" and before "The home" (or wherever the current file's order makes clear is right):

```markdown
- **Model facts** — `state/model_facts.json`, one entry per `provider/model`, holding what is actually known about a model's capabilities — vision, tools, reasoning — each tagged `yes`/`doubted`/`no` with where the belief came from (`published`/`observed`/`guessed`/`manual`) and how many contradicting results it has survived (`flexrouter/model_facts.py`: `ModelFactsStore`, `CapabilityFact`). Deliberately separate from `ModelConfig.vision`, the static flag declared in settings that `engine.py` already acts on today — this store is *learned* from real traffic and does not (yet) influence routing. One contradicting failure only ever casts doubt, never certainty (`record_contradicting_failure`); any success is stronger evidence than any failure and restores `yes` outright (`record_success`). A `manual` fact is never touched by either. This stage also closes a gap Stage 5 left open: `flexrouter/error_brain.py` now classifies a bare 400 as `bad_request`, since `model_facts.json` is finally somewhere for that evidence to go.
```

Update the "Not yet built" paragraph:

```markdown
The v2 design (`docs/superpowers/specs/2026-09-18-flexrouter-v2-design.md`) describes nine stages. Stages 1 through 6 are implemented: the shared home, the OpenAI-shaped surface, the per-request trace, real per-key state, the error brain that classifies (but does not act on) failed attempts, and now model facts that learn a model's capabilities from real traffic (but do not yet influence which model gets picked). Do not treat later-stage concepts (anything not covered above) as present in the code.
```

- [ ] **Step 4: Write the ADR**

```markdown
# 0013. Model facts are learned, separately from declared config

Date: 2026-09-21
Status: Accepted

## Context

Spec section 4b asks for a persistent record of what each model can
actually do — vision, tools, reasoning — built from real traffic through a
deliberately lenient state machine: one contradicting result only ever
casts doubt, never certainty; three contradicting results (five for a
value the provider itself published) are needed before something is
marked `no`; any success is stronger evidence than any failure and
restores `yes` outright.

`engine.py` already has a related, but different, mechanism:
`ModelConfig.vision`, a flag the owner declares in settings, which
`_skip_reason` already uses today to reject a vision request against a
model that doesn't claim to support it. That flag is static and
config-driven. What this stage builds is dynamic and traffic-driven.
Conflating the two — letting a learned `no` actually block a request the
way the declared flag does — would be exactly the kind of routing-decision
change Stage 5 already established belongs in its own, separately
reviewed task, not folded into the stage that builds the knowledge base
in the first place.

Stage 5 (ADR 0012, ruling 5) declined to add a `bad_request` classification
rule because nothing existed yet to send that evidence to. This stage is
that destination.

## Decision

**`state/model_facts.json` and `ModelConfig.vision` remain two separate,
unconnected mechanisms.** Neither reads nor writes the other. This stage
teaches the model-facts store from real traffic; it does not wire that
knowledge back into selection. That wiring — and the judgment call of
whether a learned `no`/`doubted` should ever be allowed to override an
owner's explicit config, or only supplement missing config — is deferred
to a stage that can give it the same task-by-task care Stages 2-5 gave
their own routing changes.

**Stage 5's deferred `bad_request` rule is added now.** `classify_by_rule`
maps a bare 400 status to `bad_request`, `source="rule"`, `confidence=1.0`
— `flexrouter/error_brain.py` is this project's own code, not a frozen
file, so this is a normal, anticipated extension, not an exception to
anything.

**Only `vision` gets real evidence wiring this stage.** The state machine
itself (`record_success`, `record_contradicting_failure`,
`check_staleness`) is capability-agnostic and works identically for
`tools` and `reasoning` — but the router has no per-request signal today
for whether a call actually exercised tool-calling or extended reasoning,
the way `vision: bool` already exists for image input. Inventing that
signal is a small feature of its own, filed as a follow-up, not smuggled
into a stage whose job is the state machine and the store.

**A failure counts as evidence under three conditions, all required**:
the request asked for `vision`, the classified verdict is `bad_request` or
`model_gone`, and the verdict's confidence is at or above
`ErrorBrain.confidence_threshold` — a new, small public property added to
`ErrorBrain` so this comparison doesn't duplicate the same number
`ErrorBrain` already enforces internally for its own `flagged_for_review`
marking.

**A `manual` fact absorbs a contradicting result silently rather than
raising.** The spec calls for "a notice," but nothing in this codebase has
a notice-delivery mechanism yet (that's a dashboard concern, Stage 8) —
a no-op is the correct behaviour until there is somewhere for a notice to
go; a request that happens to generate disputed evidence about a
manually-pinned fact should not crash because of it.

**Staleness (an `observed` `no` reverting to `doubted` after 30 days) is
checked on read**, inside `ModelFactsStore.get`, not by a background job —
this project has no scheduler infrastructure, and a fact's staleness only
matters at the moment something is about to consult it.

## Consequences

- A future dashboard (Stage 8) or a future selection-preference stage can
  read `state/model_facts.json` and get real, evidence-backed capability
  beliefs, with full provenance and a strike history, without having
  built any of the collection machinery themselves.
- `tools` and `reasoning` capability facts can exist in the schema (a
  dashboard could show them as "not yet observed") but will never actually
  gain evidence until the follow-up request-level detection feature lands.
- The next stage that wants routing to actually consult this store
  inherits a state machine that is already correct and already tested —
  it only has to decide how to weigh "learned" against "declared," not
  build the learning itself.
```

- [ ] **Step 5: Run the whole suite one last time**

Run: `python -m pytest -q`
Expected: 713 + (this stage's new test count) passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add tests/test_model_facts_e2e.py CONTEXT.md docs/adr/0013-model-facts-are-learned-separately-from-declared-config.md
git commit -m "docs(facts): prove model facts end to end, and record the scope ruling as ADR 0013"
```

---

## After all four tasks

- Run the full suite once more and record the final passed/skipped count.
- File anything discovered but out of scope under `.scratch/v2-stage6-followups/issues/` — in particular, the deferred "wire tools/reasoning evidence detection" and "let selection actually consult model_facts.json" items named in this plan's rulings.
- There is no git remote. Merge this stage's branch into `master` yourself once its final whole-branch review is clean, the same way Stages 3-5 did — the owner explicitly asked not to be consulted through the end of Stage 7.
