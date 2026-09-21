# flexrouter v2 — Stage 7: Startup Catalogue Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. The owner explicitly asked for subagent-driven development and explicitly asked not to be consulted for input through the end of Stage 7 — rule on every ambiguity yourself and record it, exactly as Stages 3-6 did.

**Goal:** Once per service startup, per provider, check what models a provider actually has and record what changed — models that appeared, models that vanished — building on the catalogue-diffing machinery Stages 1-2 already built and fully tested (`flexrouter/refresh.py`'s `refresh_config()`, already called manually from the CLI and the dashboard). This stage adds the one thing that was missing: an automatic trigger, once per service start, with no owner action required. Newly appeared models also get a `state/model_facts.json` entry recording their published context window (Stage 6 dependency), so a future dashboard has something real to show for them immediately.

**Architecture:** `refresh_config()` is not touched — it is already correct, already tested, already the thing both the CLI and the dashboard call. This stage calls it once, synchronously, from `LocalRouter.__init__`, wrapped so a failure never prevents the service from starting (the same "never fail on this" discipline `_maybe_hot_reload` already established for config reloads). Cadence: the spec originally said "once a day"; the owner changed this to "once per service startup" (recorded in the roadmap, 2026-09-20) because a background timer only matters for a service left running unattended, and this one is something he starts and stops himself.

**Deliberate scope cut, and why:** spec §6 also describes an *apply* mechanism — accepting a pending appeared model into a bucket, or accepting a vanished model's removal, with a per-provider auto-apply setting. Building that safely means giving `overrides.json` a genuinely new capability it does not have today (injecting a brand-new model identity, not just patching an existing one's fields — `flexrouter/overrides.py`'s `apply_overrides` today can only touch models `config.yaml` already declares), and it is a change that can alter which models are actually selectable — the same category of risk Stage 6 deferred "let selection consult model_facts" over, and for the same reason: a routing-affecting change deserves its own dedicated, carefully reviewed stage, not a rider on the stage that builds the automatic trigger. This plan builds the safe, always-on default half (find and record) and files the accept/apply half as a follow-up — see ruling 5.

**Tech Stack:** Python 3.11+, pytest, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-18-flexrouter-v2-design.md` (§6, "Catalogue refresh")

**Roadmap:** `docs/superpowers/plans/2026-09-18-v2-roadmap.md` (Stage 7; also the 2026-09-20 owner decision changing "daily" to "on startup")

**Previous stage:** `docs/superpowers/plans/2026-09-21-v2-stage6-model-facts.md`

## Global Constraints

- Python floor is `>=3.11`. Do not raise it.
- **Nothing in this codebase may write `config.yaml`.** `refresh_config()` already guarantees this; this stage adds no new writer of it.
- **`flexrouter/refresh.py`, `flexrouter/catalogue.py`, `flexrouter/probe.py` are not modified in this stage.** They are already correct and already tested (`tests/test_refresh.py`, `tests/test_catalogue.py`, `tests/test_probe.py`). This stage only calls `refresh_config()` from a new place.
- **Do not touch `engine.py`, `recovery.py`, `window.py`, `quota.py`, `rate_limits.py`, `errors.py`, `client.py`.**
- **A catalogue refresh failure must never prevent the service from starting or fail a request.** Matches `_maybe_hot_reload`'s established discipline for config reload failures.
- Tests: `pytest`, `asyncio_mode = "auto"` already set. **Do not use `respx` together with FastAPI's `TestClient`.**
- **Every `ModelConfig` fixture needs `rpm=60, tpm=60000, context_window=<something big>` explicitly.**
- **Run pytest synchronously.** Bash tool, `run_in_background` unset (never `true`), `timeout: 600000`. Never background pytest and wait for a notification — this has stalled multiple prior implementers on this project, including several on the immediately preceding stages.
- `tests/conftest.py` has an autouse fixture pointing `FLEXROUTER_HOME` at a temp directory. Do not remove it.
- **README.md currently holds an unsaved rewrite that is not from any agent.** Do not stage it, do not revert it, do not touch it. Never `git add -A` or `git add .` — name files explicitly.
- **If you ever see a file you're editing change content you didn't just write yourself, stop and report it immediately.**
- The suite must finish at **735 passed, 1 skipped or better** with no ignore flags: `python -m pytest -q`.

## Rulings made while writing this plan

Record these as ADR 0014 in the final task.

1. **`refresh_config()` runs once, synchronously, at the end of `LocalRouter.__init__`** — not in a background thread, not deferred to first request. It already makes its own per-provider HTTP calls and already tolerates a provider being unreachable (excludes it from comparison rather than reporting "everything vanished"); running it synchronously simply means service startup takes a little longer, once, which is the accepted cost of "check on startup" replacing a background timer (the owner's own 2026-09-20 decision already priced this in).
2. **A refresh failure at startup is caught, logged, and otherwise ignored** — it must never raise out of `__init__` and prevent the service from starting. `refresh_config()` already handles a single provider failing internally; this ruling covers the outer, unexpected case (e.g. the state directory becoming briefly unwritable) the same way `_maybe_hot_reload` already treats a reload failure as "keep serving what we had."
3. **Newly appeared models get exactly one fact recorded: their published context window**, via a new `ModelFactsStore.record_discovered()` method. Nothing else in `state/model_facts.json`'s worked example (vision/tools/reasoning/size_class/provisional_score) is populated by this stage — those need either real evidence from traffic (Stage 6, already built, unrelated to catalogue discovery) or a real classifier (`Decider`, still `NullDecider` — resolves to nothing) to fill in. Context window is the one field the spec explicitly calls "always" published, and `refresh_config()`'s own `appeared` entries already carry it (`{"model": ..., "context_window": ..., ...}`), so recording it costs nothing and is genuinely useful.
4. **`record_discovered()` never overwrites an existing `context` fact.** A model can appear in a catalogue refresh more than once across service restarts (it was already known, briefly vanished, reappeared) — the first-seen context value is treated as authoritative and left alone rather than churned on every restart.
5. **The apply/accept mechanism — turning a pending appeared model into a selectable one, or a pending vanished model into an accepted removal — is deferred, filed as a follow-up, not built in this stage.** It requires a new `overrides.json` capability (injecting a brand-new model identity, which `overrides.py`'s `apply_overrides` cannot do today — it only patches fields on models `config.yaml` already declares) and it is a change that can alter what the router actually serves, the same category of risk Stage 6 deferred consulting `model_facts.json` from selection over. `state/catalog_pending.json` (already written by the unmodified `refresh_config()`) is where this evidence already lives, ready for that follow-up — or for Stage 8's dashboard, which needs an accept/reject UI for exactly this data anyway and is a more natural place for the interaction design than a backend-only stage.
6. **No new CLI command is added in this stage.** `flexrouter refresh` (manual, existing) and the automatic startup trigger (new, this stage) are the only two ways a refresh runs. A CLI command to *apply* a pending change is part of ruling 5's deferred scope.

---

## File Structure

| File | Responsibility |
|---|---|
| `flexrouter/model_facts.py` | **modify** — one new method, `ModelFactsStore.record_discovered()`. |
| `flexrouter/_router.py` | **modify** — call `refresh_config()` once at the end of `__init__`, record discovered models' context windows, never let failure propagate. |

---

### Task 1: `ModelFactsStore.record_discovered()`

**Files:**
- Modify: `flexrouter/model_facts.py`
- Test: additions to `tests/test_model_facts.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ModelFactsStore.record_discovered(provider: str, model: str, context_window: Optional[int]) -> None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_model_facts.py`:

```python
def test_record_discovered_sets_a_published_context_fact(tmp_path):
    from flexrouter.model_facts import ModelFactsStore
    store = ModelFactsStore(str(tmp_path))
    store.record_discovered("openrouter", "new-model", context_window=131072)
    facts = store.get("openrouter", "new-model")
    assert facts.context.value == 131072
    assert facts.context.source == "published"


def test_record_discovered_with_no_context_window_does_nothing(tmp_path):
    from flexrouter.model_facts import ModelFactsStore
    store = ModelFactsStore(str(tmp_path))
    store.record_discovered("openrouter", "new-model", context_window=None)
    facts = store.get("openrouter", "new-model")
    assert facts.context is None


def test_record_discovered_never_overwrites_an_existing_context_fact(tmp_path):
    from flexrouter.model_facts import ModelFactsStore
    store = ModelFactsStore(str(tmp_path))
    store.record_discovered("openrouter", "m", context_window=8192)
    store.record_discovered("openrouter", "m", context_window=999999)  # a later, different refresh
    facts = store.get("openrouter", "m")
    assert facts.context.value == 8192  # first-seen value kept
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_model_facts.py -k record_discovered -q`
Expected: FAIL — `AttributeError: 'ModelFactsStore' object has no attribute 'record_discovered'`

- [ ] **Step 3: Write the implementation**

In `flexrouter/model_facts.py`, add a method to `ModelFactsStore` (place it near `record_success`/`record_contradicting_failure`):

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_model_facts.py -q`
Expected: PASS, all tests including the 3 new ones.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: 735+ passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/model_facts.py tests/test_model_facts.py
git commit -m "feat(facts): record a newly-discovered model's published context window"
```

---

### Task 2: Wire automatic startup catalogue refresh

**Files:**
- Modify: `flexrouter/_router.py` (`__init__` only)
- Test: `tests/test_startup_refresh.py`

**Interfaces:**
- Consumes: `flexrouter.refresh.refresh_config` (pre-existing, unmodified), `RefreshResult` (pre-existing — `.added: list[str]`, each entry `"provider/model"`), `flexrouter.model_facts.ModelFactsStore.record_discovered` (Task 1).
- Produces: no new names — this is a call added inside an existing method.

**Read the current content of `flexrouter/_router.py`'s `__init__` in full before editing** — `self._model_facts = ModelFactsStore(self._cfg.state_dir)` is the last of the state-store constructions (line 111 as of this branch's base); your new code goes after it, before the sampler starts.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_startup_refresh.py
import json

from flexrouter._router import LocalRouter
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000, context_window=100_000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def test_startup_calls_refresh_and_writes_catalog_pending(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    calls = []

    def fake_refresh_config(config_path, state_dir, aa_key=None):
        calls.append((config_path, state_dir))
        from flexrouter.refresh import RefreshResult
        return RefreshResult(timestamp="2026-09-21T00:00:00Z", added=[], removed=[],
                             changed=[], provider_errors=[], pending_path=None)

    monkeypatch.setattr("flexrouter._router.refresh_config", fake_refresh_config)
    router = LocalRouter(str(tmp_path / "config.yaml"))
    router.close()

    assert len(calls) == 1
    assert calls[0][1] == str(tmp_path / "state")


def test_startup_refresh_records_model_facts_for_appeared_models(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    def fake_refresh_config(config_path, state_dir, aa_key=None):
        from flexrouter.refresh import RefreshResult
        # Write the same shape refresh_config itself would, so record_discovered
        # has something real to read.
        import os
        pending = {"alpha": {"checked_at": "2026-09-21T00:00:00Z",
                             "appeared": [{"model": "new-model", "context_window": 131072,
                                          "rpm": None, "tpm": None, "score": 50, "free": True}],
                             "vanished": [], "changed": []}}
        os.makedirs(state_dir, exist_ok=True)
        with open(os.path.join(state_dir, "catalog_pending.json"), "w", encoding="utf-8") as f:
            json.dump(pending, f)
        return RefreshResult(timestamp="2026-09-21T00:00:00Z", added=["alpha/new-model"],
                             removed=[], changed=[], provider_errors=[], pending_path=None)

    monkeypatch.setattr("flexrouter._router.refresh_config", fake_refresh_config)
    router = LocalRouter(str(tmp_path / "config.yaml"))
    router.close()

    facts = router._model_facts.get("alpha", "new-model")
    assert facts.context.value == 131072
    assert facts.context.source == "published"


def test_a_refresh_failure_at_startup_does_not_prevent_the_service_from_starting(
        tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    def broken_refresh_config(config_path, state_dir, aa_key=None):
        raise RuntimeError("state directory briefly unwritable")

    monkeypatch.setattr("flexrouter._router.refresh_config", broken_refresh_config)
    router = LocalRouter(str(tmp_path / "config.yaml"))  # must not raise
    router.close()
```

Implementer note: reading back `state/catalog_pending.json` to find appeared models' context windows (rather than trusting `RefreshResult.added` alone, which is just a list of identity strings with no context window) is the real design — `refresh_config()` already wrote that file with the full appeared-entry shape (`{"model": ..., "context_window": ..., ...}`) before returning. Read the current `flexrouter/refresh.py` to confirm the exact on-disk shape (`{provider: {"appeared": [{"model": ..., "context_window": ..., ...}], ...}}`) before writing the implementation — this plan's earlier research confirmed it, but verify against the live file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_startup_refresh.py -q`
Expected: FAIL — `refresh_config` is never called from `_router.py` yet.

- [ ] **Step 3: Write the implementation**

Add the import at the top of `flexrouter/_router.py`:

```python
from flexrouter.refresh import refresh_config
```

In `LocalRouter.__init__`, right after the line `self._model_facts = ModelFactsStore(self._cfg.state_dir)`, add:

```python
        self._run_startup_catalogue_refresh()
```

Add a new private method, placed near `_maybe_hot_reload` (same file, later in the class):

```python
    def _run_startup_catalogue_refresh(self) -> None:
        """Once per service start, check what each provider actually has.

        Replaces a daily background timer (owner's call, 2026-09-20): this
        service is started and stopped by hand, so a calendar timer only
        matters for something left running unattended. refresh_config()
        already tolerates one provider being unreachable — it excludes that
        provider from comparison rather than reporting false vanishings.
        This wrapper's only job is making sure nothing about this call can
        stop the service from starting, the same discipline
        _maybe_hot_reload already applies to a bad config reload.
        """
        import os

        try:
            result = refresh_config(
                str(self._config_path), self._cfg.state_dir,
                aa_key=os.environ.get("AA_API_KEY"))
        except Exception as e:
            logger.error(
                "Startup catalogue refresh failed; continuing without it: %s",
                f"{type(e).__name__}: {e}")
            return

        from flexrouter.store import read_json
        pending_path = Path(self._cfg.state_dir) / "catalog_pending.json"
        pending = read_json(pending_path, default={})
        for ident in result.added:
            provider, _, model = ident.partition("/")
            entry = next(
                (m for m in pending.get(provider, {}).get("appeared", [])
                 if m.get("model") == model),
                None,
            )
            if entry:
                self._model_facts.record_discovered(
                    provider, model, entry.get("context_window"))
```

`Path` is already imported at the top of `flexrouter/_router.py` (`from pathlib import Path`) — confirm before adding a duplicate import.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_startup_refresh.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: 738+ passed, 1 skipped. **Every existing test that constructs a `LocalRouter` now also triggers a real `refresh_config()` call** (making real, likely-failing HTTP calls against fake `https://alpha.test/v1`-style URLs in test fixtures) unless that test already monkeypatches `flexrouter._router.load_config` in a way that `refresh_config` can still run against — check whether existing tests slow down noticeably or start failing because of this. If many existing tests are affected, this is a real, whole-suite-level finding: report it rather than silently patching every test file. The most likely fix, if needed, is that `refresh_config()` already handles connection failures to a fake URL gracefully (returns `provider_errors`, does not raise) since Stage 1/2 tests already exercise exactly this — confirm this holds and existing tests still pass, they should just each take a small amount of real wall-clock time for the doomed HTTP attempt to fail over. If the full suite becomes meaningfully slower (multiple extra minutes) because of this, note it in your report as a concern for the controller to weigh, but do not change scope to fix it yourself without asking — this plan's own tests already monkeypatch `refresh_config` directly to avoid this exact cost, and that same technique is available to any other test if it turns out to need it, which is a decision for whoever finds the slowdown, not a default action.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/_router.py tests/test_startup_refresh.py
git commit -m "feat(catalogue): check what each provider has once per service start"
```

---

### Task 3: End-to-end proof, docs, the ADR, and filing the deferred follow-up

**Files:**
- Test: `tests/test_startup_refresh_e2e.py`
- Modify: `CONTEXT.md`
- Create: `docs/adr/0014-catalogue-refresh-runs-on-startup-apply-is-deferred.md`
- Create: `.scratch/v2-stage7-followups/issues/01-accept-apply-mechanism-for-pending-catalogue-changes.md`

**Interfaces:**
- Consumes: everything from Tasks 1-2.
- Produces: no new names.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_startup_refresh_e2e.py
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


def test_starting_the_real_service_leaves_a_catalog_pending_file(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    client = TestClient(create_app(str(tmp_path / "config.yaml")))
    # Any request forces the router to be constructed, which runs the
    # startup refresh — this proves the wiring end to end through the real
    # service entry point, not just by constructing LocalRouter directly.
    client.get("/v1/models")

    assert (tmp_path / "state" / "catalog_pending.json").exists()
```

Implementer note: this test intentionally does NOT mock `flexrouter.client.AsyncClient`/`refresh_config` — it proves the real `refresh_config()` (against an unreachable fake URL, `https://alpha.test/v1`) still leaves a `catalog_pending.json` file (even if every provider in it shows a `provider_errors` entry and nothing "appeared"), because Task 2's wrapper never lets the attempt crash startup. If this test is slow (a real, doomed HTTP connection attempt has to time out), that's expected and consistent with Task 2's own note about wall-clock cost — do not mock it away just to make it fast; the whole point is proving the real, unmocked path doesn't break anything.

- [ ] **Step 2: Run the test to verify it passes**

Run: `python -m pytest tests/test_startup_refresh_e2e.py -q`
Expected: PASS, if Tasks 1-2 are correct — this is a proof, not new behaviour. May take longer than a typical test due to a real (doomed) HTTP connection attempt; that is expected.

- [ ] **Step 3: Update `CONTEXT.md`**

Find the existing "Catalogue refresh" glossary entry (already present, from Stage 1/2) and add one sentence to the end of it:

```markdown
Runs automatically once per service start, in addition to the existing manual `flexrouter refresh` and the dashboard's refresh button (`flexrouter/_router.py`: `LocalRouter._run_startup_catalogue_refresh`) — a failure here is logged and otherwise ignored, never blocking the service from starting. Newly-discovered models get their published context window recorded to `state/model_facts.json` (`ModelFactsStore.record_discovered`); nothing about a pending appeared or vanished model is applied automatically yet — accepting a pending change into (or out of) a bucket is deferred (ADR 0014).
```

Update the "Not yet built" paragraph:

```markdown
The v2 design (`docs/superpowers/specs/2026-09-18-flexrouter-v2-design.md`) describes nine stages. Stages 1 through 7 are implemented: the shared home, the OpenAI-shaped surface, the per-request trace, real per-key state, the error brain, model facts, and now an automatic per-startup catalogue check. Accepting a pending catalogue change into the router's actual configuration is not yet built — see ADR 0014. Do not treat later-stage concepts (anything not covered above) as present in the code.
```

- [ ] **Step 4: Write the ADR**

```markdown
# 0014. Catalogue refresh runs on startup; applying a pending change is deferred

Date: 2026-09-21
Status: Accepted

## Context

Spec section 6 asks for two things: an automatic catalogue check (originally
"once a day," changed by the owner on 2026-09-20 to "once per service
start," since this service is started and stopped by hand rather than left
running unattended), and a way to apply what that check finds — accepting
a newly-appeared model into a bucket, or accepting a vanished model's
removal, with a per-provider setting to do either automatically.

`flexrouter/refresh.py`'s `refresh_config()` already existed, already
correct and already tested, from Stages 1-2: it makes the actual
per-provider calls, diffs against `config.yaml`, and writes
`state/catalog_pending.json` — but nothing had ever called it except a
human running `flexrouter refresh` or clicking the dashboard's refresh
button. The automatic trigger was the one genuinely missing piece for the
"check" half of this section.

The "apply" half needs something `overrides.json` cannot do today:
`flexrouter/overrides.py`'s `apply_overrides` only patches fields on a
model `config.yaml` already declares (`provider`/`model` are deliberately
excluded from the allowed-fields list, specifically so an override can
never turn one model's identity into a different one) — it has no way to
inject a brand-new model that doesn't exist in `config.yaml` at all. Adding
that capability, and deciding how an auto-apply setting should choose
which bucket a newly-appeared model lands in, is a change that can alter
which models the router actually serves — the same category of risk Stage
6 priced when it deferred letting `model_facts.json` influence selection.

## Decision

**This stage builds the check half only.** `LocalRouter.__init__` calls
the unmodified `refresh_config()` once, synchronously, wrapped so nothing
about the call — a slow provider, an unreachable one, an unexpected
exception — can prevent the service from starting.

*Cost:* service startup takes a little longer, once, for the round trip to
every configured provider. This is the accepted cost of "check on
startup" replacing a background timer, already priced in by the owner's
2026-09-20 decision.

**Newly-discovered models get exactly one fact recorded automatically**:
their published context window, via `ModelFactsStore.record_discovered()`,
read back from the `catalog_pending.json` entry `refresh_config()` already
wrote. Nothing else about a new model is inferred — there is no real
`Decider` configured (still `NullDecider`, which answers nothing), and
nothing about the model has been *observed* yet (Stage 6's evidence
wiring only fires from real traffic, which a never-yet-routed model has
had none of).

**The apply mechanism is deferred, not built.** Turning a pending appeared
model into something selectable, or a pending vanished model into an
accepted removal, needs: a new way for `overrides.json` to represent a
model identity that doesn't exist in `config.yaml`; a decision about which
bucket an auto-applied model lands in; and — for a manual "ask me first"
acceptance — some interface to say yes, which doesn't exist without either
a CLI command this stage didn't build or the dashboard this project
doesn't have yet (Stage 8). Filed as a follow-up
(`.scratch/v2-stage7-followups/issues/01-...md`) rather than rushed.

*Cost:* `state/catalog_pending.json` accumulates real, correct findings
that nothing acts on yet — exactly the state this codebase was already in
before this stage, just refreshed automatically now instead of only on a
manual command. No regression, a real but partial step forward.

## Consequences

- The pending tray this stage's own roadmap entry names has real data in
  it, automatically, from the first service start onward — Stage 8's
  dashboard (or a future CLI command) has something true to show and act
  on the moment it exists, without needing to also build the discovery
  side.
- `state/model_facts.json` entries for brand-new models carry a real,
  `published`-sourced context window from the moment they're discovered,
  even before any traffic has ever been routed to them.
- The next stage or follow-up that builds the apply mechanism inherits a
  known, load-bearing constraint it must solve rather than discover:
  `overrides.py`'s `ALLOWED_FIELDS` deliberately excludes model identity,
  and that exclusion is there on purpose (it stops an override from
  quietly turning one model into another) — whatever adds "inject a new
  model" needs its own, separate mechanism, not a weakening of that
  existing guarantee.
```

- [ ] **Step 5: File the deferred follow-up**

```markdown
# Issue 01: no way to accept a pending catalogue change into (or out of) the router's actual configuration

Status: ready-for-agent

## What

`state/catalog_pending.json` (written automatically on every service start
as of Stage 7, and manually via `flexrouter refresh`/the dashboard's
refresh button since Stages 1-2) records every model a provider's
catalogue shows that `config.yaml` doesn't know about yet ("appeared"),
and every model `config.yaml` lists that the provider's catalogue no
longer shows ("vanished"). Nothing acts on either list. A newly-appeared
model is never added to a bucket; a vanished model is never disabled or
flagged. The spec (§6) calls for a per-provider auto-apply setting plus a
manual, ask-me-first default — neither exists.

## Why it is not urgent

Nothing is broken by its absence — `catalog_pending.json` is exactly as
informative as it already was pre-Stage-7, just populated automatically
now instead of only on demand. The router keeps serving whatever
`config.yaml` plus `overrides.json` already say, unaffected either way.

## Done when

- `overrides.json` gains a way to represent a model that doesn't exist in
  `config.yaml` at all (today `flexrouter/overrides.py`'s
  `apply_overrides`/`ALLOWED_FIELDS` can only patch fields on a model
  `config.yaml` already declares — `provider`/`model` identity fields are
  deliberately excluded, on purpose, to stop an override turning one
  model into another; whatever this issue builds must not weaken that).
- A decision is made and recorded on which bucket a newly-appeared model
  lands in when accepted, and on what "accepting" a vanished model's
  removal actually does (disable it via the existing `enabled: false`
  override mechanism, most likely, since that already works for a model
  `config.yaml` still declares).
- A new `ProviderConfig.catalogue_auto_apply: bool = False` setting (or
  similar) lets a provider opt into applying its own pending changes
  automatically on the next startup refresh, matching spec's stated
  default (manual/ask-me-first) and opt-in (auto-apply).
- Some interface exists to accept/dismiss a pending item manually — a CLI
  command is the smallest option if this lands before Stage 8's
  dashboard; the dashboard's eventual "pending tray" UI can call the same
  underlying function either way.
- Real tests prove: an accepted appeared model actually becomes selectable
  (shows up in `/v1/models`, can be routed to); an accepted vanished
  model's removal actually stops it being selected; `config.yaml` is never
  written by any of this.

## Constraint

`flexrouter/engine.py` is reused-unchanged by spec decree — whatever
mechanism lands here must make an accepted model selectable through
`config.yaml`/`overrides.json`'s existing load path into `FlexConfig.tiers`,
not by teaching `engine.py` anything new (see ADR 0009/0010/0011/0013 for
the established pattern).
```

- [ ] **Step 6: Run the whole suite one last time**

Run: `python -m pytest -q`
Expected: 738 + (this stage's new test count) passed, 1 skipped.

- [ ] **Step 7: Commit**

```bash
git add tests/test_startup_refresh_e2e.py CONTEXT.md docs/adr/0014-catalogue-refresh-runs-on-startup-apply-is-deferred.md .scratch/v2-stage7-followups/issues/01-accept-apply-mechanism-for-pending-catalogue-changes.md
git commit -m "docs(catalogue): prove startup refresh end to end, and record the scope ruling as ADR 0014"
```

---

## After all three tasks

- Run the full suite once more and record the final passed/skipped count.
- There is no git remote. Merge this stage's branch into `master` yourself once its final whole-branch review is clean, the same way Stages 3-6 did — the owner explicitly asked not to be consulted through the end of Stage 7, which this is.
- **This is the last stage the owner asked for in this run.** Once merged, produce the three deliverables he asked for at the start: (1) a handoff file for whatever comes after Stage 7, in the shape of the original stage-3 handoff document; (2) an extremely detailed, plain-language document (he is not a programmer — no jargon) explaining every rule and piece of logic built across Stages 3-7, with the why behind each; (3) a low-effort interactive demo comparing the original tool's behavior against the finished Stage 7 state.
