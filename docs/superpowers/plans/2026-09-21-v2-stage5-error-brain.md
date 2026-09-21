# flexrouter v2 — Stage 5: The Decider Protocol and the Error Brain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. The owner explicitly asked for subagent-driven development and explicitly asked not to be consulted for input through the end of Stage 7 — rule on every ambiguity yourself and record it, exactly as Stages 3 and 4 did.

**Goal:** Give every failed request a real, typed reason (`too_fast`, `bad_key`, `needs_payment`, `model_gone`, `their_end_temporary`, `message_too_long`, `bad_request`, `unknown`) instead of an opaque error string, by adding a `Decider` protocol, a `NullDecider` default, built-in status-code/substring rules, and a persistent, self-teaching error brain (`state/error_brain.json`) — and populate `attempts[].verdict`, the field Stage 3 reserved (ADR 0010 ruling 5) and left `null` ever since.

**Architecture:** This stage is deliberately **observability-only**. The status-code-driven routing decisions this codebase already makes (401/403 → bench the key, 402 → quarantine the provider, 404/410 → quarantine the route, 429 → cool the key — all built in Stages 2 through 4) are not touched, not duplicated, and not second-guessed. The error brain classifies every failure into the spec's vocabulary and records it for the trace and for a future dashboard, but nothing in `_router.py`'s existing quarantine/penalize/bench decisions changes because of what it says. `engine.py` is not touched.

**Tech Stack:** Python 3.11+, pytest, `dataclasses`, `typing.Protocol`, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-18-flexrouter-v2-design.md` (§4, "The decision layer" and §4a, "The error brain")

**Roadmap:** `docs/superpowers/plans/2026-09-18-v2-roadmap.md` (Stage 5). Also resolves the roadmap's Open Q4 ("ship with `NullDecider` as the default").

**Previous stage:** `docs/superpowers/plans/2026-09-21-v2-stage4-per-key-state.md`

## Global Constraints

- Python floor is `>=3.11`. Do not raise it.
- **Nothing in this codebase may write `config.yaml`.** This stage writes only to `state/error_brain.json`.
- **Secrets are never returned by any HTTP endpoint, printed by any CLI command, or written to any file.** `state/error_brain.json`'s `sample` field is provider-supplied text — it must be scrubbed the same way `state/traces.jsonl` already is (ADR 0010), with no exceptions.
- `FLEXROUTER_HOME` overrides the home root everywhere, with no exceptions.
- **Do not touch `engine.py`, `recovery.py`, `window.py`, `quota.py`, `rate_limits.py`, `errors.py`, `client.py`.** This stage's classification logic is purely additive next to the existing status-code branching in `_router.py` — it does not replace `exc.is_permanent`/`exc.is_provider_wide` (`client.py`), and it does not change which of `quarantine`/`quarantine_provider`/`penalize`/`mark_benched`/`mark_cooling` gets called anywhere. If a task seems to need a routing-decision change, stop and say so — it is out of this stage's scope by ruling 1 below.
- **`audit.csv` is retained unchanged.**
- Tests: `pytest`, `asyncio_mode = "auto"` already set. **Do not use `respx` together with FastAPI's `TestClient`.** Endpoint tests monkeypatch `flexrouter.client.AsyncClient.chat` / `.stream_chat` instead.
- **Every `ModelConfig` fixture needs `rpm=60, tpm=60000, context_window=<something big>` explicitly.**
- **Selection tests that need a specific model chosen must use widely separated scores (99 vs 40).**
- **Run pytest synchronously.** Bash tool, `run_in_background` unset (never `true`), `timeout: 600000`. Never background pytest and wait for a notification.
- `tests/conftest.py` has an autouse fixture pointing `FLEXROUTER_HOME` at a temp directory. Do not remove it.
- **README.md currently holds an unsaved rewrite that is not from any agent.** Do not stage it, do not revert it, do not touch it. Never `git add -A` or `git add .` — name files explicitly.
- **If you ever see a file you're editing change content you didn't just write yourself, stop and report it immediately.** A prior stage on this project had an abandoned, unstopped background agent mutate files concurrently with active work — confirm no other agent should be touching your files, and if you spawn a subagent yourself (you must not — see each task's own instructions) that is a process violation on its own.
- The suite must finish at **688 passed, 1 skipped or better** with no ignore flags: `python -m pytest -q`.

## Rulings made while writing this plan

Record these as ADR 0012 in the final task. Do not ask the owner about any of these.

1. **This stage is observability-only — it does not change any existing routing decision.** The spec's §4a table ("Router action" column) maps each verdict to a router behaviour, but every one of those behaviours for the cases this codebase can already tell apart from a bare status code (`bad_key`↔401/403, `needs_payment`↔402/`is_provider_wide`, `model_gone`↔404/410/`is_permanent`, `too_fast`↔429) is **already implemented**, built across Stages 2–4, already reviewed, already load-bearing. Reimplementing the same decisions through a new `Decider`/verdict layer would either duplicate that logic (two sources of truth for the same fact) or risk silently changing it. This stage adds the *vocabulary* and the *memory* (the error brain persists and classifies unrecognized text) without moving the *decision*. Acting on a verdict — the part of the spec's table this stage does not implement — is filed as a follow-up for a stage that can give it the same care Stages 2–4 got, not silently dropped.
2. **The classifier is genuinely valuable for exactly one case this codebase does not already handle: `ProviderError` whose `status_code` is not 402/404/410/429/401/403** — an arbitrary 4xx (400, 422, …), a 5xx, or `None` (a below-HTTP-layer failure). For every other failure branch, the router already knows the verdict unambiguously from which `except` clause fired, and passes a fixed, correct status to the classifier rather than re-deriving it from text: `RateLimitError` branch always classifies with `status=429`; the auth-failure (`RouterError`) branch always classifies with `status=401` (this codebase's `client.py` raises `RouterError` only for `resp.status_code in (401, 403)`, and both map to the identical `bad_key` verdict per the spec's table, so 401 is a safe, accurate stand-in for either). Empty-stream/empty-completion branches (no real HTTP status exists) classify with `status=None` and the literal message text, which correctly falls through every built-in rule to `unknown` — an honest verdict for "the provider said nothing was wrong and then sent nothing useful."
3. **Built-in rules run before any `Decider` is ever consulted, and never call `text.lower()` on unscrubbed text.** `ErrorBrain.classify(text, status)` scrubs `text` via `flexrouter.redact.scrub` as its very first action, unconditionally, before rule-matching, fingerprinting, or persistence — defense-in-depth matching `TraceWriter`'s established "scrub on the way in, regardless of caller" discipline (ADR 0010), even though every call site in `_router.py` already passes `str(exc)` and could in principle be pre-scrubbed by the caller. Nothing unscrubbed is ever hashed, matched, or written, full stop.
4. **`message_too_long` is a substring rule, not a status-code rule** — no status code means "the prompt was too long" on its own. Matched against phrases real providers use (`"context length"`, `"maximum context"`, `"too long"`, `"context_length_exceeded"`), case-insensitively, on the scrubbed text.
5. **`bad_request` has no built-in rule at all in this stage.** The spec's table says a `bad_request` verdict should "feed to §4b as capability evidence" — but §4b (`state/model_facts.json`, the capability state machine) does not exist yet (Stage 6). Without a place to feed that evidence, classifying something as `bad_request` here would be a verdict with no consumer. A 400-class `ProviderError` with no more specific match falls through to the `Decider` (which, under the shipped `NullDecider`, returns `unknown`) rather than being force-fit into a `bad_request` rule that does nothing yet. This is the accepted cost of sequencing: Stage 6 can add the rule once it has somewhere to send the evidence.
6. **`ModelFacts` is represented as a plain `dict`, not a dataclass, in this stage's `Decider` protocol.** `describe_model`'s return shape is fully specified in spec §4b (vision/tools/reasoning/context/size_class/provisional_score, each itself carrying `value`/`source`/`confidence`) — that belongs to Stage 6, which owns and will define the real dataclass. Inventing one now and migrating it later is pure churn. `describe_model` is declared on the `Decider` protocol (so the interface matches the spec exactly) and implemented trivially by `NullDecider` (returns `{}`), but **nothing calls it this stage** — there is no catalogue refresh yet (Stage 7) to call it with.
7. **`NullDecider` ships as the default and only implementation.** This resolves the roadmap's Open Q4 directly: `typesafe/jev-1.13` access and pricing are still unconfirmed, so no real classifier ships. Every unrecognized error becomes `unknown`, `source="null"`, `confidence=0.0` — always below the confidence threshold, always "flag for review, do not act," which this stage already guarantees by construction (ruling 1: nothing acts on verdicts yet regardless of confidence).
8. **The confidence threshold (spec default `0.80`) is enforced by `ErrorBrain`, not by the `Decider`.** A `Decider` returns whatever confidence it computed; `ErrorBrain.classify()` is the one place that decides whether a below-threshold result gets a `flagged_for_review: True` marker before being stored. This keeps the threshold a single, changeable number in one place rather than duplicated into every future `Decider` implementation.
9. **Manual entries are respected defensively, even though nothing in this stage ever creates one.** `ErrorBrainEntry` carries a `source` field; `ErrorBrain`'s write path never overwrites an existing entry whose `source == "manual"` — it only updates `seen`/`last_at`/`sample` bookkeeping and leaves `verdict`/`confidence` untouched, matching spec's "manual entries are never overwritten, only flagged when reality disagrees." No CLI or dashboard writes a manual entry yet (that is a later stage); this rule exists so that when one does, `ErrorBrain` already honours it correctly.
10. **The fingerprint algorithm matches the spec's worked example exactly**: lowercase, every digit replaced with `#`, whitespace collapsed to single spaces, clipped to 200 characters — computed on the already-scrubbed text (ruling 3), so a scrubbed credential's `…tail` remnant is itself further normalized rather than treated as meaningfully distinct text.
11. **A fingerprint hit short-circuits before consulting the `Decider` at all** — the "steady-state cost is ~zero" property the spec calls out. Rule-matches don't touch the brain's stored fingerprints; only text that fails every built-in rule is fingerprinted, looked up, and (on a genuine miss) sent to the `Decider`.
12. **`ErrorBrain` is rebuilt on every `LocalRouter.reload()`**, the same pattern `TraceWriter`/`KeyStateStore` already established, so a `state_dir` change via hot config reload is picked up rather than silently continuing to write to the old location.

---

## File Structure

| File | Responsibility |
|---|---|
| `flexrouter/decider.py` | **new** — `ErrorVerdict`, the `Decider` protocol (`classify_error`, `describe_model`), `NullDecider`. |
| `flexrouter/error_brain.py` | **new** — built-in status-code/substring rules, the fingerprint function, `ErrorBrainEntry`, `ErrorBrain` (persistence, confidence threshold, manual-entry protection, the actual `classify()` orchestration `_router.py` calls). |
| `flexrouter/_router.py` | **modify** — `self._error_brain` in `__init__`/`reload`; a `classify()` call plus a `"verdict"` key added to every `attempts.append({...})` site in both `agenerate` and `agenerate_stream` (9 sites total: 3 in `agenerate`, 6 in `agenerate_stream`). |

---

### Task 1: `ErrorVerdict`, the `Decider` protocol, `NullDecider`

**Files:**
- Create: `flexrouter/decider.py`
- Test: `tests/test_decider.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass(frozen=True) class ErrorVerdict: verdict: str; source: str; confidence: float`
  - `class Decider(Protocol): def classify_error(self, text: str, status: Optional[int]) -> ErrorVerdict: ...` and `def describe_model(self, model_id: str, provider: str, published: dict) -> dict: ...`
  - `class NullDecider: def classify_error(self, text, status) -> ErrorVerdict` (always returns `ErrorVerdict("unknown", "null", 0.0)`) `def describe_model(self, model_id, provider, published) -> dict` (always returns `{}`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_decider.py
from flexrouter.decider import Decider, ErrorVerdict, NullDecider


def test_error_verdict_is_a_frozen_dataclass():
    v = ErrorVerdict(verdict="too_fast", source="rule", confidence=1.0)
    assert v.verdict == "too_fast"
    assert v.source == "rule"
    assert v.confidence == 1.0


def test_null_decider_classifies_everything_as_unknown():
    d = NullDecider()
    v = d.classify_error("anything at all", 500)
    assert v.verdict == "unknown"
    assert v.source == "null"
    assert v.confidence == 0.0


def test_null_decider_describe_model_returns_an_empty_dict():
    d = NullDecider()
    assert d.describe_model("groq/llama-3.3", "groq", {}) == {}


def test_null_decider_satisfies_the_decider_protocol():
    # A NullDecider must be usable anywhere the Decider protocol is
    # required — this is what "the router works fully without any
    # classifier configured" (spec §4) actually depends on.
    d: Decider = NullDecider()
    assert isinstance(d.classify_error("x", None), ErrorVerdict)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_decider.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flexrouter.decider'`

- [ ] **Step 3: Write the implementation**

```python
# flexrouter/decider.py
"""One small structured-output model doing classification jobs that would
otherwise be hand-maintained forever (spec §4). Two consumers share one
interface: classifying an unfamiliar provider error, and describing a
newly-discovered model's capabilities.

`describe_model` is declared here to match the spec's interface exactly,
but nothing calls it yet — there is no catalogue refresh (Stage 7) to call
it with, and its real return shape (spec §4b: vision/tools/reasoning/
context/size_class/provisional_score, each with its own value/source/
confidence) belongs to Stage 6, which will define it. Returning a plain
dict rather than inventing a dataclass now avoids a migration later.

NullDecider is the only implementation this stage ships. `typesafe/jev-1.13`
access and pricing are still unconfirmed (roadmap Open Q4) — every
unrecognized error becomes "unknown" with zero confidence, which is always
below the confidence threshold ErrorBrain enforces, so the router works
fully without any classifier configured.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class ErrorVerdict:
    verdict: str
    source: str
    confidence: float


class Decider(Protocol):
    def classify_error(self, text: str, status: Optional[int]) -> ErrorVerdict: ...
    def describe_model(self, model_id: str, provider: str, published: dict) -> dict: ...


class NullDecider:
    def classify_error(self, text: str, status: Optional[int]) -> ErrorVerdict:
        return ErrorVerdict(verdict="unknown", source="null", confidence=0.0)

    def describe_model(self, model_id: str, provider: str, published: dict) -> dict:
        return {}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_decider.py -q`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add flexrouter/decider.py tests/test_decider.py
git commit -m "feat(decider): the Decider protocol and the NullDecider default"
```

---

### Task 2: Built-in rules, fingerprinting, and `ErrorBrain`

**Files:**
- Create: `flexrouter/error_brain.py`
- Test: `tests/test_error_brain.py`

**Interfaces:**
- Consumes: `flexrouter.decider.Decider`, `ErrorVerdict`, `NullDecider` (Task 1); `flexrouter.redact.scrub` (pre-existing, unchanged); `flexrouter.store.read_json`/`write_json`/`harden` (pre-existing, unchanged).
- Produces:
  - `classify_by_rule(text: str, status: Optional[int]) -> Optional[ErrorVerdict]` — the built-in, instant rules. Returns `None` when nothing matches (the caller must then fall through to a `Decider`).
  - `fingerprint(text: str) -> str` — lowercase, digits → `#`, whitespace collapsed, clipped to 200 chars.
  - `@dataclass class ErrorBrainEntry: verdict: str; source: str; confidence: float; seen: int; first_at: str; last_at: str; sample: str; flagged_for_review: bool = False`
  - `class ErrorBrain(state_dir: str, decider: Decider, confidence_threshold: float = 0.80)`:
    - `.classify(text: str, status: Optional[int], now: Optional[str] = None) -> ErrorVerdict` — the one method `_router.py` calls. Scrubs `text` first (ruling 3); tries `classify_by_rule`; on a rule hit, records/updates the brain entry (`source="rule"`) and returns it; on a rule miss, fingerprints the scrubbed text and checks the brain for an existing entry — a hit returns the stored verdict (updating `seen`/`last_at`, never touching a `manual` entry's `verdict`/`confidence`); a genuine miss calls `self._decider.classify_error(scrubbed_text, status)`, stores a new entry (`flagged_for_review=True` when `confidence < confidence_threshold`), and returns the classifier's verdict.
    - Persists via `store.write_json`/`store.harden`, same atomic-replace guarantee every other state file in this codebase has.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_error_brain.py
from flexrouter.decider import ErrorVerdict
from flexrouter.error_brain import ErrorBrain, classify_by_rule, fingerprint


class _StubDecider:
    def __init__(self, verdict="unknown", source="stub", confidence=0.5):
        self._v = ErrorVerdict(verdict, source, confidence)
        self.calls: list = []

    def classify_error(self, text, status):
        self.calls.append((text, status))
        return self._v

    def describe_model(self, *a, **kw):
        return {}


def test_classify_by_rule_maps_every_documented_status_code():
    assert classify_by_rule("nope", 401).verdict == "bad_key"
    assert classify_by_rule("nope", 403).verdict == "bad_key"
    assert classify_by_rule("nope", 402).verdict == "needs_payment"
    assert classify_by_rule("nope", 404).verdict == "model_gone"
    assert classify_by_rule("nope", 410).verdict == "model_gone"
    assert classify_by_rule("nope", 429).verdict == "too_fast"
    assert classify_by_rule("nope", 500).verdict == "their_end_temporary"
    assert classify_by_rule("nope", 503).verdict == "their_end_temporary"


def test_classify_by_rule_catches_context_length_substrings():
    assert classify_by_rule("This model's maximum context length is 8192 tokens", None).verdict == "message_too_long"
    assert classify_by_rule("prompt is too long for this model", None).verdict == "message_too_long"


def test_classify_by_rule_returns_none_for_unrecognized_text_and_status():
    assert classify_by_rule("something genuinely new", 418) is None
    assert classify_by_rule("something genuinely new", None) is None


def test_fingerprint_normalizes_digits_and_case_and_whitespace():
    a = fingerprint("Rate limit exceeded for requests per minute (id: 8842)")
    b = fingerprint("rate limit   exceeded for requests per minute (id: 1)")
    assert a == b


def test_fingerprint_clips_to_two_hundred_chars():
    assert len(fingerprint("x" * 500)) <= 200


def test_error_brain_classifies_via_rule_without_consulting_the_decider(tmp_path):
    decider = _StubDecider()
    brain = ErrorBrain(str(tmp_path), decider)
    v = brain.classify("boom", 429)
    assert v.verdict == "too_fast"
    assert v.source == "rule"
    assert decider.calls == []  # never consulted — the rule already answered


def test_error_brain_consults_the_decider_on_a_genuine_miss(tmp_path):
    decider = _StubDecider(verdict="unknown", confidence=0.5)
    brain = ErrorBrain(str(tmp_path), decider)
    v = brain.classify("a brand new kind of failure nobody has seen", None)
    assert v.verdict == "unknown"
    assert v.source == "stub"
    assert len(decider.calls) == 1


def test_error_brain_remembers_a_fingerprint_without_reconsulting_the_decider(tmp_path):
    decider = _StubDecider()
    brain = ErrorBrain(str(tmp_path), decider)
    brain.classify("a brand new kind of failure nobody has seen", None)
    brain.classify("a brand new kind of failure nobody has seen", None)  # same fingerprint
    assert len(decider.calls) == 1  # steady-state cost is ~zero


def test_error_brain_scrubs_before_persisting(tmp_path):
    decider = _StubDecider()
    brain = ErrorBrain(str(tmp_path), decider)
    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"
    brain.classify(f"Incorrect API key provided: {leaked}", 401)
    on_disk = (tmp_path / "error_brain.json").read_text(encoding="utf-8")
    assert leaked not in on_disk


def test_error_brain_flags_low_confidence_for_review(tmp_path):
    decider = _StubDecider(verdict="unknown", confidence=0.3)
    brain = ErrorBrain(str(tmp_path), decider, confidence_threshold=0.80)
    brain.classify("something ambiguous", None)
    import json
    on_disk = json.loads((tmp_path / "error_brain.json").read_text(encoding="utf-8"))
    entry = next(iter(on_disk.values()))
    assert entry["flagged_for_review"] is True


def test_error_brain_never_overwrites_a_manual_entry(tmp_path):
    decider = _StubDecider(verdict="unknown", confidence=0.9)
    brain = ErrorBrain(str(tmp_path), decider)
    fp = fingerprint("a recurring odd message")
    brain._entries[fp] = brain._entries.get(fp) or None
    from flexrouter.error_brain import ErrorBrainEntry
    brain._entries[fp] = ErrorBrainEntry(
        verdict="bad_request", source="manual", confidence=1.0, seen=1,
        first_at="2026-01-01T00:00:00Z", last_at="2026-01-01T00:00:00Z",
        sample="a recurring odd message")
    v = brain.classify("a recurring odd message", None)
    assert v.verdict == "bad_request"
    assert v.source == "manual"
    assert decider.calls == []  # a fingerprint hit never re-consults the decider


def test_error_brain_state_survives_a_new_instance(tmp_path):
    decider = _StubDecider()
    brain = ErrorBrain(str(tmp_path), decider)
    brain.classify("boom", 402)
    reloaded = ErrorBrain(str(tmp_path), decider)
    v = reloaded.classify("boom", 402)  # same rule, but prove the file round-trips
    assert v.verdict == "needs_payment"
```

Implementer note: the `test_error_brain_never_overwrites_a_manual_entry` test reaches into `brain._entries` directly to seed a manual entry, since nothing in this stage writes one through the public API (ruling 9) — this is the only way to test the protection until a later stage adds a real writer. Confirm `ErrorBrain` stores its in-memory entries on an attribute named `_entries` (a `dict[str, ErrorBrainEntry]`) when you write the implementation, and keep that name exactly, since the test depends on it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_error_brain.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flexrouter.error_brain'`

- [ ] **Step 3: Write the implementation**

```python
# flexrouter/error_brain.py
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

_TOO_LONG_SUBSTRINGS = (
    "context length", "maximum context", "too long", "context_length_exceeded",
)


def classify_by_rule(text: str, status: Optional[int]) -> Optional[ErrorVerdict]:
    """The instant, unambiguous cases. None means "genuinely unrecognized"."""
    if status is not None:
        verdict = _STATUS_RULES.get(status)
        if verdict:
            return ErrorVerdict(verdict=verdict, source="rule", confidence=1.0)
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
                confidence_threshold: float = 0.80) -> None:
        self._path = Path(state_dir) / "error_brain.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._decider = decider
        self._threshold = confidence_threshold
        self._entries: dict[str, ErrorBrainEntry] = {
            k: ErrorBrainEntry(**v) for k, v in read_json(self._path, default={}).items()
        }

    def _save(self) -> None:
        write_json(self._path, {k: asdict(v) for k, v in self._entries.items()})
        harden(self._path)

    def classify(self, text: str, status: Optional[int], now: Optional[str] = None) -> ErrorVerdict:
        clean = scrub(text or "")
        now = now or _now_iso()

        rule = classify_by_rule(clean, status)
        if rule is not None:
            self._record(fingerprint(clean), rule, clean, now)
            return rule

        fp = fingerprint(clean)
        existing = self._entries.get(fp)
        if existing is not None:
            existing.seen += 1
            existing.last_at = now
            self._save()
            return ErrorVerdict(existing.verdict, existing.source, existing.confidence)

        verdict = self._decider.classify_error(clean, status)
        self._record(fp, verdict, clean, now)
        return verdict

    def _record(self, fp: str, verdict: ErrorVerdict, sample: str, now: str) -> None:
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_error_brain.py -q`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add flexrouter/error_brain.py tests/test_error_brain.py
git commit -m "feat(errors): built-in classification rules and a persistent, self-teaching error brain"
```

---

### Task 3: Wire the error brain into `agenerate`

**Files:**
- Modify: `flexrouter/_router.py` (`__init__`, `reload`, three `attempts.append` sites inside `agenerate`)
- Test: `tests/test_error_brain_agenerate.py`

**Interfaces:**
- Consumes: `flexrouter.error_brain.ErrorBrain` (Task 2), `flexrouter.decider.NullDecider` (Task 1).
- Produces: `LocalRouter._error_brain: ErrorBrain` (new attribute). No new public methods.

**Read the current content of `flexrouter/_router.py` in full before editing** — reproduced in this plan's design notes below is the exact current shape of `agenerate`'s three failure branches; match your edits against what's actually on disk.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_error_brain_agenerate.py
import json

from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.exceptions import RouterError


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _router(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


def _traces(tmp_path):
    p = tmp_path / "state" / "traces.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]


def test_a_404_is_classified_as_model_gone_in_the_trace(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_404(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: model archived", status_code=404)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_404)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "model_gone"


def test_an_auth_failure_is_classified_as_bad_key(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_401(self, route, messages, **kwargs):
        raise RouterError("Auth failure for provider 'alpha': 401")

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_401)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "bad_key"


def test_a_429_is_classified_as_too_fast(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)
    calls = {"n": 0}

    from flexrouter.client import RateLimitError

    async def rate_limited_then_ok(self, route, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimitError("429 from alpha/big")
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", rate_limited_then_ok)
    router.generate([{"role": "user", "content": "hi"}], "smart")

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "too_fast"


def test_an_unrecognized_status_falls_back_to_unknown(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def status_418(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: I'm a teapot", status_code=418)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", status_418)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "unknown"


def test_error_brain_state_file_is_written(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_402(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: payment required", status_code=402)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_402)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    assert (tmp_path / "state" / "error_brain.json").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_error_brain_agenerate.py -q`
Expected: FAIL — no `"verdict"` key exists in any `attempts` entry yet.

- [ ] **Step 3: Write the implementation**

Add the import:

```python
from flexrouter.decider import NullDecider
from flexrouter.error_brain import ErrorBrain
```

In `LocalRouter.__init__`, after the line that builds `self._round_robin`, add:

```python
        self._error_brain = ErrorBrain(self._cfg.state_dir, NullDecider())
```

In `reload()`, after the line that rebuilds `self._key_states`, add:

```python
        self._error_brain = ErrorBrain(self._cfg.state_dir, NullDecider())
```

In `agenerate`'s three failure branches, add one `verdict = self._error_brain.classify(...)` call each, and add `"verdict": verdict.verdict` to the corresponding `attempts.append({...})` dict:

In the `RateLimitError` branch, right before the existing `attempts.append({...})` call:

```python
                verdict = self._error_brain.classify(str(exc), 429)
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": 429,
                                 "provider_message": str(exc), "key_id": key_id,
                                 "verdict": verdict.verdict,
                                 "ms": int((time.monotonic() - start) * 1000)})
```

In the `RouterError` branch, right before its `attempts.append({...})` call (this is always an auth failure in this codebase — `client.py` raises `RouterError` only for `resp.status_code in (401, 403)` — so classify with `status=401` per ruling 2):

```python
                verdict = self._error_brain.classify(str(exc), 401)
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": None,
                                 "provider_message": str(exc), "key_id": key_id,
                                 "verdict": verdict.verdict,
                                 "ms": int((time.monotonic() - start) * 1000)})
```

In the `ProviderError` branch, right before its `attempts.append({...})` call:

```python
                verdict = self._error_brain.classify(str(exc), exc.status_code)
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": exc.status_code,
                                 "provider_message": str(exc), "key_id": key_id,
                                 "verdict": verdict.verdict,
                                 "ms": int((time.monotonic() - start) * 1000)})
```

Do not add a `"verdict"` key anywhere else — the success path's `_write_trace(ok=True, ...)` call has no `attempts` entry for the successful call itself (only prior failed attempts, if any, carry `verdict`), which is correct and matches Stage 3's existing shape.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_error_brain_agenerate.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: 688+ passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/_router.py tests/test_error_brain_agenerate.py
git commit -m "feat(errors): classify every failed attempt in agenerate"
```

---

### Task 4: Wire the error brain into `agenerate_stream`

Mirrors Task 3. `self._error_brain` already exists after Task 3 — this task only adds `classify()` calls and `"verdict"` keys to `agenerate_stream`'s own six `attempts.append` sites.

**Files:**
- Modify: `flexrouter/_router.py` (`agenerate_stream` only)
- Test: `tests/test_error_brain_agenerate_stream.py`

**Interfaces:**
- Consumes: `self._error_brain` (Task 3).
- Produces: no new names.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_error_brain_agenerate_stream.py
import json

from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError, StreamChunk
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.exceptions import RouterError


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _router(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


def _traces(tmp_path):
    p = tmp_path / "state" / "traces.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]


async def test_a_404_is_classified_as_model_gone_in_the_streaming_trace(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_404(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: model archived", status_code=404)
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", always_404)
    try:
        async for _ in router.agenerate_stream([{"role": "user", "content": "hi"}], "smart"):
            pass
    except Exception:
        pass

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "model_gone"


async def test_a_failure_after_the_first_delta_is_classified(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_stream(self, route, messages, **kwargs):
        yield StreamChunk(content="par")
        raise ProviderError("alpha/big: connection dropped", status_code=500)

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    try:
        async for _ in router.agenerate_stream([{"role": "user", "content": "hi"}], "smart"):
            pass
    except Exception:
        pass

    t = _traces(tmp_path)[0]
    assert t["attempts"][-1]["verdict"] == "their_end_temporary"


async def test_an_empty_stream_is_classified_as_unknown(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def empty_stream(self, route, messages, **kwargs):
        return
        yield  # pragma: no cover

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", empty_stream)
    try:
        async for _ in router.agenerate_stream([{"role": "user", "content": "hi"}], "smart"):
            pass
    except Exception:
        pass

    t = _traces(tmp_path)[0]
    assert t["attempts"][0]["verdict"] == "unknown"
```

Implementer note: check `tests/test_agenerate_stream.py` or `tests/test_key_selection_agenerate_stream.py` for this repo's established convention for driving `agenerate_stream` in a test (plain `async def test_...` given `asyncio_mode = "auto"`) before finalizing — match whatever's already there rather than introducing new boilerplate. The empty-stream test may need a config where retries are exhausted quickly, or `wait=False`-equivalent handling — check how existing empty-stream tests in this codebase avoid a long retry wait, and match that pattern.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_error_brain_agenerate_stream.py -q`
Expected: FAIL — no `"verdict"` key in any `agenerate_stream` attempt yet.

- [ ] **Step 3: Write the implementation**

Add a `verdict = self._error_brain.classify(...)` call plus a `"verdict": verdict.verdict` key to each of `agenerate_stream`'s six `attempts.append({...})` sites, matching Task 3's pattern exactly:

1. **Empty-stream `StopAsyncIteration`** branch — no real status, use the literal text: `verdict = self._error_brain.classify("empty stream response", None)`.
2. **`RateLimitError`** branch — `verdict = self._error_brain.classify(str(exc), 429)`.
3. **`RouterError`** (auth) branch — `verdict = self._error_brain.classify(str(exc), 401)`.
4. **`ProviderError`** branch — `verdict = self._error_brain.classify(str(exc), exc.status_code)`.
5. **The post-commit `except BaseException as exc:`** handler — `verdict = self._error_brain.classify(str(exc), getattr(exc, "status_code", None))`.
6. **The post-commit "empty, no partial output" retry branch** — `verdict = self._error_brain.classify("empty response (no content, no tool calls)", None)`.

In each case, place the `classify()` call immediately before its `attempts.append({...})`, and add `"verdict": verdict.verdict,` to that dict — following the exact same placement Task 3 used in `agenerate`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_error_brain_agenerate_stream.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: 693+ passed (688 baseline + this stage's tests so far), 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/_router.py tests/test_error_brain_agenerate_stream.py
git commit -m "feat(errors): classify every failed attempt in agenerate_stream"
```

---

### Task 5: End-to-end proof, docs, and the ADR

**Files:**
- Test: `tests/test_error_brain_e2e.py`
- Modify: `CONTEXT.md`
- Create: `docs/adr/0012-the-error-brain-classifies-it-does-not-decide.md`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: no new names.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_error_brain_e2e.py
import json

from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def test_a_real_402_through_the_http_surface_is_classified_as_needs_payment_and_persisted(
        tmp_path, monkeypatch):
    async def boom(self, route, messages, **kwargs):
        from flexrouter.client import ProviderError
        raise ProviderError("alpha rejected the request: payment required", status_code=402)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", boom)
    client = _client(tmp_path, monkeypatch)
    client.post("/v1/chat/completions",
               json={"model": "smart", "messages": [{"role": "user", "content": "hi"}]})

    trace_path = tmp_path / "state" / "traces.jsonl"
    entry = json.loads(trace_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["attempts"][0]["verdict"] == "needs_payment"

    brain_path = tmp_path / "state" / "error_brain.json"
    assert brain_path.exists()
    brain = json.loads(brain_path.read_text(encoding="utf-8"))
    assert any(e["verdict"] == "needs_payment" for e in brain.values())
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `python -m pytest tests/test_error_brain_e2e.py -q`
Expected: PASS already, if Tasks 1–4 are correct — this is a proof, not new behaviour.

- [ ] **Step 3: Update `CONTEXT.md`**

Add a glossary entry after "Cooldown / penalty" and before "Permanent vs. transient failure" (check the current file's order and place it where your own read makes clear is right):

```markdown
- **Error brain** — `state/error_brain.json`, a persistent memory of what unfamiliar provider error text means, keyed by a normalized fingerprint (`flexrouter/error_brain.py`: `ErrorBrain`, `fingerprint`, `classify_by_rule`). Built-in rules classify the unambiguous cases instantly (status codes already handled elsewhere in the routing logic, plus a few known substrings); genuinely unrecognized text is fingerprinted, checked against what has already been learned, and — on a real miss — handed to a `Decider` (`flexrouter/decider.py`). This stage ships only `NullDecider`, which always answers `unknown` with zero confidence. **The error brain classifies; it does not decide.** None of the router's existing quarantine/penalize/bench logic changes because of what it says — see ADR 0012. Its output lands in `attempts[].verdict` in the request trace, a field Stage 3 reserved and left `null` until now (ADR 0010).
```

- [ ] **Step 4: Write the ADR**

```markdown
# 0012. The error brain classifies; it does not decide

Date: 2026-09-21
Status: Accepted

## Context

Spec section 4a asks for provider error text to be classified into a small,
typed vocabulary (`too_fast`, `bad_key`, `needs_payment`, `model_gone`,
`their_end_temporary`, `message_too_long`, `bad_request`, `unknown`), with
built-in rules for the unambiguous cases and a small structured-output
model (behind a `Decider` protocol, defaulting to a `NullDecider`) for
everything else. The spec's own table also maps each verdict to a router
action — but by the time this stage started, every one of those actions
for the cases a bare status code already disambiguates was already built,
across Stages 2 through 4, already reviewed: a 401/403 benches the key
that got it (Stage 4), a 402 quarantines the provider
(`ProviderError.is_provider_wide`, `client.py`, frozen), a 404/410
quarantines the route (`ProviderError.is_permanent`, frozen), a 429 cools
the key that hit it (Stage 4). Reimplementing those same decisions behind
a new verdict layer would mean two sources of truth for one fact, or
worse, a subtle divergence between what the classifier says and what the
router actually does.

## Decision

**This stage is observability-only.** `ErrorBrain.classify()` is called
from every failure branch in both `agenerate` and `agenerate_stream`, and
its result is written to `attempts[].verdict` in the request trace — the
field Stage 3 reserved and left `null` (ADR 0010, ruling 5). Nothing else
changes. No call site's existing `quarantine`/`quarantine_provider`/
`penalize`/`mark_benched`/`mark_cooling` decision is touched, conditioned
on, or duplicated by a verdict.

*Cost:* the spec's "Router action" column is only half-implemented — the
classification exists, the action on it (mostly) doesn't, for cases the
router doesn't already handle by status code alone. This is accepted
because acting on a verdict is exactly the kind of routing-behaviour
change that deserves the same task-by-task, reviewed care Stages 2-4 got,
not a rider on a stage whose main job is building the classifier in the
first place.

**The classifier's real value is scoped to exactly the gap in the existing
status-code logic**: a `ProviderError` whose status is not already one of
401/402/403/404/410/429. For every branch where the router already knows
the verdict unambiguously from which `except` clause fired, `_router.py`
passes a fixed, correct status rather than re-deriving one from text — the
`RateLimitError` branch always classifies with `status=429`, the
`RouterError` (auth) branch always with `status=401` (this codebase's
`client.py` raises `RouterError` only for 401 or 403, and both verdicts
are identical — `bad_key` — so 401 is a safe stand-in for either).

**`bad_request` has no built-in rule yet.** The spec says a `bad_request`
verdict should feed `state/model_facts.json` as capability evidence — that
store doesn't exist until Stage 6. A verdict with no consumer isn't worth
manufacturing; an otherwise-unclassified 400-class error falls through to
`unknown` under `NullDecider` instead.

**Fingerprinting happens on already-scrubbed text**, unconditionally,
inside `ErrorBrain.classify()` itself — not trusting any caller to have
scrubbed first, the same defense-in-depth `TraceWriter` already
established (ADR 0010). `state/error_brain.json`'s `sample` field can
never contain anything key-shaped.

**Manual entries are protected even though nothing writes one yet.**
`ErrorBrain._record()` checks `source == "manual"` before ever overwriting
a stored verdict, so when a later stage adds a way to set one (a CLI
command, a dashboard row), the protection is already correct rather than
retrofitted.

**`ModelFacts` stays a plain `dict` on the `Decider` protocol.** Its real
shape (spec §4b) is Stage 6's to define; `describe_model` exists on the
protocol to match the spec's interface exactly, but nothing calls it this
stage.

## Consequences

- A future dashboard (Stage 8) reading `state/error_brain.json` and
  `attempts[].verdict` across traces gets real, typed reasons for
  failures — "why didn't it use the good one" now has an actual taxonomy,
  not just a raw exception string.
- The next stage that needs to *act* on a verdict (most plausibly Stage 6,
  once `state/model_facts.json` exists and `bad_request`/capability
  evidence has somewhere to go) inherits a classifier that already works
  and is already trustworthy — it only has to wire the action, not build
  the classification from scratch.
- `NullDecider` shipping as the only implementation resolves the
  roadmap's Open Q4: nothing in this codebase depends on
  `typesafe/jev-1.13` pricing or access being confirmed.
```

- [ ] **Step 5: Run the whole suite one last time**

Run: `python -m pytest -q`
Expected: 688 + (this stage's new test count) passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add tests/test_error_brain_e2e.py CONTEXT.md docs/adr/0012-the-error-brain-classifies-it-does-not-decide.md
git commit -m "docs(errors): prove the error brain end to end, and record the scope ruling as ADR 0012"
```

---

## After all five tasks

- Run the full suite once more and record the final passed/skipped count.
- File anything discovered but out of scope under `.scratch/v2-stage5-followups/issues/`.
- There is no git remote. Merge this stage's branch into `master` yourself once its final whole-branch review is clean, the same way Stages 3 and 4 did — the owner explicitly asked not to be consulted through the end of Stage 7.
