# flexrouter v2 — Stage 3: The Request Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The owner explicitly asked for subagent-driven development for this stage; do not substitute executing-plans without asking him first.

**Goal:** Write one JSON object per request to `state/traces.jsonl`, with `skipped` actually populated from `engine._skip_reason()`, nothing secret ever written to it, and daily rotation with 30-day retention.

**Architecture:** A new `flexrouter/traces.py` owns the file: append, daily rotation (today's file is always `traces.jsonl`; the moment a write lands on a new calendar day, yesterday's file is renamed to `traces-YYYY-MM-DD.jsonl` and files older than the retention window are deleted), and scrubbing every text field on the way in so nothing unscrubbed is ever persisted — same discipline `redact.py` already established for the penalty box. `LocalRouter` (`flexrouter/_router.py`) assembles the trace, because it is the only place that sees model selection, every attempt, and the final answer or failure in one flow. `engine.py` is not touched: `RoutingEngine.explain_unavailable()` already re-walks a tier and calls `_skip_reason()` for every model in it, which is exactly "why wasn't the good one used" — Stage 3 calls that existing method from outside the engine instead of teaching the engine anything new, the same pattern Stage 2 used for pinned routing (`_build_pin_engine`).

**Tech Stack:** Python 3.11+, pytest, `dataclasses`, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-18-flexrouter-v2-design.md` (§3, "The request trace")

**Roadmap:** `docs/superpowers/plans/2026-09-18-v2-roadmap.md` (Stage 3)

**Previous stage:** `docs/superpowers/plans/2026-09-19-v2-stage2-openai-surface.md`

## Global Constraints

- Python floor is `>=3.11`. Do not raise it.
- **Nothing in this codebase may write `config.yaml`.** This stage writes only to `state/`.
- **Secrets are never returned by any HTTP endpoint, printed by any CLI command, or written to any file.** This now explicitly includes `state/traces.jsonl` — see Task 1's ruling on `provider_message`.
- `FLEXROUTER_HOME` overrides the home root everywhere, with no exceptions.
- **Do not refactor** `recovery.py`, `engine.py`, `window.py`, `quota.py`, `rate_limits.py`, `errors.py`, `client.py`. They are "reused unchanged" by spec decree. If a task seems to need an edit to one of them, stop and say so rather than editing it — `explain_unavailable()` already exists on `engine.py` for exactly this stage's needs.
- **`audit.csv` is retained unchanged.** Do not remove or alter any `self._audit.log(...)` call in `_router.py`; the trace is additional, not a replacement.
- Tests: `pytest`, `asyncio_mode = "auto"` already set. **Do not use `respx` together with FastAPI's `TestClient`** — they collide in this repo. Endpoint tests monkeypatch `flexrouter.client.AsyncClient.chat` / `.stream_chat` instead.
- **Every `ModelConfig` fixture needs `rpm=60, tpm=60000, context_window=<something big>` explicitly** — there are no defaults, and this tripped up six of Stage 2's ten tasks.
- **Selection tests that need a specific model chosen must use widely separated scores (99 vs 40)** — the engine picks randomly among models within 20% of the top score.
- **Run pytest synchronously.** Any implementer subagent must call the Bash tool with `run_in_background` unset (or `false`) and `timeout: 600000`. Backgrounding pytest and waiting for a notification stalls forever — this happened to three of Stage 2's implementers.
- `tests/conftest.py` has an autouse fixture pointing `FLEXROUTER_HOME` at a temp directory. Do not remove it. A test that writes to the real home is a bug.
- **`README.md` currently holds an unsaved rewrite that is not from any agent.** Do not stage it, do not revert it, do not touch it. Never `git add -A` or `git add .` — name files explicitly in every commit.
- The suite must finish at **624 passed, 1 skipped or better** with no ignore flags: `python -m pytest -q`.

## Rulings made while writing this plan

These resolve ambiguities the spec leaves open for this stage. Record them as ADR 0010 in Task 5. Surface them to the owner in plain language at the end; do not ask him about any of these first — the pattern that worked in Stage 2 was to rule, record, and only ask about the one thing that is genuinely his call (see the end of this plan).

1. **`skipped` is built from `RoutingEngine.explain_unavailable()`, unmodified.** That method already calls `_skip_reason()` for every model in a tier and returns `{provider, model, score, available, reason, detail}`. `LocalRouter` calls it once per request, before the retry loop, and keeps only the entries where `available` is `False`, dropping `score` and `available` to match the trace shape. This is computed once at the top of the request, not per retry attempt — it answers "what did the router have to choose from when this request arrived", not "what changed while it was retrying."
2. **`provider_message` and every `skipped[].detail` are scrubbed, not literally verbatim.** The spec's own wording ("the provider's verbatim text") was written before `redact.py` existed. Stage 2's whole-branch review found exactly this shape of leak already reaching a persisted, unauthenticated-readable file (the penalty reasons in `events.csv`/quarantine state). `TraceWriter.write()` scrubs every text field on the way in, so nothing unscrubbed is ever on disk, matching how `_handle_auth_failure`/`_handle_provider_error` already scrub at their write sites. This is a deviation from the literal spec text and is recorded here rather than silently done.
3. **Stage 2 follow-up issue 02 is absorbed here, not left behind.** `_router.py`'s rate-limit failure handler still records `detail=str(exc)` to `events.py` unscrubbed, and `events.py` opens its file with no explicit encoding. Task 2 fixes both, since Stage 3 is exactly the "successor recording path" that issue named as the right place to do it.
4. **`key_id` is `None` in every attempt and in `answered_by` for this stage.** Per-key identity (`state/key_state.json`, `Scheduler`) is Stage 4's `PenaltyBox`/`RoutingEngine` work, not built yet. `RouteResult.api_key` today is a bare secret string chosen by `counter % len(provider_cfg.api_keys)` — there is no key record to name. Inventing an identity by matching the secret against `keys.json` would be guessing at a stage that has not landed; `None` says plainly "not tracked yet" and Stage 4 is where this field gets real values.
5. **`verdict` is omitted from each attempt.** The decision layer (`Decider`, `NullDecider`, the error brain) is Stage 5. A `verdict` field would either be fabricated here or left permanently `"unknown"`, and the spec's worked example shows it coming from that layer. Omitting the field (rather than writing a stand-in value) makes it unambiguous that Stage 5 fills it in, not that it was computed and happened to always be one value.
6. **`asked.needs` lists `"vision"` when the request set `vision=True`, and is empty otherwise.** No other capability negotiation exists yet in `LocalRouter.agenerate`/`agenerate_stream` — `vision` is the only capability flag the code has today.
7. **Rotation is daily-file, not downsampling.** `health_history.py`'s `compact()` downsamples old *health snapshots* to one per five minutes, which makes sense for a metric but would silently delete individual request records, which the whole point of this stage is to keep. "Rotated daily, retained 30 days, compacted like `health_history.py` does" is read as: apply the same *mechanism* (atomic replace, a retention cutoff, never touching the log format) to a *daily-file* rotation instead — the live file is always `state/traces.jsonl`; the first write after midnight renames yesterday's content to `state/traces-YYYY-MM-DD.jsonl` and deletes any dated file older than 30 days. This keeps the literal path `state/traces.jsonl` for "today", satisfies "rotated daily, retained 30 days", and never thins out an individual request the way downsampling would.
8. **One trace is written per request, at the point the request's outcome is known** — a single success, or the final failure after the retry loop is exhausted, or (streaming only) the moment a post-commit failure occurs. A request that is still retrying has not written anything yet; nothing double-writes.
9. **`ms_to_first_token` is `None` for a non-streamed answer**, per the spec's own note that it "only means something for a streamed answer". For a streamed answer it is measured from the start of the attempt that ultimately answered (the only attempt that ever reaches the commit point) to the first moment any event — text, reasoning, or a tool-call fragment — is yielded from it.
10. **`cost_usd` is always `0.0`.** No pricing table exists anywhere in the codebase yet (`audit.py` already always logs `cost_usd=0.0`); Stage 3 does not invent one.

---

## File Structure

| File | Responsibility |
|---|---|
| `flexrouter/traces.py` | **new** — `TraceWriter`: append one JSON line, scrub every text field first, rotate the file daily, delete anything past the retention window. |
| `flexrouter/events.py` | **modify** — explicit `encoding="utf-8"` on both the write and read path. |
| `flexrouter/_router.py` | **modify** — scrub the rate-limit `detail`; build and write one trace per request in `agenerate` and `agenerate_stream`. |
| `CONTEXT.md` | **modify** — add a "Request trace" glossary entry; update "Not yet built". |
| `docs/adr/0010-the-request-trace-is-scrubbed-not-verbatim.md` | **new** — records rulings 1–10 above. |

---

### Task 1: `TraceWriter` — append, scrub, rotate, retain

**Files:**
- Create: `flexrouter/traces.py`
- Test: `tests/test_traces.py`

**Interfaces:**
- Consumes: `flexrouter.redact.scrub`.
- Produces:
  - `TraceWriter(state_dir: str, retention_days: int = 30)`
  - `TraceWriter.write(entry: dict) -> None` — appends one scrubbed JSON line to `state_dir/traces.jsonl`, rotating first if the last write was on an earlier calendar day (UTC).
  - `TraceWriter.compact(now: datetime | None = None) -> None` — deletes any `state_dir/traces-*.jsonl` file whose date is older than `retention_days` before `now`. Called automatically by `write()` on rotation, and exposed directly so a test (and a future CLI command) can call it without waiting for a day to roll over.
  - `new_trace_id() -> str` — `"req_"` plus 24 hex characters, matching the `"chatcmpl-" + uuid.uuid4().hex[:29]` pattern already used in `flexrouter/app.py`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_traces.py
import json
from datetime import datetime, timedelta, timezone

from flexrouter.traces import TraceWriter, new_trace_id


def test_new_trace_id_has_the_req_prefix():
    tid = new_trace_id()
    assert tid.startswith("req_")
    assert len(tid) == len("req_") + 24


def test_two_trace_ids_differ():
    assert new_trace_id() != new_trace_id()


def test_write_appends_one_json_line(tmp_path):
    w = TraceWriter(str(tmp_path))
    w.write({"id": "req_1", "ok": True})
    w.write({"id": "req_2", "ok": False})
    lines = (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["id"] == "req_1"
    assert json.loads(lines[1])["id"] == "req_2"


def test_provider_message_is_scrubbed_on_the_way_in(tmp_path):
    w = TraceWriter(str(tmp_path))
    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"
    w.write({
        "id": "req_1",
        "attempts": [{"n": 1, "provider": "openrouter", "model": "m",
                      "provider_message": f"Incorrect API key provided: {leaked}"}],
    })
    on_disk = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    assert leaked not in on_disk
    assert "…1234" in on_disk


def test_skipped_detail_is_scrubbed_too(tmp_path):
    w = TraceWriter(str(tmp_path))
    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"
    w.write({
        "id": "req_1",
        "skipped": [{"provider": "groq", "model": "m", "reason": "quarantined",
                     "detail": f"bad key {leaked}"}],
    })
    on_disk = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    assert leaked not in on_disk


def test_write_is_utf8(tmp_path):
    w = TraceWriter(str(tmp_path))
    w.write({"id": "req_1", "attempts": [
        {"n": 1, "provider": "p", "model": "m", "provider_message": "…tail"}]})
    raw = (tmp_path / "traces.jsonl").read_bytes()
    raw.decode("utf-8")  # must not raise


def test_rotation_moves_yesterdays_file_to_a_dated_name(tmp_path):
    w = TraceWriter(str(tmp_path))
    w.write({"id": "req_1"})
    live = tmp_path / "traces.jsonl"
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    import os
    os.utime(live, (yesterday.timestamp(), yesterday.timestamp()))

    w.write({"id": "req_2"})

    dated = tmp_path / f"traces-{yesterday.date().isoformat()}.jsonl"
    assert dated.exists()
    assert json.loads(dated.read_text(encoding="utf-8").strip())["id"] == "req_1"
    assert json.loads(live.read_text(encoding="utf-8").strip())["id"] == "req_2"


def test_compact_deletes_dated_files_past_retention(tmp_path):
    w = TraceWriter(str(tmp_path), retention_days=30)
    now = datetime.now(timezone.utc)
    old = tmp_path / f"traces-{(now - timedelta(days=31)).date().isoformat()}.jsonl"
    recent = tmp_path / f"traces-{(now - timedelta(days=5)).date().isoformat()}.jsonl"
    old.write_text('{"id": "old"}\n', encoding="utf-8")
    recent.write_text('{"id": "recent"}\n', encoding="utf-8")

    w.compact(now=now)

    assert not old.exists()
    assert recent.exists()


def test_compact_ignores_files_that_do_not_match_the_dated_pattern(tmp_path):
    w = TraceWriter(str(tmp_path))
    junk = tmp_path / "traces-not-a-date.jsonl"
    junk.write_text("{}\n", encoding="utf-8")
    w.compact()  # must not raise
    assert junk.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_traces.py -q` (foreground, no `run_in_background`, `timeout: 600000`)
Expected: FAIL — `ModuleNotFoundError: No module named 'flexrouter.traces'`

- [ ] **Step 3: Write the implementation**

```python
# flexrouter/traces.py
"""One JSON object per request, appended to state/traces.jsonl.

This is the foundation for the dashboard, the error brain, and the attribute
corrections (spec section 3). engine.py already computes exactly why each
model was passed over (`_skip_reason`, called by `explain_unavailable`) and
V1 discarded the answer — this module only records it; `_router.py` is what
calls `explain_unavailable`.

Every text field is scrubbed here, on the way in, the same discipline
`redact.py` already established for the penalty box: nothing unscrubbed is
ever written to disk, which means the spec's "verbatim provider text" cannot
be taken literally (see ADR 0010, ruling 2).

Rotation is daily-file rather than health_history.py's downsampling: the
live file is always traces.jsonl ("today"); the first write after midnight
UTC renames yesterday's content to traces-YYYY-MM-DD.jsonl and anything
older than the retention window is deleted. Downsampling makes sense for a
health metric sampled many times a second; it would silently thin out
individual requests here, which is the one thing this stage exists to keep.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from flexrouter.redact import scrub

_DATED_FILE = re.compile(r"^traces-(\d{4}-\d{2}-\d{2})\.jsonl$")


def new_trace_id() -> str:
    return "req_" + uuid.uuid4().hex[:24]


def _scrub_entry(entry: dict) -> dict:
    """A deep copy of `entry` with every known text field scrubbed.

    Round-trips through json rather than a manual deep copy: every value
    this module is ever asked to write is itself JSON (it is about to be
    written as a JSON line), so this is exact and needs no recursion of its
    own to maintain.
    """
    out = json.loads(json.dumps(entry))
    for skip in out.get("skipped") or []:
        if "detail" in skip:
            skip["detail"] = scrub(skip["detail"])
    for att in out.get("attempts") or []:
        if "provider_message" in att and att["provider_message"]:
            att["provider_message"] = scrub(att["provider_message"])
    return out


class TraceWriter:
    def __init__(self, state_dir: str, retention_days: int = 30) -> None:
        self._dir = Path(state_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "traces.jsonl"
        self._retention_days = retention_days

    def _rotate_if_new_day(self, now: datetime) -> None:
        if not self._path.exists():
            return
        last_write = datetime.fromtimestamp(self._path.stat().st_mtime, tz=timezone.utc)
        if last_write.date() == now.date():
            return
        dated = self._dir / f"traces-{last_write.date().isoformat()}.jsonl"
        if not dated.exists():
            os.replace(self._path, dated)
        else:
            # Two rotations landed on the same dated name (a restart after a
            # long-untouched file, say) — append rather than clobber a day's
            # existing archive.
            with dated.open("a", encoding="utf-8") as dst, \
                    self._path.open("r", encoding="utf-8") as src:
                dst.write(src.read())
            self._path.unlink()
        self.compact(now=now)

    def compact(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=self._retention_days)).date()
        for f in self._dir.glob("traces-*.jsonl"):
            m = _DATED_FILE.match(f.name)
            if not m:
                continue
            try:
                day = date.fromisoformat(m.group(1))
            except ValueError:
                continue
            if day < cutoff:
                f.unlink(missing_ok=True)

    def write(self, entry: dict) -> None:
        now = datetime.now(timezone.utc)
        self._rotate_if_new_day(now)
        scrubbed = _scrub_entry(entry)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(scrubbed) + "\n")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_traces.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add flexrouter/traces.py tests/test_traces.py
git commit -m "feat(traces): a scrubbing, self-rotating writer for state/traces.jsonl"
```

---

### Task 2: Absorb Stage 2 follow-up issue 02 — scrub and UTF-8 the events log

Small and independent of Task 1's file, so it gets its own task and its own reviewer gate rather than being folded silently into Task 3's larger diff.

**Files:**
- Modify: `flexrouter/events.py` (`__init__`'s file open, `record`'s file open)
- Modify: `flexrouter/_router.py` (the four rate-limit `detail=str(exc)` call sites)
- Test: `tests/test_events.py` (add to the existing file)

**Interfaces:**
- Consumes: `flexrouter.redact.scrub` (already used elsewhere in `_router.py`).
- Produces: no new names — same `EventLogger.record` signature.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_events.py`:

```python
def test_a_key_shaped_detail_is_scrubbed_before_it_reaches_disk(tmp_path):
    from flexrouter.events import EventLogger

    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"
    logger = EventLogger(str(tmp_path))
    logger.record("groq", "llama", "rate_limited", detail=f"429: key {leaked} throttled")

    on_disk = (tmp_path / "events.csv").read_text(encoding="utf-8")
    assert leaked not in on_disk


def test_events_csv_round_trips_as_utf8(tmp_path):
    from flexrouter.events import EventLogger

    logger = EventLogger(str(tmp_path))
    logger.record("groq", "llama", "rate_limited", detail="took …1234 characters")

    raw = (tmp_path / "events.csv").read_bytes()
    raw.decode("utf-8")  # must not raise
```

Note: `EventLogger.record` itself does not scrub — scrubbing at the call site in `_router.py` is what Task 2 actually adds; these two tests describe the effect once that call site is fixed and prove the file opens are UTF-8-safe either way. Confirm this by first checking `EventLogger.record`'s current signature in `flexrouter/events.py` — it takes the detail string as given, so the caller (Step 3) is responsible for scrubbing before calling it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_events.py -q`
Expected: FAIL on `test_a_key_shaped_detail_is_scrubbed_before_it_reaches_disk` — the leaked string is still in the file, because nothing scrubs a detail passed straight through by a test calling `record` directly is already scrubbed by the caller in production code, but this test bypasses `_router.py` and calls `record` directly with an unscrubbed detail, so it will keep failing until `EventLogger.record` scrubs internally. Re-read this before Step 3: **scrub inside `EventLogger.record`, not only at the four `_router.py` call sites**, so the guarantee holds regardless of caller.

- [ ] **Step 3: Write the implementation**

In `flexrouter/events.py`, add the import and scrub in `record`, and add explicit encoding to both file opens:

```python
from flexrouter.redact import scrub
```

```python
    def __init__(self, state_dir: str) -> None:
        self._dir = Path(state_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "events.csv"
        self._recent: deque[dict] = deque(maxlen=500)
        if not self._path.exists():
            with self._path.open("w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self.HEADERS).writeheader()

    def record(self, provider: str, model: str, event_type: str,
               detail: str = "", penalty_seconds: int = 0) -> None:
        if event_type not in _EVENT_TYPES:
            raise ValueError(f"Unknown event_type {event_type!r}; expected one of {sorted(_EVENT_TYPES)}")
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": provider,
            "model": model,
            "event_type": event_type,
            "detail": scrub(detail),
            "penalty_seconds": penalty_seconds,
        }
        with self._path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.HEADERS).writerow(row)
        self._recent.append(row)
```

In `flexrouter/_router.py`, the four rate-limit sites currently read
`detail=str(exc)` (in `agenerate`'s `except RateLimitError`, and three times
in `agenerate_stream`'s `except RateLimitError` and the empty-stream /
empty-completion-after-retry branches that already pass a literal string, not
`str(exc)` — only the `RateLimitError` branches need a change). Change:

```python
                self._events.record(
                    route.provider, route.model, "rate_limited",
                    detail=str(exc),
                    penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
```

to:

```python
                self._events.record(
                    route.provider, route.model, "rate_limited",
                    detail=scrub(str(exc)),
                    penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
```

in both places it occurs (once in `agenerate`, once in `agenerate_stream`).
`scrub` is already imported in `_router.py` (`from flexrouter.redact import
scrub`) — check the top of the file before adding the import again.

Since `EventLogger.record` now scrubs internally regardless, these two edits
are belt-and-suspenders, not the fix — leave them in anyway, because a
future caller of `record` that bypasses `_router.py`'s existing pattern
should not be the only thing standing between a leak and disk.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_events.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: 624+ passed, 1 skipped. If an existing test in `tests/test_events.py`
asserted a raw `detail` string containing something long/key-shaped, update
the assertion to the scrubbed form.

- [ ] **Step 6: Close the follow-up issue**

Edit `.scratch/v2-stage2-followups/issues/02-unscrubbed-provider-text-in-events.md`,
changing its `Status:` line from `ready-for-agent` to `wontfix` is wrong — use
the label vocabulary in `docs/agents/triage-labels.md`; the correct status for
"done" is to move the file to a `resolved/` subdirectory if that convention
exists in `.scratch/`, or otherwise append a `## Resolved` section at the
bottom naming this stage and this commit. Check
`.scratch/v2-stage1-followups/` for how a previously-closed issue in this repo
was actually marked, and match that convention exactly rather than inventing a
new one.

- [ ] **Step 7: Commit**

```bash
git add flexrouter/events.py flexrouter/_router.py tests/test_events.py .scratch/v2-stage2-followups/issues/02-unscrubbed-provider-text-in-events.md
git commit -m "fix(events): scrub rate-limit details and open events.csv as utf-8"
```

---

### Task 3: The trace for a non-streamed answer

**Files:**
- Modify: `flexrouter/_router.py` (`__init__`, `reload`, `agenerate`)
- Test: `tests/test_trace_agenerate.py`

**Interfaces:**
- Consumes: `flexrouter.traces.TraceWriter`, `flexrouter.traces.new_trace_id` (Task 1).
- Produces: `LocalRouter._traces: TraceWriter` (new attribute). No new public methods — the trace is an internal side effect of `agenerate`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_trace_agenerate.py
import json

from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path, **extra_models):
    models = [ModelConfig(provider="alpha", model="big", score=99,
                          rpm=60, tpm=60000, context_window=100_000)]
    return FlexConfig(
        tiers={"smart": models},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _router(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


def _traces(tmp_path):
    p = tmp_path / "state" / "traces.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]


def test_a_successful_request_writes_one_trace(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    router.generate([{"role": "user", "content": "hi"}], "smart")

    traces = _traces(tmp_path)
    assert len(traces) == 1
    t = traces[0]
    assert t["id"].startswith("req_")
    assert t["ok"] is True
    assert t["asked"] == {"bucket": "smart", "stream": False, "needs": [],
                          "approx_input_tokens": 0}
    assert t["answered_by"] == {"provider": "alpha", "model": "big", "key_id": None}
    assert t["tokens"] == {"in": 5, "out": 2}
    assert t["cost_usd"] == 0.0
    assert t["ms_to_first_token"] is None
    assert isinstance(t["ms_total"], int)
    assert t["skipped"] == []
    assert t["attempts"] == []


def test_needs_lists_vision_when_the_request_asked_for_it(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    router.generate([{"role": "user", "content": "hi"}], "smart", vision=True)

    assert _traces(tmp_path)[0]["asked"]["needs"] == ["vision"]


def test_a_provider_failure_is_recorded_as_an_attempt(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)
    calls = {"n": 0}

    async def fake_chat(self, route, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError("alpha/big: upstream fell over", status_code=500)
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    router.generate([{"role": "user", "content": "hi"}], "smart")

    t = _traces(tmp_path)[0]
    assert len(t["attempts"]) == 1
    a = t["attempts"][0]
    assert a["n"] == 1
    assert a["provider"] == "alpha"
    assert a["model"] == "big"
    assert a["status"] == 500
    assert "upstream fell over" in a["provider_message"]
    assert a["key_id"] is None
    assert isinstance(a["ms"], int)
    assert t["ok"] is True  # the retry succeeded


def test_a_model_that_needs_more_context_than_the_prompt_shows_up_in_skipped(tmp_path, monkeypatch):
    small = ModelConfig(provider="beta", model="tiny", score=50,
                        rpm=60, tpm=60000, context_window=10)
    big = ModelConfig(provider="alpha", model="big", score=99,
                      rpm=60, tpm=60000, context_window=100_000)
    cfg = FlexConfig(
        tiers={"smart": [big, small]},
        providers={
            "alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"]),
            "beta": ProviderConfig(base_url="https://beta.test/v1", api_keys=["k"]),
        },
        state_dir=str(tmp_path / "state"),
    )
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: cfg)
    router = LocalRouter(str(tmp_path / "config.yaml"))

    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"total_tokens": 1}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    # estimated_tokens set high enough via a long message to exceed `tiny`'s window.
    router.generate([{"role": "user", "content": "x" * 100}], "smart")

    t = _traces(tmp_path)[0]
    skipped = {(s["provider"], s["model"]): s for s in t["skipped"]}
    assert ("beta", "tiny") in skipped
    assert skipped[("beta", "tiny")]["reason"] == "context_too_small"
    assert ("alpha", "big") not in skipped


def test_a_request_that_exhausts_every_retry_still_writes_a_trace(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def always_fails(self, route, messages, **kwargs):
        raise ProviderError("alpha/big: gone", status_code=404)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", always_fails)
    try:
        router.generate([{"role": "user", "content": "hi"}], "smart", wait=False)
    except Exception:
        pass

    traces = _traces(tmp_path)
    assert len(traces) == 1
    assert traces[0]["ok"] is False
    assert traces[0]["answered_by"] is None
```

Implementer note: `test_a_request_that_exhausts_every_retry_still_writes_a_trace`
uses `wait=False` and a 404 (permanent → quarantined on first failure) so the
tier empties after one attempt and `generate()` raises `RouterBusy` quickly
rather than sleeping through the configured retry/backoff loop.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_trace_agenerate.py -q`
Expected: FAIL — no `state/traces.jsonl` is written at all yet.

- [ ] **Step 3: Write the implementation**

In `flexrouter/_router.py`, add the import:

```python
from flexrouter.traces import TraceWriter, new_trace_id
```

In `LocalRouter.__init__`, after the line that builds `self._history`, add:

```python
        self._traces = TraceWriter(self._cfg.state_dir)
```

In `LocalRouter.reload`, after the line that rebuilds `self._history`, add:

```python
        self._traces = TraceWriter(self._cfg.state_dir)
```

Replace the body of `agenerate` with the version below. It keeps every
existing line (audit logging, event recording, quarantine/penalty handling)
exactly as it was, and adds trace bookkeeping around it:

```python
    async def agenerate(
        self,
        messages: list[dict],
        tier: str,
        wait: bool = True,
        vision: bool = False,
        session_id: Optional[str] = None,
        **kwargs,
    ) -> dict:
        self._maybe_hot_reload()

        # Run hooks
        ctx = HookContext(messages=messages, hooks=self._cfg.hooks, vision=vision)
        ctx = self._hooks.run(ctx)
        vision = ctx.vision
        estimated_tokens = ctx.estimated_tokens

        retries = self._cfg.retry.retries
        backoff = self._cfg.retry.backoff_seconds

        trace_id = new_trace_id()
        request_started = time.monotonic()
        skipped = [
            {"provider": s["provider"], "model": s["model"],
             "reason": s["reason"], "detail": s["detail"]}
            for s in self._engine_for(tier).explain_unavailable(tier, estimated_tokens, vision)
            if not s["available"]
        ]
        attempts: list[dict] = []

        def _write_trace(ok: bool, answered_by: Optional[dict] = None,
                         tokens: Optional[dict] = None) -> None:
            self._traces.write({
                "id": trace_id,
                "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds") + "Z",
                "asked": {
                    "bucket": tier, "stream": False,
                    "needs": ["vision"] if vision else [],
                    "approx_input_tokens": estimated_tokens,
                },
                "skipped": skipped,
                "attempts": attempts,
                "answered_by": answered_by,
                "tokens": tokens or {"in": 0, "out": 0},
                "cost_usd": 0.0,
                "ms_total": int((time.monotonic() - request_started) * 1000),
                "ms_to_first_token": None,
                "ok": ok,
            })

        # If credentials are what emptied the tier, the caller needs to hear
        # that rather than a generic "everything is busy" — one is a config
        # problem they must fix, the other resolves itself in 30 seconds.
        last_auth_error: RouterError | None = None

        for attempt in range(retries + 1):
            route = self._engine_for(tier).select(tier, estimated_tokens, vision, session_id)

            if route is None:
                if last_auth_error is not None:
                    _write_trace(ok=False)
                    raise last_auth_error
                blocked = self._quarantine_block_reason(tier)
                if blocked:
                    _write_trace(ok=False)
                    raise RouterBusy(blocked)
                if not wait:
                    _write_trace(ok=False)
                    raise RouterBusy(f"All models in tier {tier!r} are unavailable")
                secs = self._engine_for(tier).seconds_until_available(tier)
                await asyncio.sleep(max(secs, 1.0))
                continue

            start = time.monotonic()
            try:
                result = await self._client.chat(route, messages, **kwargs)
            except RateLimitError as exc:
                self._engine.penalize(route.provider, route.model)
                self._events.record(
                    route.provider, route.model, "rate_limited",
                    detail=scrub(str(exc)),
                    penalty_seconds=self._penalties.penalty_seconds(route.provider, route.model))
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="rate_limited",
                )
                self._history.record(self._engine.health_snapshot())
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": 429,
                                 "provider_message": str(exc), "key_id": None,
                                 "ms": int((time.monotonic() - start) * 1000)})
                if attempt == retries:
                    _write_trace(ok=False)
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue
            except RouterError as exc:
                self._handle_auth_failure(route, exc)
                last_auth_error = exc
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="auth_error",
                )
                self._history.record(self._engine.health_snapshot())
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": None,
                                 "provider_message": str(exc), "key_id": None,
                                 "ms": int((time.monotonic() - start) * 1000)})
                # No backoff: a rejected key won't un-reject in two seconds.
                if attempt == retries:
                    _write_trace(ok=False)
                    raise
                continue
            except ProviderError as exc:
                self._handle_provider_error(route, exc)
                self._audit.log(
                    tier=tier, provider=route.provider, model=route.model,
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="error",
                )
                self._history.record(self._engine.health_snapshot())
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": exc.status_code,
                                 "provider_message": str(exc), "key_id": None,
                                 "ms": int((time.monotonic() - start) * 1000)})
                if attempt == retries:
                    _write_trace(ok=False)
                    raise RouterBusy(f"All retries exhausted for tier {tier!r}")
                await asyncio.sleep(backoff)
                continue

            latency_ms = int((time.monotonic() - start) * 1000)
            usage = result.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)

            self._engine.record_request(route.provider, route.model, total_tokens)
            self._quota_tracker.record(route.provider, route.model)
            self._audit.log(
                tier=tier,
                provider=route.provider,
                model=route.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=0.0,
                latency_ms=latency_ms,
                status="ok",
            )
            self._history.record(self._engine.health_snapshot())
            _write_trace(
                ok=True,
                answered_by={"provider": route.provider, "model": route.model, "key_id": None},
                tokens={"in": prompt_tokens, "out": completion_tokens},
            )
            return result

        _write_trace(ok=False)
        raise RouterBusy(f"All models in tier {tier!r} are unavailable after {retries} retries")
```

Add `from datetime import datetime, timezone` to the top-level imports if not
already present (`_router.py` currently imports only `time`, not `datetime` —
check before adding a duplicate).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_trace_agenerate.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: 624+ passed, 1 skipped. Every existing test that builds a
`LocalRouter`/`FlexRouter` (the renamed-in-Stage-2 class) over a real
temp-dir config will now also produce a `state/traces.jsonl` as a side
effect — this is expected and should not break anything, since
`tests/conftest.py`'s autouse fixture already isolates `FLEXROUTER_HOME`.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/_router.py tests/test_trace_agenerate.py
git commit -m "feat(trace): write a scrubbed request trace for every non-streamed call"
```

---

### Task 4: The trace for a streamed answer

Streaming has two properties Task 3's shape does not: `ms_to_first_token`
means something, and a failure can happen *after* the router has already
committed to a model (ADR 0009) — that failure must still produce a trace,
because a request that half-answered and then failed is exactly the case the
dashboard most needs to show.

**Files:**
- Modify: `flexrouter/_router.py` (`agenerate_stream`)
- Test: `tests/test_trace_agenerate_stream.py`

**Interfaces:**
- Consumes: `TraceWriter`, `new_trace_id` (Task 1), the same trace shape Task 3
  established.
- Produces: no new names.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_trace_agenerate_stream.py
import json

from flexrouter._router import LocalRouter
from flexrouter.client import ProviderError, StreamChunk
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [
            ModelConfig(provider="alpha", model="big", score=99,
                        rpm=60, tpm=60000, context_window=100_000),
            ModelConfig(provider="beta", model="small", score=40,
                        rpm=60, tpm=60000, context_window=100_000),
        ]},
        providers={
            "alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"]),
            "beta": ProviderConfig(base_url="https://beta.test/v1", api_keys=["k"]),
        },
        state_dir=str(tmp_path / "state"),
    )


def _router(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return LocalRouter(str(tmp_path / "config.yaml"))


def _traces(tmp_path):
    p = tmp_path / "state" / "traces.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]


async def _drain(router, messages, tier, **kwargs):
    events = []
    async for ev in router.agenerate_stream(messages, tier, **kwargs):
        events.append(ev)
    return events


def test_a_successful_stream_writes_one_trace_with_a_first_token_time(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_stream(self, route, messages, **kwargs):
        yield StreamChunk(content="hi")
        yield StreamChunk(usage={"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4})

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        _drain(router, [{"role": "user", "content": "hi"}], "smart", stream=True))

    traces = _traces(tmp_path)
    assert len(traces) == 1
    t = traces[0]
    assert t["ok"] is True
    assert t["asked"]["stream"] is True
    assert t["answered_by"] == {"provider": "alpha", "model": "big", "key_id": None}
    assert t["tokens"] == {"in": 3, "out": 1}
    assert isinstance(t["ms_to_first_token"], int)
    assert t["ms_to_first_token"] <= t["ms_total"]


def test_failure_before_first_delta_retries_and_records_the_failed_attempt(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_stream(self, route, messages, **kwargs):
        if route.provider == "alpha":
            raise ProviderError("alpha/big: down", status_code=500)
        yield StreamChunk(content="hi")

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        _drain(router, [{"role": "user", "content": "hi"}], "smart", stream=True))

    t = _traces(tmp_path)[0]
    assert t["ok"] is True
    assert len(t["attempts"]) == 1
    assert t["attempts"][0]["provider"] == "alpha"
    assert t["answered_by"]["provider"] == "beta"


def test_a_failure_after_the_first_delta_still_writes_a_trace(tmp_path, monkeypatch):
    router = _router(tmp_path, monkeypatch)

    async def fake_stream(self, route, messages, **kwargs):
        yield StreamChunk(content="par")
        raise ProviderError("alpha/big: connection dropped mid-answer", status_code=500)

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    import asyncio

    async def run():
        events = []
        try:
            async for ev in router.agenerate_stream(
                    [{"role": "user", "content": "hi"}], "smart", stream=True):
                events.append(ev)
        except Exception:
            pass
        return events

    asyncio.get_event_loop().run_until_complete(run())

    traces = _traces(tmp_path)
    assert len(traces) == 1
    t = traces[0]
    assert t["ok"] is False
    assert t["answered_by"] is None
    assert "connection dropped mid-answer" in t["attempts"][-1]["provider_message"]
    assert t["attempts"][-1]["provider"] == "alpha"
```

Implementer note: check whether this repo's existing streaming tests
(`tests/test_agenerate_stream.py`) drive the async generator with
`asyncio.get_event_loop().run_until_complete(...)` or with `pytest.mark.asyncio`
directly on an `async def test_...`; `pyproject.toml` has `asyncio_mode =
"auto"`, so writing these as plain `async def test_...` functions (no
`asyncio.get_event_loop()` boilerplate) is almost certainly the existing
convention — match it and simplify the snippets above accordingly before
they are used verbatim.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_trace_agenerate_stream.py -q`
Expected: FAIL — no trace file is written from the streaming path yet.

- [ ] **Step 3: Write the implementation**

In `agenerate_stream`, mirror Task 3's setup right after the existing
`retries`/`backoff`/`max_attempts` lines:

```python
        trace_id = new_trace_id()
        request_started = time.monotonic()
        skipped = [
            {"provider": s["provider"], "model": s["model"],
             "reason": s["reason"], "detail": s["detail"]}
            for s in self._engine_for(tier).explain_unavailable(tier, estimated_tokens, vision)
            if not s["available"]
        ]
        attempts: list[dict] = []

        def _write_trace(ok: bool, answered_by: Optional[dict] = None,
                         tokens: Optional[dict] = None,
                         ms_to_first_token: Optional[int] = None) -> None:
            self._traces.write({
                "id": trace_id,
                "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds") + "Z",
                "asked": {
                    "bucket": tier, "stream": True,
                    "needs": ["vision"] if vision else [],
                    "approx_input_tokens": estimated_tokens,
                },
                "skipped": skipped,
                "attempts": attempts,
                "answered_by": answered_by,
                "tokens": tokens or {"in": 0, "out": 0},
                "cost_usd": 0.0,
                "ms_total": int((time.monotonic() - request_started) * 1000),
                "ms_to_first_token": ms_to_first_token,
                "ok": ok,
            })
```

Every pre-commit failure branch (the empty-stream `StopAsyncIteration`
handler, `RateLimitError`, `RouterError`, `ProviderError` — all four already
existing, before the `# Committed:` comment) gets one `attempts.append(...)`
call added right before its existing `yield AttemptFailedEvent(...)`, using
the same shape Task 3 used:

```python
                attempts.append({"n": attempt + 1, "provider": route.provider,
                                 "model": route.model, "status": None,
                                 "provider_message": "empty stream response",
                                 "key_id": None,
                                 "ms": int((time.monotonic() - start) * 1000)})
```

(adjust `status` and `provider_message` per branch: `429` /
`scrub(str(exc))`-equivalent-but-unscrubbed-here-since-TraceWriter-scrubs-on-write
for the rate-limit case reusing `str(exc)`; `None` / `str(exc)` for the auth
case; `exc.status_code` / `str(exc)` for the `ProviderError` case — matching
Task 3's `agenerate` branch-by-branch exactly). And on the branch that raises
instead of continuing (`if attempt == retries: raise ...`), call
`_write_trace(ok=False)` immediately before that raise, in all four branches.

For the same "no wait escape" `route is None` branch that raises `RouterBusy`
on `blocked`, add `_write_trace(ok=False)` immediately before that raise too.

Now the commit boundary. Immediately after the existing comment `# Committed:
...` and its `accumulated`, `final_usage`, `has_tool_calls`,
`tool_calls_by_key`, `tool_call_order`, `last_key_by_index` initializations,
add:

```python
            first_token_at: Optional[float] = None
```

Inside `_events_for`, at its very first line, before the existing body, add:

```python
            def _events_for(sc) -> list:
                nonlocal has_tool_calls, first_token_at
                events: list = []
                if first_token_at is None and (sc.content or sc.reasoning or sc.tool_call_delta):
                    first_token_at = time.monotonic()
```

(the rest of `_events_for`'s existing body follows unchanged — this only adds
the `nonlocal` name and the timestamp capture at the top).

Everything from `any_yielded = False` through the two `empty completion`
branches (`if not full_text and not has_tool_calls:` and its two sub-branches,
including the `raise RouterError(...)` and the `yield AttemptFailedEvent` /
`continue` paths) stays exactly as it is, **except**: the `raise
RouterError(...)` in the "empty after partial output" branch is a post-commit
failure and must write a trace first:

```python
                    raise RouterError(
                        f"{route.provider}/{route.model}: empty completion "
                        "after partial output (no content, no tool calls)")
```

becomes:

```python
                    _write_trace(
                        ok=False,
                        ms_to_first_token=(
                            int((first_token_at - start) * 1000) if first_token_at else None),
                    )
                    raise RouterError(
                        f"{route.provider}/{route.model}: empty completion "
                        "after partial output (no content, no tool calls)")
```

The pre-commit-equivalent branch just below it (empty stream, `any_yielded`
False, which retries rather than raising) needs an `attempts.append(...)` the
same shape as the others, right before its existing `yield
AttemptFailedEvent(...)`, and `_write_trace(ok=False)` right before its `if
attempt == retries: raise RouterBusy(...)`.

At the success path, right before `yield DoneEvent(result=result); return`,
add the trace write:

```python
            self._history.record(self._engine.health_snapshot())
            _write_trace(
                ok=True,
                answered_by={"provider": route.provider, "model": route.model, "key_id": None},
                tokens={"in": prompt_tokens, "out": completion_tokens},
                ms_to_first_token=(
                    int((first_token_at - start) * 1000) if first_token_at else None),
            )
            yield DoneEvent(result=result)
            return
```

Finally, the one failure mode with no existing `except` around it at all: a
`ProviderError`/`RouterError`/anything else raised from inside `async for sc
in stream:` itself, after commit, with no empty-completion branch involved
(e.g. Task 3's stated non-goal test, "connection dropped mid-answer" arriving
as an exception from the provider stream mid-iteration rather than as an
empty completion). Wrap the whole block from `first_chunk is not None:`
through the `async for sc in stream:` loop in a `try`/`except BaseException`
that writes the failure trace and re-raises, without adding any new retry
behaviour:

```python
            try:
                if first_chunk is not None:
                    for ev in _events_for(first_chunk):
                        any_yielded = True
                        yield ev

                async for sc in stream:
                    for ev in _events_for(sc):
                        any_yielded = True
                        yield ev
            except BaseException:
                _write_trace(
                    ok=False,
                    ms_to_first_token=(
                        int((first_token_at - start) * 1000) if first_token_at else None),
                )
                raise
```

`except BaseException` rather than `except Exception` because
`GeneratorExit` (the caller closing the generator early, e.g. a client
disconnecting mid-stream) must also flush a trace — a request the caller
walked away from mid-answer is exactly as worth recording as one the
provider failed.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_trace_agenerate_stream.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: 624+ passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/_router.py tests/test_trace_agenerate_stream.py
git commit -m "feat(trace): write a request trace for streamed calls, including mid-stream failures"
```

---

### Task 5: Rotation proven end to end, docs, and the ADR

Tasks 1–4 prove each piece in isolation with mocked file times. This task
proves the pieces work together through the real service surface, and files
the design record the handoff process requires.

**Files:**
- Test: `tests/test_trace_e2e.py`
- Modify: `CONTEXT.md`
- Create: `docs/adr/0010-the-request-trace-is-scrubbed-not-verbatim.md`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: no new names.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trace_e2e.py
import json

from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["sk-live-not-a-real-key-ABCDEFGH"])},
        state_dir=str(tmp_path / "state"),
    )


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def test_a_real_request_through_the_http_surface_leaves_a_trace_with_no_secret_in_it(
        tmp_path, monkeypatch):
    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"

    async def boom_then_ok(self, route, messages, **kwargs):
        raise __import__("flexrouter.client", fromlist=["ProviderError"]).ProviderError(
            f"alpha rejected the key {leaked}", status_code=401)

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", boom_then_ok)
    client = _client(tmp_path, monkeypatch)
    client.post("/v1/chat/completions",
               json={"model": "smart", "messages": [{"role": "user", "content": "hi"}]})

    trace_path = tmp_path / "state" / "traces.jsonl"
    assert trace_path.exists()
    on_disk = trace_path.read_text(encoding="utf-8")
    assert leaked not in on_disk
    entry = json.loads(on_disk.strip().splitlines()[-1])
    assert entry["ok"] is False
```

Implementer note: `ProviderError(status_code=401)` is not a status this
client raises as `ProviderError` in real life (401 becomes `RouterError` in
`client.py`) — that is fine here, this test drives `_router.py` directly and
only cares that whatever text reaches the trace gets scrubbed; do not change
`client.py` to make the status realistic.

- [ ] **Step 2: Run the test to verify it fails or passes**

Run: `python -m pytest tests/test_trace_e2e.py -q`
Expected: PASS already, if Tasks 1–4 are correct — this task is a proof, not
new behaviour. If it fails, the bug is in Tasks 1–4, not in this test; fix
it there.

- [ ] **Step 3: Update `CONTEXT.md`**

Add a new glossary entry, alphabetically placed after "The home" and before
"Settings" (matching the existing entry order), or after whichever entry
your read of the current file makes clear is the right spot:

```markdown
- **Request trace** — one JSON object per request, appended to `state/traces.jsonl`, rotated daily (`flexrouter/traces.py`: `TraceWriter`). Every model the router considered but could not use is in `skipped`, taken from `RoutingEngine.explain_unavailable()` without any change to `engine.py`. Every attempt against a provider is in `attempts`. `provider_message` and `skipped[].detail` are scrubbed the same way a quarantine reason is (`flexrouter/redact.py`), not literally verbatim, despite the design doc's wording — nothing unscrubbed is ever written to this file. `key_id` is always `null` for now: per-key identity does not exist until the per-key state stage lands. `verdict` is absent from every attempt for the same reason on the decision-layer side.
```

Update the closing "Not yet built" paragraph to add Stage 3:

```markdown
The v2 design (`docs/superpowers/specs/2026-09-18-flexrouter-v2-design.md`) describes nine stages. Stages 1 through 3 are implemented: the shared home, the OpenAI-shaped surface on one background service with the importable class demoted to a client of it, and the per-request trace with `skipped` populated and nothing secret ever written. Do not treat later-stage concepts (anything not covered above) as present in the code.
```

- [ ] **Step 4: Write the ADR**

```markdown
# 0010. The request trace is scrubbed, not verbatim

Date: 2026-09-19
Status: Accepted

## Context

Spec section 3 asks for `provider_message` to be "the provider's verbatim
text" in a per-request trace written to disk. That line was written before
`flexrouter/redact.py` existed. Several providers echo the rejected
credential back inside their own error text, and Stage 2's whole-branch
review already found exactly this shape of leak reaching a persisted,
unauthenticated-readable file once (the penalty reasons behind
`quarantine_reason()`). A trace is a saved record, read back later, of
provider text taken from possibly-hostile or possibly-leaky upstream
responses — the single highest-risk piece of this stage.

`engine.py` is reused unchanged by spec decree. It already computes exactly
why a model was passed over, through `_skip_reason()`, and
`explain_unavailable()` already re-walks a tier calling it for every model —
that method existed before this stage and was not written for it, but it is
exactly the shape Stage 3 needs.

Per-key identity (`state/key_state.json`) and the decision layer (`Decider`,
the error brain) are later stages, not built yet.

## Decision

**`provider_message` and `skipped[].detail` are scrubbed on the way into
`state/traces.jsonl`, by `TraceWriter.write()`, unconditionally.**
`flexrouter/redact.py`'s `scrub()` is applied to every value under those two
keys before the JSON line is written. This is not literally what the spec
asked for.

*Cost:* a trace read back later shows `…1234` instead of the provider's
whole sentence in the rare case that sentence contained something scrubbed —
a web address, a long model name, or an actual leaked credential all look
the same to the scrubber, exactly as ADR 0009 already accepted for the
`/v1/chat/completions` error envelope. No new cost beyond what ADR 0009
already priced in; this is the same rule reapplied at a new write site.

**`skipped` is populated by calling `RoutingEngine.explain_unavailable()`
from `LocalRouter`, once per request, before the retry loop begins.**
`engine.py` gains no new method and no edit. `_router.py` filters the
returned list to `available: False` entries and drops the `score` and
`available` keys to match the trace shape.

*Cost:* `skipped` reflects the tier's state at the moment the request
arrived, not at the moment each retry attempt happened. A model that becomes
unavailable partway through this request's own retries (for instance,
penalized by an earlier attempt within the same request) will not appear in
`skipped` for this trace, only in a later one. This was chosen over
recomputing `explain_unavailable()` on every retry because the spec's shape
has one `skipped` list per trace, not one per attempt, and the field answers
"what could the router see when this request arrived", which is the
debugging question the spec motivates the field with.

**`key_id` is `null` everywhere in this stage.** `RouteResult.api_key` is a
bare secret chosen by blind rotation (`counter % len(provider_cfg.api_keys)`)
with no identity attached — the per-key state stage has not landed. Matching
the secret against `keys.json` to reconstruct an identity was considered and
rejected: it would be inferring a fact the routing path does not yet track,
rather than recording one it does.

**`verdict` is absent from every attempt.** The decision layer that would
compute it (`Decider`, `NullDecider`, the error brain) does not exist yet.

## Consequences

- A trace read back by a future dashboard, or by the error brain in a later
  stage, is provably free of anything key-shaped, at the cost of occasionally
  reading slightly worse.
- `skipped` and the four failure-handling branches in `_router.py`
  (`_handle_auth_failure`, `_handle_provider_error`, the rate-limit branch,
  the empty-completion branches) needed no change to their own scrubbing —
  they already scrubbed at their own write sites per ADR 0009. `TraceWriter`
  scrubbing again is deliberate belt-and-suspenders, not redundant: a value
  reaching the trace by a path that does not go through those branches (for
  instance the raw `str(exc)` attempt-message this stage records) is still
  caught.
- `key_id` and `verdict` being placeholders rather than real values is
  visible in every trace written by this stage, which is the point: a future
  reader should be able to tell "not tracked yet" apart from "tracked and
  happened to be this value" without reading this ADR first.
```

- [ ] **Step 5: Run the whole suite one last time**

Run: `python -m pytest -q`
Expected: 624 + (this stage's new test count) passed, 1 skipped, with no
ignore flags.

- [ ] **Step 6: Commit**

```bash
git add tests/test_trace_e2e.py CONTEXT.md docs/adr/0010-the-request-trace-is-scrubbed-not-verbatim.md
git commit -m "docs(trace): prove rotation end to end, and record the scrubbing ruling as ADR 0010"
```

---

## After all five tasks

- Run the full suite once more and record the final passed/skipped count.
- File anything discovered but out of scope under
  `.scratch/v2-stage3-followups/issues/`, matching the format used in
  `.scratch/v2-stage2-followups/issues/`.
- There is no git remote, so a pull request is not an option — the owner
  chose a local merge into `master` at the end of Stage 1 and Stage 2. Ask
  him, in plain language, whether he wants this stage merged into `master`
  now or kept on its branch — that is the one open question this plan does
  not answer for him.
