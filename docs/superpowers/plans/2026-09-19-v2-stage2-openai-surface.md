# flexrouter v2 — Stage 2: The OpenAI-Shaped Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the OpenAI-shaped wire surface on the one service, and demote the importable `FlexRouter` from an in-process router to a thin HTTP client that fails loudly when the service is not running.

**Architecture:** The routing machinery that `FlexRouter` holds today is renamed `LocalRouter` and stays exactly where it is — it is what the service runs. A new `FlexRouter` in `flexrouter/_client_router.py` keeps the same method signatures but speaks HTTP to `http://127.0.0.1:<port>/v1/...`, reconstructing the same stream event dataclasses from the wire. The wire vocabulary becomes bucket-first: a bare bucket name (`smart`) routes through that bucket, a name containing `/` (`groq/llama-3.3-70b-versatile`) pins that one model, and `auto` picks the best-scoring bucket. Pinning is served by a second `RoutingEngine` over a shadow config whose buckets are one-model buckets keyed `provider/model`, sharing the live rate-limit, penalty and quota stores — so `engine.py` is not touched. Tool-call deltas are forwarded to the wire as the provider's own dictionaries instead of being flattened and rebuilt.

**Tech Stack:** Python 3.11+, FastAPI, httpx, pytest, `hmac.compare_digest`, dataclasses.

**Spec:** `docs/superpowers/specs/2026-09-18-flexrouter-v2-design.md` (§2, "The OpenAI surface" and "The library becomes a client")

**Roadmap:** `docs/superpowers/plans/2026-09-18-v2-roadmap.md` (Stage 2)

**Previous stage:** `docs/superpowers/plans/2026-09-18-v2-stage1-shared-home.md`

## Global Constraints

- Python floor is `>=3.11`, already set in `pyproject.toml`. Do not raise it.
- **Nothing in this codebase may write `config.yaml`.** Every machine-made
  change goes to `overrides.json`. This is the standing Stage 1 constraint and
  it binds this stage too.
- **Secrets are never returned by any HTTP endpoint or printed in full by any
  CLI command.** Only `mask()` output (`…e8d3`). This now explicitly includes
  provider error text forwarded to a client — see Task 5.
- `FLEXROUTER_HOME` overrides the home root everywhere, with no exceptions.
- Default service port is **4891** (`flexrouter/home.py` `DEFAULT_PORT`).
  `dashboard_port` stays accepted as a deprecated alias.
- `buckets:` is the preferred spelling in settings files; `tiers:` stays
  accepted as an alias. The internal attribute stays `FlexConfig.tiers` and the
  public parameter stays `tier` — renaming either would touch `engine.py`,
  which the spec forbids refactoring.
- **Do not refactor** `recovery.py`, `engine.py`, `window.py`, `quota.py`,
  `rate_limits.py`, `errors.py`, `client.py`. They are listed as "reused
  unchanged" in the spec. Three independent reviews judged them better than the
  commercial equivalents. This plan is designed so that none of them needs an
  edit; if you believe one does, stop and say so rather than editing it.
- **No auto-start and no silent local fallback** in the library. Rejected twice
  by the owner. One error, with the start command in it.
- **No mid-stream failover.** A failure after the first delta emits one
  well-formed error chunk, then `[DONE]`, then closes.
- Tests: `pytest`, `asyncio_mode = "auto"` already set. **Do not use `respx`
  together with FastAPI's `TestClient`** — they collide in this repo. Endpoint
  tests monkeypatch the decider and `probe_key` instead.
- **Selection tests must use widely separated scores (99 vs 40).** The engine
  picks randomly among models within 20% of the top score.
- `tests/conftest.py` has an autouse fixture pointing `FLEXROUTER_HOME` at a
  temp directory. A test that writes to the real home is a bug. Do not remove
  it.
- The suite must finish at **481 passed, 1 skipped or better** with no ignore
  flags: `python -m pytest -q`.

## Rulings made while writing this plan

These resolve ambiguities the spec leaves open. They are recorded here and land
as ADR 0009 in Task 10. Surface them to the owner in plain language at the end.

1. **Buckets are named plainly on the wire.** `/v1/models` lists bucket names
   bare (`smart`), not `auto-smart`. The old spellings (`auto-<bucket>` and
   `<bucket>::<provider>/<model>`) stay accepted on input forever so existing
   chat clients keep working, but they are no longer advertised. The internal
   field stays `.tiers`; the split between the owner's word ("bucket") and the
   code's word ("tier") is confined to one helper module.
2. **A name containing `/` pins one model.** There is no other way to tell a
   bucket from a model, and bucket names never contain `/`.
3. **Pinning does not touch `engine.py`.** It is served by a second
   `RoutingEngine` over a shadow config, sharing the live stores.
4. **The optional local key guards `/v1/*` only.** The dashboard's `/api/*`
   and the SPA stay open on loopback, because a browser pointed at the
   dashboard has no way to carry a bearer header and the whole surface is
   bound to `127.0.0.1` by default.
5. **The client library emits no routing-progress events.** `AttemptEvent` and
   `AttemptFailedEvent` describe in-process retry behaviour and have no place
   in the OpenAI shape. The client's `agenerate_stream` yields only
   `DeltaEvent`, `ReasoningDeltaEvent`, `ToolCallDeltaEvent` and `DoneEvent`.
6. **`FlexRouter.reload()` becomes a no-op on the client.** The service hot
   reloads itself; a client has nothing to reload. It stays on the class so
   existing calling code keeps working.

---

## File Structure

| File | Responsibility |
|---|---|
| `flexrouter/wire.py` | **new** — the whole vocabulary translation between what a client writes in `model` and what the router needs. Pure functions, no I/O. |
| `flexrouter/_router.py` | **modify** — class renamed `LocalRouter`; gains a pin engine and a `raw` field on `ToolCallDeltaEvent`. |
| `flexrouter/_client_router.py` | **new** — the new `FlexRouter`: an HTTP client with the old signatures. |
| `flexrouter/app.py` | **modify** — `GET /v1/models/{id}`, bucket-first listing, the optional local key, faithful error envelopes, raw tool-call forwarding, the error chunk. |
| `flexrouter/redact.py` | **new** — one function that strips anything key-shaped out of text bound for a client. |
| `flexrouter/exceptions.py` | **modify** — add `ServiceNotRunning`. |
| `flexrouter/config.py` | **modify** — parse `settings.auth_token`; mask it everywhere settings are shown. |
| `flexrouter/__init__.py` | **modify** — export the client `FlexRouter`, `LocalRouter` and `ServiceNotRunning`. |

---

### Task 1: The wire vocabulary

A client writes one string in `model`. Three things it can mean, and the
translation is currently spread across `_parse_model_to_tier` and
`_resolve_tier` in `app.py` with no way to pin a model. Pull it into one
testable module first; everything else in the stage depends on it.

**Files:**
- Create: `flexrouter/wire.py`
- Test: `tests/test_wire.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass(frozen=True) class Target: kind: Literal["bucket", "pin"]; name: str`
    — for a bucket, `name` is the bucket name; for a pin, `name` is
    `"provider/model"`.
  - `parse_model(model: str) -> Target`
  - `resolve(target: Target, bucket_names: list[str], best_bucket: str) -> str`
    returning the string to pass as `tier` to the router.
  - `bucket_id(name: str) -> str` and `model_id(provider: str, model: str) -> str`
    — the ids advertised by `/v1/models`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_wire.py
import pytest

from flexrouter.wire import Target, bucket_id, model_id, parse_model, resolve


def test_bare_name_is_a_bucket():
    assert parse_model("smart") == Target("bucket", "smart")


def test_name_with_a_slash_pins_one_model():
    assert parse_model("groq/llama-3.3-70b-versatile") == Target(
        "pin", "groq/llama-3.3-70b-versatile")


def test_auto_is_a_bucket_named_auto():
    assert parse_model("auto") == Target("bucket", "auto")


def test_legacy_auto_prefix_still_names_a_bucket():
    assert parse_model("auto-smart") == Target("bucket", "smart")


def test_legacy_double_colon_form_now_pins_the_model():
    assert parse_model("smart::groq/llama-3.3-70b-versatile") == Target(
        "pin", "groq/llama-3.3-70b-versatile")


def test_empty_model_falls_back_to_auto():
    assert parse_model("") == Target("bucket", "auto")


def test_resolve_passes_a_known_bucket_through():
    assert resolve(Target("bucket", "fast"), ["smart", "fast"], "smart") == "fast"


def test_resolve_auto_uses_the_best_bucket():
    assert resolve(Target("bucket", "auto"), ["smart", "fast"], "smart") == "smart"


def test_resolve_unknown_bucket_raises_and_names_the_real_ones():
    with pytest.raises(KeyError) as exc:
        resolve(Target("bucket", "nope"), ["smart", "fast"], "smart")
    assert "nope" in str(exc.value)
    assert "smart" in str(exc.value)


def test_resolve_pin_passes_the_model_name_through():
    assert resolve(Target("pin", "groq/x"), ["smart"], "smart") == "groq/x"


def test_ids_are_what_v1_models_advertises():
    assert bucket_id("smart") == "smart"
    assert model_id("groq", "llama-3.3") == "groq/llama-3.3"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_wire.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flexrouter.wire'`

- [ ] **Step 3: Write the implementation**

```python
# flexrouter/wire.py
"""What a client writes in `model`, and what the router needs, are not the
same vocabulary. This is the only place that translates between them.

The owner's word for a group of models is "bucket" and settings files write
`buckets:`, but the internal field is still `FlexConfig.tiers` and the public
parameter is still `tier` (see the Bucket entry in CONTEXT.md and ADR 0009).
That split is deliberately confined to this module: everything outward-facing
here says bucket, and the one string handed back is called a tier by its
caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Target:
    kind: Literal["bucket", "pin"]
    name: str


def parse_model(model: str) -> Target:
    """Work out what a client meant by the string it put in `model`.

    A name containing "/" is a specific provider's model and pins it; bucket
    names never contain "/". Anything else names a bucket. The two older
    spellings this service used to advertise ("auto-smart", and
    "smart::groq/llama") are still understood, because chat clients save the
    model name in their own settings and would otherwise break on upgrade.
    """
    model = (model or "").strip()
    if not model:
        return Target("bucket", "auto")
    if "::" in model:
        # Legacy discovery id. The bucket half is dropped: the model half is
        # more specific, and pinning is now a real behaviour rather than the
        # no-op it used to be.
        _, _, rest = model.partition("::")
        if "/" in rest:
            return Target("pin", rest)
        return Target("bucket", rest or "auto")
    if "/" in model:
        return Target("pin", model)
    if model.startswith("auto-") and len(model) > len("auto-"):
        return Target("bucket", model[len("auto-"):])
    return Target("bucket", model)


def resolve(target: Target, bucket_names: list[str], best_bucket: str) -> str:
    """The string to hand the router as its `tier` argument.

    Raises KeyError naming the buckets that do exist, because that message is
    forwarded to whoever sent the request.
    """
    if target.kind == "pin":
        return target.name
    if target.name == "auto":
        return best_bucket
    if target.name in bucket_names:
        return target.name
    raise KeyError(
        f"There is no bucket named {target.name!r}. "
        f"Buckets you have: {', '.join(sorted(bucket_names)) or 'none yet'}.")


def bucket_id(name: str) -> str:
    return name


def model_id(provider: str, model: str) -> str:
    return f"{provider}/{model}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_wire.py -q`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add flexrouter/wire.py tests/test_wire.py
git commit -m "feat(wire): one place that translates a client's model name"
```

---

### Task 2: Pinning one model, without touching the engine

`engine.py` is reused-unchanged by spec decree, and its `select()` reads
`self._cfg.tiers[tier]`. So a pin is served by giving a second engine a shadow
config whose buckets are one-model buckets named `provider/model`. Both engines
share the same rate-limit store, penalty box and quota tracker, so a pinned
call consumes and respects exactly the same allowances as a bucket call.

**Files:**
- Modify: `flexrouter/_router.py` (constructor, `reload`, and the five call
  sites that take a tier: in `agenerate`, `agenerate_stream` and
  `remaining_capacity`)
- Test: `tests/test_pinned_routing.py`

**Interfaces:**
- Consumes: nothing (the caller passes an already-resolved string).
- Produces: `FlexRouter._engine_for(tier: str) -> RoutingEngine` — returns the
  pin engine when `tier` contains `/`, the normal engine otherwise. Also
  `FlexRouter._build_pin_engine() -> RoutingEngine` and the attribute
  `_pin_engine`. (The class is still named `FlexRouter` at this point; Task 9
  renames it to `LocalRouter`.)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pinned_routing.py
import pytest

from flexrouter._router import FlexRouter
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={
            "smart": [
                ModelConfig(provider="alpha", model="big", score=99),
                ModelConfig(provider="beta", model="small", score=40),
            ],
        },
        providers={
            "alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"]),
            "beta": ProviderConfig(base_url="https://beta.test/v1", api_keys=["k"]),
        },
        state_dir=str(tmp_path / "state"),
    )


def _built_router(tmp_path, monkeypatch):
    """A real router over the fixture config, with no file on disk."""
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    return FlexRouter(str(tmp_path / "config.yaml"))


def test_pin_engine_has_one_bucket_per_model(tmp_path, monkeypatch):
    router = _built_router(tmp_path, monkeypatch)
    assert set(router._pin_engine._cfg.tiers) == {"alpha/big", "beta/small"}
    assert [mc.model for mc in router._pin_engine._cfg.tiers["alpha/big"]] == ["big"]


def test_engine_for_picks_the_pin_engine_only_for_slashed_names(tmp_path, monkeypatch):
    router = _built_router(tmp_path, monkeypatch)
    assert router._engine_for("smart") is router._engine
    assert router._engine_for("alpha/big") is router._pin_engine


def test_pinned_selection_always_returns_that_model(tmp_path, monkeypatch):
    router = _built_router(tmp_path, monkeypatch)
    for _ in range(20):
        route = router._engine_for("beta/small").select("beta/small", 10, False)
        assert (route.provider, route.model) == ("beta", "small")


def test_pin_and_bucket_share_one_penalty_box(tmp_path, monkeypatch):
    router = _built_router(tmp_path, monkeypatch)
    assert router._pin_engine._penalties is router._engine._penalties
    assert router._pin_engine._cfg is not router._engine._cfg


def test_a_quarantine_through_one_engine_is_seen_by_the_other(tmp_path, monkeypatch):
    router = _built_router(tmp_path, monkeypatch)
    router._penalties.quarantine("beta", "small", "gone")
    assert router._pin_engine.select("beta/small", 10, False) is None


def test_unknown_pin_raises_keyerror(tmp_path, monkeypatch):
    router = _built_router(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        router._engine_for("alpha/nope").select("alpha/nope", 10, False)


def test_reload_rebuilds_the_pin_engine(tmp_path, monkeypatch):
    router = _built_router(tmp_path, monkeypatch)
    new = _cfg(tmp_path)
    new.tiers["smart"].append(ModelConfig(provider="alpha", model="extra", score=40))
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: new)
    router.reload()
    assert "alpha/extra" in router._pin_engine._cfg.tiers
```

Implementer note: check `PenaltyBox.quarantine`'s real signature in
`flexrouter/recovery.py` before running the fifth test and match it. Do not
change `recovery.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_pinned_routing.py -q`
Expected: FAIL — `AttributeError: 'FlexRouter' object has no attribute '_pin_engine'`

- [ ] **Step 3: Write the implementation**

In `flexrouter/_router.py`, after the block that builds `self._engine` in
`__init__`, add:

```python
        self._pin_engine = self._build_pin_engine()
```

Add `import dataclasses` to the module imports, and add these two methods to
the class, next to `reload`:

```python
    def _build_pin_engine(self) -> RoutingEngine:
        """A second engine whose buckets are one model each, named
        "provider/model".

        This exists because a request may name one specific model instead of a
        bucket, and `RoutingEngine.select` looks its candidates up by bucket
        name in the config it was given. `engine.py` is reused unchanged by
        spec decree, so instead of teaching it about pins we hand a second
        instance a shadow config. Both share the same rate-limit store,
        penalty box and quota tracker, so a pinned call consumes and respects
        exactly the same allowances as a bucket call, and a model quarantined
        through one is quarantined through the other.
        """
        pinned: dict[str, list] = {}
        for model_configs in self._cfg.tiers.values():
            for mc in model_configs:
                pinned.setdefault(f"{mc.provider}/{mc.model}", [mc])
        shadow = dataclasses.replace(self._cfg, tiers=pinned)
        return RoutingEngine(
            shadow,
            rate_limit_store=self._rate_limit_store,
            penalties=self._penalties,
            quota_tracker=self._quota_tracker,
        )

    def _engine_for(self, tier: str) -> RoutingEngine:
        """The engine that knows about `tier`.

        A name with a "/" in it is one specific model; bucket names never
        contain one. See `flexrouter/wire.py`, which is what produces these
        strings from a request.
        """
        return self._pin_engine if "/" in tier else self._engine
```

Then change these five call sites from `self._engine.` to
`self._engine_for(tier).`:

- in `agenerate`: `route = self._engine.select(tier, ...)` and
  `secs = self._engine.seconds_until_available(tier)`
- in `agenerate_stream`: the same two
- in `remaining_capacity`: `return self._engine.remaining_capacity(tier)`

Leave every other `self._engine.` call alone. `penalize`, `record_request` and
`health_snapshot` take a provider and model rather than a tier, and they write
to the shared stores, so routing either way records to the same place.

Finally, in `reload()`, after the three existing `self._engine.*` assignments,
add:

```python
        self._pin_engine = self._build_pin_engine()
```

`dataclasses.replace` on `FlexConfig` re-runs `__post_init__`, which only
reconciles `port` and `dashboard_port` — both already set — so the shadow
config's port matches the real one and nothing else is touched.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_pinned_routing.py -q`
Expected: PASS, 7 tests.

- [ ] **Step 5: Run the whole suite — nothing else may change**

Run: `python -m pytest -q`
Expected: the previous 481 passed / 1 skipped, plus the new tests from
Tasks 1–2.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/_router.py tests/test_pinned_routing.py
git commit -m "feat(router): route to one named model without touching the engine"
```

---

### Task 3: Bucket-first model listing and `GET /v1/models/{id}`

**Files:**
- Modify: `flexrouter/app.py` (`list_models`, `_parse_model_to_tier`,
  `_resolve_tier`, `chat_completions`)
- Test: `tests/test_v1_models.py`

**Interfaces:**
- Consumes: `flexrouter.wire.parse_model`, `resolve`, `bucket_id`, `model_id`
  (Task 1).
- Produces: `app._model_entries(router) -> list[dict]` — every entry
  `/v1/models` returns, in order; `app._best_bucket(router) -> str`.
  `_parse_model_to_tier` and `_resolve_tier` are **deleted**; everything that
  called them calls `wire` instead.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_v1_models.py
from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={
            "smart": [ModelConfig(provider="alpha", model="big", score=99)],
            "fast": [ModelConfig(provider="beta", model="small", score=40)],
        },
        providers={
            "alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"]),
            "beta": ProviderConfig(base_url="https://beta.test/v1", api_keys=["k"]),
        },
        state_dir=str(tmp_path / "state"),
    )


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def test_buckets_are_listed_by_their_plain_names(tmp_path, monkeypatch):
    data = _client(tmp_path, monkeypatch).get("/v1/models").json()["data"]
    ids = [e["id"] for e in data]
    assert ids[:3] == ["smart", "fast", "auto"]
    assert "auto-smart" not in ids


def test_every_model_is_listed_as_provider_slash_model(tmp_path, monkeypatch):
    data = _client(tmp_path, monkeypatch).get("/v1/models").json()["data"]
    ids = [e["id"] for e in data]
    assert "alpha/big" in ids
    assert "beta/small" in ids
    assert not any("::" in i for i in ids)


def test_a_bucket_entry_says_it_is_a_bucket(tmp_path, monkeypatch):
    data = _client(tmp_path, monkeypatch).get("/v1/models").json()["data"]
    smart = next(e for e in data if e["id"] == "smart")
    assert smart["object"] == "model"
    assert smart["flexrouter"]["kind"] == "bucket"
    assert smart["flexrouter"]["models"] == ["alpha/big"]


def test_a_model_entry_carries_its_routing_facts(tmp_path, monkeypatch):
    data = _client(tmp_path, monkeypatch).get("/v1/models").json()["data"]
    big = next(e for e in data if e["id"] == "alpha/big")
    fx = big["flexrouter"]
    assert fx["kind"] == "model"
    assert fx["provider"] == "alpha"
    assert fx["score"] == 99
    assert fx["buckets"] == ["smart"]
    assert fx["quarantined"] is False


def test_get_one_bucket(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch).get("/v1/models/smart")
    assert r.status_code == 200
    assert r.json()["id"] == "smart"


def test_get_one_model_with_a_slash_in_its_name(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch).get("/v1/models/alpha/big")
    assert r.status_code == 200
    assert r.json()["id"] == "alpha/big"


def test_get_auto(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch).get("/v1/models/auto")
    assert r.status_code == 200
    assert r.json()["flexrouter"]["resolves_to"] == "smart"


def test_get_an_unknown_model_is_a_404_in_the_openai_envelope(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch).get("/v1/models/nope")
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["type"] == "invalid_request_error"
    assert err["code"] == "model_not_found"
    assert "nope" in err["message"]


def test_legacy_ids_still_resolve_on_get(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.get("/v1/models/auto-smart").json()["id"] == "smart"
    assert c.get("/v1/models/smart::alpha/big").json()["id"] == "alpha/big"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_v1_models.py -q`
Expected: FAIL — the listing starts with `auto` and carries `auto-smart`.

- [ ] **Step 3: Write the implementation**

Add to the imports at the top of `app.py`:

```python
from flexrouter.wire import bucket_id, model_id, parse_model, resolve
```

Replace `list_models`, `_parse_model_to_tier` and `_resolve_tier` in
`flexrouter/app.py` with:

```python
def _best_bucket(router) -> str:
    """The bucket holding the single highest-scoring model. What `auto` means."""
    buckets = list(router._cfg.tiers.keys())
    if not buckets:
        return "default"
    best_score = None
    best = buckets[0]
    for name in buckets:
        for mc in router._cfg.tiers[name]:
            if best_score is None or mc.score > best_score:
                best_score, best = mc.score, name
    return best


def _model_entries(router) -> list[dict]:
    """Everything /v1/models advertises: buckets first, then `auto`, then every
    model by its own name.

    Buckets come first because they are the normal path — the owner's code and
    his chat clients ask for "smart", not for a particular provider's model. A
    chat client that renders this list in a dropdown shows him the names he
    chose himself, at the top.
    """
    tiers = router._cfg.tiers
    penalties = router._engine._penalties
    entries: list[dict] = []

    for name, model_configs in tiers.items():
        entries.append({
            "id": bucket_id(name),
            "object": "model",
            "created": 0,
            "owned_by": "flexrouter",
            "flexrouter": {
                "kind": "bucket",
                "models": [model_id(mc.provider, mc.model) for mc in model_configs],
            },
        })

    entries.append({
        "id": "auto", "object": "model", "created": 0, "owned_by": "flexrouter",
        "flexrouter": {"kind": "bucket", "models": [],
                       "resolves_to": _best_bucket(router)},
    })

    buckets_by_model: dict[tuple[str, str], list[str]] = {}
    for name, model_configs in tiers.items():
        for mc in model_configs:
            buckets_by_model.setdefault((mc.provider, mc.model), []).append(name)

    seen: set[tuple[str, str]] = set()
    for model_configs in tiers.values():
        for mc in model_configs:
            key = (mc.provider, mc.model)
            if key in seen:
                continue
            seen.add(key)
            entries.append({
                "id": model_id(mc.provider, mc.model),
                "object": "model",
                "created": 0,
                "owned_by": mc.provider,
                "flexrouter": {
                    "kind": "model",
                    "provider": mc.provider,
                    "model": mc.model,
                    "buckets": buckets_by_model[key],
                    "score": mc.score,
                    "rpm": mc.rpm,
                    "tpm": mc.tpm,
                    "context_window": mc.context_window,
                    "vision": mc.vision,
                    "quarantined": penalties.is_quarantined(mc.provider, mc.model),
                    "quarantine_reason": penalties.quarantine_reason(mc.provider, mc.model),
                },
            })

    return entries


@v1.get("/models")
async def list_models():
    return {"object": "list", "data": _model_entries(get_router())}


@v1.get("/models/{wanted:path}")
async def get_model(wanted: str):
    """One entry.

    The path converter is needed because a model's name contains a slash,
    which FastAPI would otherwise read as another path segment.
    """
    router = get_router()
    target = parse_model(wanted)
    looking_for = target.name if target.kind == "pin" else bucket_id(target.name)
    for entry in _model_entries(router):
        if entry["id"] == looking_for:
            return entry
    return openai_error(
        f"There is no bucket or model named {wanted!r}.",
        "invalid_request_error", "model_not_found", 404)
```

In `chat_completions`, replace the three lines that resolved the tier with:

```python
    model = body.get("model", "auto")
    router = get_router()
    try:
        tier = resolve(parse_model(model), list(router._cfg.tiers.keys()),
                       _best_bucket(router))
    except KeyError as exc:
        return openai_error(str(exc.args[0]), "invalid_request_error",
                            "model_not_found", 404)
    kwargs = _kwargs_from(body)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_v1_models.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 5: Fix the existing tests that assert the old ids**

Run: `python -m pytest tests/test_app.py -q`
A test asserting `auto-smart` or `smart::alpha/big` in a **response** is
asserting the old advertised vocabulary — update it to the new id. A test
**sending** one of those strings is testing the compatibility path — leave it
exactly as it is, and if none exists, add one.

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest -q`

- [ ] **Step 7: Commit**

```bash
git add flexrouter/app.py tests/test_v1_models.py tests/test_app.py
git commit -m "feat(api): list buckets by name, and serve one model at a time"
```

---

### Task 4: Pinned requests actually reach the pinned model

Task 2 built the mechanism and Task 3 parses the name. This wires them together
and proves it end to end, which is the part a reviewer should be able to reject
on its own.

**Files:**
- Modify: `flexrouter/app.py` (`chat_completions` and `_stream_chat` error
  handling)
- Test: `tests/test_pinned_requests.py`

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces: no new names.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pinned_requests.py
from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [
            ModelConfig(provider="alpha", model="big", score=99),
            ModelConfig(provider="beta", model="small", score=40),
        ]},
        providers={
            "alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"]),
            "beta": ProviderConfig(base_url="https://beta.test/v1", api_keys=["k"]),
        },
        state_dir=str(tmp_path / "state"),
    )


def _client(tmp_path, monkeypatch, record):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    async def fake_chat(self, route, messages, **kwargs):
        record.append((route.provider, route.model))
        return {"choices": [{"message": {"role": "assistant", "content": "hi"},
                             "finish_reason": "stop"}],
                "usage": {"total_tokens": 3}}

    # respx and TestClient collide in this repo; patch the client method.
    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def test_a_pinned_request_always_reaches_that_model(tmp_path, monkeypatch):
    record: list = []
    client = _client(tmp_path, monkeypatch, record)
    for _ in range(10):
        r = client.post("/v1/chat/completions", json={
            "model": "beta/small", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
    assert set(record) == {("beta", "small")}


def test_a_bucket_request_uses_the_high_scorer(tmp_path, monkeypatch):
    record: list = []
    client = _client(tmp_path, monkeypatch, record)
    client.post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    assert record == [("alpha", "big")]


def test_pinning_a_model_that_is_not_configured_is_a_clear_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, [])
    r = client.post("/v1/chat/completions", json={
        "model": "alpha/ghost", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "model_not_found"
    assert "alpha/ghost" in err["message"]


def test_the_legacy_double_colon_id_now_pins(tmp_path, monkeypatch):
    record: list = []
    client = _client(tmp_path, monkeypatch, record)
    for _ in range(10):
        client.post("/v1/chat/completions", json={
            "model": "smart::beta/small",
            "messages": [{"role": "user", "content": "hi"}]})
    assert set(record) == {("beta", "small")}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_pinned_requests.py -q`
Expected: `test_pinning_a_model_that_is_not_configured_is_a_clear_404` fails
with a 500 and a bare `KeyError` in the body, because `agenerate` raises
`KeyError` from the pin engine and `app.py` catches it as a generic exception.

- [ ] **Step 3: Write the implementation**

In `chat_completions`, add a `KeyError` handler above the generic
`except Exception` one:

```python
    except KeyError:
        # The pin engine raises KeyError for a model that is not in the
        # settings at all. The client asked for something specific by name;
        # say so rather than returning a bare 500.
        return openai_error(
            f"There is no bucket or model named {model!r}.",
            "invalid_request_error", "model_not_found", 404)
```

Add the matching handler to `_stream_chat`'s `except` chain, above its generic
one:

```python
    except KeyError:
        yield _sse_error(f"There is no bucket or model named {model!r}.",
                         "invalid_request_error", "model_not_found")
        yield "data: [DONE]\n\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_pinned_requests.py -q`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add flexrouter/app.py tests/test_pinned_requests.py
git commit -m "feat(api): naming one model routes to exactly that model"
```

---

### Task 5: Faithful error envelopes, with nothing secret in them

The spec requires the provider's own message to survive rather than being
flattened to "server_error". Stage 1 shipped two secret leaks through exactly
this kind of pass-through, and `client.py`'s `describe_http_error` puts the
provider's raw response body into the exception text. Several providers echo
the rejected key back in an auth error. So the pass-through and the redaction
land together, in one task, because shipping the first without the second is
the leak.

**Files:**
- Create: `flexrouter/redact.py`
- Test: `tests/test_redact.py`, `tests/test_error_envelope.py`
- Modify: `flexrouter/app.py` (`openai_error`, `_sse_error`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `redact.scrub(text: str) -> str` — the same text with anything
  key-shaped replaced by `…` plus its last four characters.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_redact.py
from flexrouter.redact import scrub


def test_an_openai_style_key_is_cut_down_to_its_tail():
    out = scrub("Incorrect API key provided: sk-proj-AAAABBBBCCCCDDDDEEEE1234")
    assert "sk-proj-AAAABBBBCCCCDDDDEEEE1234" not in out
    assert "…1234" in out


def test_a_bare_long_token_is_cut_down_too():
    out = scrub("bad credential gsk0123456789abcdefghijklmnopqrstuvwxyzAB")
    assert "gsk0123456789abcdefghijklmnopqrstuvwxyzAB" not in out
    assert "…zyAB" in out or "…yzAB" in out


def test_a_bearer_header_echoed_back_is_cut_down():
    out = scrub("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456")
    assert "abcdefghijklmnopqrstuvwxyz123456" not in out


def test_ordinary_words_survive_untouched():
    text = "rate limit exceeded, retry in 20s"
    assert scrub(text) == text


def test_a_long_plain_word_is_not_mistaken_for_a_key():
    assert scrub("uncharacteristically disproportionate") == \
        "uncharacteristically disproportionate"


def test_empty_text_is_safe():
    assert scrub("") == ""
```

Implementer note: a model name such as `llama-3.3-70b-versatile` contains both
letters and digits and is longer than the floor, so it *is* scrubbed. That is
the deliberate trade — see the module docstring. Do not add a model-name
exception; a rule with a carve-out is a rule with a hole in it. If a reviewer
objects, file it as a follow-up issue rather than widening the rule here.

```python
# tests/test_error_envelope.py
from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.client import ProviderError
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig
from flexrouter.exceptions import RouterError


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


def _client(tmp_path, monkeypatch, raises):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    async def boom(self, route, messages, **kwargs):
        raise raises

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", boom)
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def test_the_providers_own_words_survive(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch,
                     RouterError("context length 8192 exceeded by 40 tokens"))
    r = client.post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    assert "context length 8192 exceeded by 40 tokens" in r.json()["error"]["message"]


def test_a_key_echoed_by_the_provider_never_reaches_the_client(tmp_path, monkeypatch):
    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"
    client = _client(tmp_path, monkeypatch,
                     RouterError(f"alpha rejected the key {leaked}"))
    r = client.post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    assert leaked not in r.text
    assert "…1234" in r.json()["error"]["message"]


def test_a_key_echoed_mid_stream_never_reaches_the_client(tmp_path, monkeypatch):
    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"
    client = _client(tmp_path, monkeypatch, ProviderError(f"alpha: {leaked} rejected"))
    with client.stream("POST", "/v1/chat/completions", json={
            "model": "smart", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}) as r:
        body = "".join(r.iter_text())
    assert leaked not in body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_redact.py tests/test_error_envelope.py -q`
Expected: FAIL — no `flexrouter.redact` module; the leak tests find the key in
the body.

- [ ] **Step 3: Write the implementation**

```python
# flexrouter/redact.py
"""Nothing key-shaped leaves this process in full.

A provider's own error text is forwarded to whoever sent the request, because
flattening it to "server_error" throws away the only explanation anyone will
get. But several providers echo the rejected credential back in that text
("Incorrect API key provided: sk-..."), so the text has to be scrubbed on the
way out. Stage 1 shipped two leaks of exactly this shape — one through raw
parser error text, one through an export — and both were caught late.

The rule is deliberately blunt: any long run of key-ish characters that mixes
letters and digits is cut down to its last four characters. That catches some
model names too. A false positive costs a slightly less readable error
message; a false negative costs a key, so the trade only goes one way.
"""
from __future__ import annotations

import re

# Long runs of the characters credentials are made of. The 24-character floor
# is below every provider key format we have seen and above the request ids and
# token counts that show up in provider error text.
_KEYISH = re.compile(r"[A-Za-z0-9_\-]{24,}")


def scrub(text: str) -> str:
    """The same text with anything that could be a credential cut to a tail."""
    if not text:
        return text

    def _cut(m: re.Match) -> str:
        token = m.group(0)
        has_digit = any(c.isdigit() for c in token)
        has_alpha = any(c.isalpha() for c in token)
        # A run this long made only of digits is a number; made only of
        # letters it is a word or a sentence fragment. Credentials mix.
        if not (has_digit and has_alpha):
            return token
        return "…" + token[-4:]

    return _KEYISH.sub(_cut, text)
```

Then in `flexrouter/app.py`, scrub at the two places every outbound message
passes through — this is why both are funnelled through helpers:

```python
from flexrouter.redact import scrub
```

```python
def openai_error(message: str, error_type: str = "server_error",
                 code: str | None = None, status: int = 500) -> JSONResponse:
    # Scrubbed here, at the single exit, rather than at each raise site. A
    # raise site added later would otherwise be a leak nobody notices.
    err: dict = {"message": scrub(message), "type": error_type}
    if code is not None:
        err["code"] = code
    return JSONResponse({"error": err}, status_code=status)


def _sse_error(message: str, error_type: str = "server_error",
               code: str | None = None) -> str:
    err: dict = {"message": scrub(message), "type": error_type}
    if code is not None:
        err["code"] = code
    return _sse({"error": err})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_redact.py tests/test_error_envelope.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 5: Prove there is no other exit**

Run: `grep -n "JSONResponse(" flexrouter/app.py`
Every `/v1/*` error response must go through `openai_error`. If one builds a
`JSONResponse` directly with a provider message in it, route it through
`openai_error` too. Dashboard `/api/*` responses are out of scope here — they
already mask through `mask()`.

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest -q`
Expected: pass. A test asserting a full model name inside an error message will
now see it scrubbed — update the assertion to the part of the message that
matters, and note it in the commit.

- [ ] **Step 7: Commit**

```bash
git add flexrouter/redact.py flexrouter/app.py tests/test_redact.py tests/test_error_envelope.py
git commit -m "feat(api): keep the provider's words, drop anything key-shaped"
```

---

### Task 6: Forward tool-call deltas as the provider wrote them

LiteLLM drops tool calls (BerriAI/litellm#17246) because it re-parses them. This
codebase has the same shape: `client.py` keeps the provider's raw tool-call
dict, `_router.py` flattens it to four fields, and `app.py` rebuilds a dict from
those four. Anything the provider sent that is not one of the four is gone, and
the rebuilt delta loses its `index`. Carry the original through instead.

`client.py` is reused-unchanged and needs no edit — it already holds the raw
dict.

**Files:**
- Modify: `flexrouter/_router.py` (`ToolCallDeltaEvent`, and the one place it
  is constructed)
- Modify: `flexrouter/app.py` (`_stream_chat`'s tool-call branch)
- Test: `tests/test_tool_call_passthrough.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ToolCallDeltaEvent` gains `raw: dict = field(default_factory=dict)`
  — the provider's own delta dictionary, unmodified. The four existing fields
  stay, because `DoneEvent` assembly and existing callers use them. Also
  `app._tool_call_delta(event) -> dict`, the fallback rebuild.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tool_call_passthrough.py
import json

from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.client import StreamChunk
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


# A captured tool-call delta carrying an "index", a "type", and a
# provider-specific "cache_control" that the four-field flattening throws away.
RAW_DELTA = {
    "index": 0,
    "id": "call_abc123",
    "type": "function",
    "function": {"name": "get_weather", "arguments": ""},
    "cache_control": {"type": "ephemeral"},
}
RAW_ARGS = {"index": 0, "function": {"arguments": '{"city":'}}


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    async def fake_stream(self, route, messages, **kwargs):
        yield StreamChunk(tool_call_delta=RAW_DELTA)
        yield StreamChunk(tool_call_delta=RAW_ARGS)
        yield StreamChunk(usage={"total_tokens": 9})

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def _chunks(client):
    with client.stream("POST", "/v1/chat/completions", json={
            "model": "smart", "stream": True,
            "messages": [{"role": "user", "content": "weather?"}]}) as r:
        body = "".join(r.iter_text())
    out = []
    for line in body.splitlines():
        if line.startswith("data: ") and line[6:].strip() != "[DONE]":
            out.append(json.loads(line[6:]))
    return out


def _tool_deltas(chunks):
    return [c["choices"][0]["delta"]["tool_calls"][0]
            for c in chunks if c.get("choices")
            and c["choices"][0]["delta"].get("tool_calls")]


def test_the_providers_own_tool_call_dict_is_forwarded_untouched(tmp_path, monkeypatch):
    deltas = _tool_deltas(_chunks(_client(tmp_path, monkeypatch)))
    assert deltas[0] == RAW_DELTA
    assert deltas[1] == RAW_ARGS


def test_nothing_the_provider_sent_is_dropped(tmp_path, monkeypatch):
    first = _tool_deltas(_chunks(_client(tmp_path, monkeypatch)))[0]
    assert first["cache_control"] == {"type": "ephemeral"}
    assert first["type"] == "function"


def test_the_argument_delta_keeps_its_index_without_an_id(tmp_path, monkeypatch):
    second = _tool_deltas(_chunks(_client(tmp_path, monkeypatch)))[1]
    assert second["index"] == 0
    assert "id" not in second
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_tool_call_passthrough.py -q`
Expected: FAIL — the forwarded dict is the rebuilt four-field one; the
`cache_control` and `index` assertions fail.

- [ ] **Step 3: Write the implementation**

In `flexrouter/_router.py`, make sure `field` is imported
(`from dataclasses import dataclass, field`) and change the dataclass:

```python
@dataclass
class ToolCallDeltaEvent:
    index: int
    id: Optional[str]
    name: Optional[str]
    arguments: Optional[str]
    raw: dict = field(default_factory=dict)
    """The provider's own delta dictionary, unmodified.

    The four fields above are a lossy reading of it, kept because DoneEvent
    assembly and existing library callers use them. Anything forwarding this
    to a client must forward `raw`: LiteLLM's tool-call drops come from
    re-parsing, and the four fields are exactly that re-parse.
    """
```

At the construction site in `agenerate_stream`, add the raw dict:

```python
                    events.append(ToolCallDeltaEvent(
                        index=idx,
                        id=tc.get("id"),
                        name=fn.get("name"),
                        arguments=fn.get("arguments"),
                        raw=tc,
                    ))
```

In `flexrouter/app.py`, add the fallback helper next to `_sse_error`:

```python
def _tool_call_delta(event) -> dict:
    """The lossy rebuild, kept only for events that carry no raw dictionary."""
    delta: dict = {"index": event.index}
    if event.id is not None:
        delta["id"] = event.id
        delta["type"] = "function"
    if event.name is not None:
        delta["function"] = {"name": event.name}
    if event.arguments is not None:
        delta.setdefault("function", {})["arguments"] = event.arguments
    return delta
```

and replace the whole `ToolCallDeltaEvent` branch of `_stream_chat` with:

```python
            elif isinstance(event, ToolCallDeltaEvent):
                # Forwarded exactly as the provider wrote it. Rebuilding it
                # from the parsed fields is how LiteLLM loses tool calls
                # (BerriAI/litellm#17246), and the spec names that explicitly.
                # `raw` is empty only for an event built by older code; fall
                # back to the parsed fields in that case.
                delta = dict(event.raw) if event.raw else _tool_call_delta(event)
                yield chunk([{"index": 0, "delta": {"tool_calls": [delta]},
                              "finish_reason": None}])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_tool_call_passthrough.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 5: Run the existing stream tests**

Run: `python -m pytest tests/test_agenerate_stream.py tests/test_app.py -q`
Expected: PASS. If a test asserts the old rebuilt shape (`{"id": ..., "type":
"function", "function": {...}}` with no `index`), it was asserting the bug —
update it to the raw shape and say so in the commit message.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/_router.py flexrouter/app.py tests/test_tool_call_passthrough.py
git commit -m "fix(stream): forward tool-call deltas as the provider wrote them"
```

---

### Task 7: One well-formed error chunk after the first delta

Before the first delta, a failure takes the normal routing failover path — that
already works, and this task locks it in with a test. After the first delta, the
stream must emit one chunk a client can parse, then `[DONE]`, then close, and it
must never switch models mid-answer.

**Files:**
- Modify: `flexrouter/app.py` (`_stream_chat`)
- Test: `tests/test_stream_failure.py`

**Interfaces:**
- Consumes: Task 5's `scrub`, Task 6's `chunk()` closure.
- Produces: `_stream_chat` gains an `error_chunk(message, error_type, code)`
  closure emitting a full chunk envelope with an `error` key and
  `finish_reason: "error"`, used once any content has already gone out.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stream_failure.py
import json

from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.client import ProviderError, StreamChunk
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [
            ModelConfig(provider="alpha", model="big", score=99),
            ModelConfig(provider="beta", model="small", score=40),
        ]},
        providers={
            "alpha": ProviderConfig(base_url="https://alpha.test/v1", api_keys=["k"]),
            "beta": ProviderConfig(base_url="https://beta.test/v1", api_keys=["k"]),
        },
        state_dir=str(tmp_path / "state"),
    )


def _run(tmp_path, monkeypatch, fake_stream):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))
    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    from flexrouter import app as app_mod
    app_mod.state.router = None
    client = TestClient(create_app(str(tmp_path / "config.yaml")))
    with client.stream("POST", "/v1/chat/completions", json={
            "model": "smart", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}) as r:
        return "".join(r.iter_text())


def _parsed(body):
    return [json.loads(l[6:]) for l in body.splitlines()
            if l.startswith("data: ") and l[6:].strip() != "[DONE]"]


def test_failing_before_the_first_delta_fails_over_to_the_other_model(tmp_path, monkeypatch):
    seen: list = []

    async def fake(self, route, messages, **kwargs):
        seen.append(route.provider)
        if route.provider == "alpha":
            raise ProviderError("alpha/big: upstream fell over")
        yield StreamChunk(content="hello")

    body = _run(tmp_path, monkeypatch, fake)
    assert seen == ["alpha", "beta"]
    assert "hello" in body
    assert '"error"' not in body


def test_failing_after_the_first_delta_never_switches_model(tmp_path, monkeypatch):
    seen: list = []

    async def fake(self, route, messages, **kwargs):
        seen.append(route.provider)
        yield StreamChunk(content="par")
        raise ProviderError("alpha/big: connection dropped mid-answer")

    body = _run(tmp_path, monkeypatch, fake)
    assert seen == ["alpha"], "mid-stream failover is a stated non-goal"
    assert "par" in body


def test_the_error_chunk_is_a_well_formed_chunk(tmp_path, monkeypatch):
    async def fake(self, route, messages, **kwargs):
        yield StreamChunk(content="par")
        raise ProviderError("alpha/big: connection dropped mid-answer")

    last = _parsed(_run(tmp_path, monkeypatch, fake))[-1]
    assert last["object"] == "chat.completion.chunk"
    assert last["id"].startswith("chatcmpl-")
    assert last["choices"][0]["finish_reason"] == "error"
    assert last["choices"][0]["delta"] == {}
    assert "connection dropped mid-answer" in last["error"]["message"]


def test_the_error_chunk_shares_the_id_of_the_content_chunks(tmp_path, monkeypatch):
    async def fake(self, route, messages, **kwargs):
        yield StreamChunk(content="par")
        raise ProviderError("alpha/big: dropped")

    chunks = _parsed(_run(tmp_path, monkeypatch, fake))
    assert len({c["id"] for c in chunks}) == 1


def test_the_stream_ends_with_the_done_marker_exactly_once(tmp_path, monkeypatch):
    async def fake(self, route, messages, **kwargs):
        yield StreamChunk(content="par")
        raise ProviderError("alpha/big: dropped")

    body = _run(tmp_path, monkeypatch, fake)
    assert body.count("data: [DONE]") == 1
    assert body.rstrip().endswith("data: [DONE]")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_stream_failure.py -q`
Expected: the two shape tests fail — the error arrives as a bare
`{"error": {...}}` with no `id`, `object` or `choices`.

- [ ] **Step 3: Write the implementation**

In `_stream_chat`, add an error-chunk builder alongside the existing `chunk()`
closure:

```python
    def error_chunk(message: str, error_type: str = "server_error",
                    code: str | None = None) -> str:
        """One chunk a client can actually parse, carrying the failure.

        A bare {"error": ...} object is what this used to send; a client that
        expects every payload to be a chat.completion.chunk throws on it and
        shows the user nothing, losing the partial answer that already
        arrived. This uses the same envelope as every other chunk, so a client
        that ignores unknown keys renders the partial answer and stops
        cleanly.
        """
        err: dict = {"message": scrub(message), "type": error_type}
        if code is not None:
            err["code"] = code
        return chunk([{"index": 0, "delta": {}, "finish_reason": "error"}],
                     error=err)
```

Track whether any content has already gone out. Next to the existing
`first = True`, add:

```python
        emitted = False       # at least one content chunk has gone out
```

Set `emitted = True` immediately after each `yield chunk([...])` that carries
content, reasoning or a tool call.

Then in every `except` handler at the bottom of `_stream_chat` — `RouterBusy`,
`RouterError`, `KeyError` and the generic `Exception` — and in the timeout
branch inside the loop, choose between the two shapes:

```python
    except RouterBusy as exc:
        if emitted:
            yield error_chunk(str(exc), "server_error", "provider_unavailable")
        else:
            yield _sse_error(str(exc), "server_error", "provider_unavailable")
        yield "data: [DONE]\n\n"
```

Keep `_sse_error` in the module: before any content has gone out the stream has
no identity worth asserting, and both shapes are covered by tests.

The "never switches model" behaviour needs no new code: `agenerate_stream`
already stops retrying once the provider stream has yielded, because
`client.stream_chat` raises mid-iteration rather than returning. The test
locks that in.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_stream_failure.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`

- [ ] **Step 6: Commit**

```bash
git add flexrouter/app.py tests/test_stream_failure.py
git commit -m "feat(stream): one parseable error chunk when a stream dies mid-answer"
```

---

### Task 8: The optional local key

**Files:**
- Modify: `flexrouter/config.py` (parse `settings.auth_token`; mask it),
  `flexrouter/app.py` (the guard), `flexrouter/overrides.py` (keep it out of
  `ALLOWED_FIELDS`), `flexrouter/home.py` (`STARTER_CONFIG` comment)
- Test: `tests/test_local_token.py`

The spec's other half of this bullet — "bound to `127.0.0.1` by default" — is
already done: `flexrouter/cli.py:51` passes `host="127.0.0.1"`. Verify it, do
not change it, and do not add a setting for it.

**Interfaces:**
- Consumes: `redact.scrub` (Task 5).
- Produces: `FlexConfig.auth_token: str | None = None`;
  `app._check_token(request) -> JSONResponse | None` — `None` when the request
  may proceed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_local_token.py
from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path, token=None):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
        auth_token=token,
    )


def _client(tmp_path, monkeypatch, token):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path, token))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


SECRET = "flx-AAAABBBBCCCCDDDDEEEEFFFF1234"


def test_no_token_configured_means_no_guard(tmp_path, monkeypatch):
    assert _client(tmp_path, monkeypatch, None).get("/v1/models").status_code == 200


def test_a_configured_token_is_required(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch, SECRET).get("/v1/models")
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["type"] == "invalid_request_error"
    assert err["code"] == "invalid_api_key"


def test_the_right_token_gets_in(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch, SECRET).get(
        "/v1/models", headers={"Authorization": f"Bearer {SECRET}"})
    assert r.status_code == 200


def test_the_wrong_token_does_not(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch, SECRET).get(
        "/v1/models", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_the_rejection_never_repeats_the_token_back(tmp_path, monkeypatch):
    nearly = SECRET[:-4] + "xxxx"
    r = _client(tmp_path, monkeypatch, SECRET).get(
        "/v1/models", headers={"Authorization": f"Bearer {nearly}"})
    assert SECRET not in r.text
    assert SECRET[:-4] not in r.text


def test_chat_is_guarded_too(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch, SECRET).post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


def test_the_dashboard_stays_open_on_loopback(tmp_path, monkeypatch):
    # A browser pointed at the dashboard cannot carry a bearer header, and the
    # whole surface is bound to 127.0.0.1. ADR 0009.
    assert _client(tmp_path, monkeypatch, SECRET).get("/api/status").status_code == 200
```

Add one more test in whichever existing file covers settings masking (find it
with `grep -rln "mask_settings\|redact_settings_text" tests/`), asserting that
a settings structure carrying `auth_token` comes back masked and that the raw
value does not appear in the masked output. Match that file's existing style.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_local_token.py -q`
Expected: FAIL — `TypeError: FlexConfig.__init__() got an unexpected keyword
argument 'auth_token'`.

- [ ] **Step 3: Write the implementation**

In `flexrouter/config.py`, add the field to `FlexConfig` (after `hooks`):

```python
    auth_token: str | None = None
    """An optional key this machine's service requires on every /v1 request.

    Hand-written in the settings file and never written by the tool, like
    everything else in there. It is a credential, so it is masked everywhere
    settings are shown and it is not in overrides.ALLOWED_FIELDS — a key that
    could be set from the dashboard could be set by anything that reached the
    dashboard.
    """
```

In `load_config`, alongside the other settings reads:

```python
    auth_token = settings.get("auth_token") or None
```

and pass `auth_token=auth_token` into the `FlexConfig(...)` construction.

Extend the existing settings-masking function (around line 206 of `config.py`)
so `settings.auth_token` is masked the same way a provider key is: find the
branch that already recognises credential field names and add `auth_token` to
it. Do the same in `redact_settings_text` so the raw file text is redacted too.

In `flexrouter/overrides.py`, confirm `auth_token` is **not** in
`ALLOWED_FIELDS["settings"]`. If the allow-list is derived from `FlexConfig`'s
fields rather than written out by hand, add an explicit exclusion with a
comment saying why.

In `flexrouter/app.py`:

```python
import hmac

_UNAUTHORIZED = ("This flexrouter needs a key. Send it as an Authorization "
                 "header: Bearer <your key>. It is the auth_token line in "
                 "your settings.")


def _check_token(request: Request):
    """None when the request may proceed, an error response when it may not.

    Guards /v1 only. The dashboard and its data stay open: a browser has no
    way to carry this header, and the service binds to 127.0.0.1. ADR 0009.
    """
    expected = get_router()._cfg.auth_token
    if not expected:
        return None
    header = request.headers.get("authorization") or ""
    prefix = "Bearer "
    given = header[len(prefix):] if header.startswith(prefix) else ""
    # compare_digest, not ==, so how long this takes says nothing about how
    # much of the key was right.
    if given and hmac.compare_digest(given, expected):
        return None
    # The message contains neither key, right or wrong.
    return openai_error(_UNAUTHORIZED, "invalid_request_error",
                        "invalid_api_key", 401)
```

Wire it in as middleware scoped to `/v1/`, inside `create_app`, so the error
body stays exactly the OpenAI envelope (a FastAPI dependency raising
`HTTPException` would wrap it in `{"detail": ...}` instead):

```python
    @app.middleware("http")
    async def _guard_v1(request: Request, call_next):
        if request.url.path.startswith("/v1/"):
            denied = _check_token(request)
            if denied is not None:
                return denied
        return await call_next(request)
```

In `flexrouter/home.py`, add to `STARTER_CONFIG` under `settings:`:

```
  # Optional. Set this and every app pointed at flexrouter must send it as
  # its API key. Leave it out and anything on this machine can use the
  # service. Your provider keys do not go here - they live in keys.json.
  # auth_token: pick-something-long
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_local_token.py -q`
Expected: PASS, 7 tests.

- [ ] **Step 5: Prove nothing writes it back**

Run: `python -m pytest tests/test_overrides.py -q` and
`grep -rn "auth_token" flexrouter/`
Expected: `auth_token` appears only in `config.py`, `app.py` and the starter
comment in `home.py`. If it appears anywhere that writes a file, that is a bug.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/config.py flexrouter/app.py flexrouter/home.py flexrouter/overrides.py tests/
git commit -m "feat(api): optional local key on the chat surface"
```

---

### Task 9: The library becomes a client

The class that routes stays where it is and is renamed `LocalRouter`, because
the service runs it. The name `FlexRouter` moves to a new module and means an
HTTP client from here on.

**Files:**
- Modify: `flexrouter/_router.py` (rename the class), `flexrouter/app.py`
  (import `LocalRouter`), `flexrouter/__init__.py`, `flexrouter/exceptions.py`
- Create: `flexrouter/_client_router.py`
- Test: `tests/test_client_router.py`
- Modify: every test importing `flexrouter._router.FlexRouter`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `exceptions.ServiceNotRunning(RouterError)`
  - `_client_router.FlexRouter(config_path: str | None = None, *, base_url: str | None = None, timeout: float = 300.0)`
    with `generate(messages, tier, wait=True, vision=False, session_id=None, **kwargs) -> dict`,
    `async agenerate(...) -> dict`,
    `async agenerate_stream(...) -> AsyncIterator[StreamEvent]`,
    `reload() -> None`, `close() -> None`, and the context-manager methods.
  - `_router.LocalRouter` — the old class, same behaviour.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_client_router.py
import json

import httpx
import pytest

from flexrouter import FlexRouter, RouterError, ServiceNotRunning
from flexrouter._router import DeltaEvent, DoneEvent, ToolCallDeltaEvent

BASE = "http://127.0.0.1:4891"


def _router(handler):
    r = FlexRouter(base_url=BASE)
    r._http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                base_url=BASE)
    return r


def _sse(lines):
    return httpx.Response(200, text="\n\n".join(lines) + "\n\n",
                          headers={"content-type": "text/event-stream"})


async def test_agenerate_returns_what_the_service_returned():
    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert body["model"] == "smart"
        assert body["stream"] is False
        return httpx.Response(200, json={"choices": [
            {"message": {"role": "assistant", "content": "hi"}}]})

    out = await _router(handler).agenerate([{"role": "user", "content": "yo"}], "smart")
    assert out["choices"][0]["message"]["content"] == "hi"


async def test_the_tier_argument_becomes_the_model_name():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": []})

    await _router(handler).agenerate([], "fast")
    assert seen["model"] == "fast"


async def test_a_pinned_model_passes_straight_through():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": []})

    await _router(handler).agenerate([], "groq/llama-3.3")
    assert seen["model"] == "groq/llama-3.3"


async def test_extra_arguments_are_forwarded():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": []})

    await _router(handler).agenerate([], "smart", temperature=0.2,
                                     tools=[{"type": "function"}])
    assert seen["temperature"] == 0.2
    assert seen["tools"] == [{"type": "function"}]


async def test_a_service_error_is_raised_with_the_services_words():
    def handler(request):
        return httpx.Response(404, json={"error": {
            "message": "There is no bucket named 'nope'.",
            "type": "invalid_request_error", "code": "model_not_found"}})

    with pytest.raises(RouterError) as exc:
        await _router(handler).agenerate([], "nope")
    assert "no bucket named 'nope'" in str(exc.value)


async def test_a_dead_service_raises_one_clear_error():
    def handler(request):
        raise httpx.ConnectError("nothing listening")

    with pytest.raises(ServiceNotRunning) as exc:
        await _router(handler).agenerate([], "smart")
    message = str(exc.value)
    assert "flexrouter isn't running" in message
    assert "flexrouter serve" in message
    assert BASE in message


async def test_a_dead_service_never_routes_locally(monkeypatch):
    called = []
    monkeypatch.setattr("flexrouter._router.LocalRouter.agenerate",
                        lambda *a, **k: called.append(1))

    def handler(request):
        raise httpx.ConnectError("nothing listening")

    with pytest.raises(ServiceNotRunning):
        await _router(handler).agenerate([], "smart")
    assert called == [], "a silent local fallback resurrects per-project state"


async def test_stream_events_are_rebuilt_from_the_wire():
    lines = [
        'data: {"id":"c1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{"content":"he"},"finish_reason":null}]}',
        'data: {"id":"c1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{"content":"llo"},"finish_reason":null}]}',
        'data: {"id":"c1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]

    def handler(request):
        assert json.loads(request.content)["stream"] is True
        return _sse(lines)

    events = [e async for e in _router(handler).agenerate_stream([], "smart")]
    assert [e.text for e in events if isinstance(e, DeltaEvent)] == ["he", "llo"]
    assert isinstance(events[-1], DoneEvent)
    assert events[-1].result["choices"][0]["message"]["content"] == "hello"


async def test_tool_call_deltas_keep_the_providers_dictionary():
    raw = {"index": 0, "id": "call_1", "type": "function",
           "function": {"name": "f", "arguments": "{}"}}
    lines = [
        'data: {"id":"c1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{"tool_calls":[' + json.dumps(raw) + ']},'
        '"finish_reason":null}]}',
        'data: {"id":"c1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
        "data: [DONE]",
    ]

    def handler(request):
        return _sse(lines)

    events = [e async for e in _router(handler).agenerate_stream([], "smart")]
    tc = next(e for e in events if isinstance(e, ToolCallDeltaEvent))
    assert tc.raw == raw
    assert tc.name == "f"
    done = events[-1]
    assert done.result["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "f"


async def test_an_error_chunk_mid_stream_raises_after_the_deltas():
    lines = [
        'data: {"id":"c1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{"content":"par"},"finish_reason":null}]}',
        'data: {"id":"c1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{},"finish_reason":"error"}],'
        '"error":{"message":"dropped","type":"server_error"}}',
        "data: [DONE]",
    ]

    def handler(request):
        return _sse(lines)

    seen = []
    with pytest.raises(RouterError) as exc:
        async for e in _router(handler).agenerate_stream([], "smart"):
            seen.append(e)
    assert [e.text for e in seen if isinstance(e, DeltaEvent)] == ["par"]
    assert "dropped" in str(exc.value)


async def test_the_client_sends_the_local_key_when_one_is_set():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": []})

    router = _router(handler)
    router._token = "flx-secret-token-value-here"
    await router.agenerate([], "smart")
    assert seen["auth"] == "Bearer flx-secret-token-value-here"


def test_reload_is_a_no_op_that_does_not_explode():
    FlexRouter(base_url=BASE).reload()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_client_router.py -q`
Expected: FAIL — `ImportError: cannot import name 'ServiceNotRunning'`.

- [ ] **Step 3: Rename the routing class**

In `flexrouter/_router.py`:
- `class FlexRouter:` becomes `class LocalRouter:`
- `def __enter__(self) -> "FlexRouter":` becomes `-> "LocalRouter"`
- Add **no** `FlexRouter = LocalRouter` alias. An alias would let old code keep
  routing in-process without noticing, which is the exact fault this stage
  removes.

In `flexrouter/app.py`, `get_router` becomes:

```python
        from flexrouter._router import LocalRouter
        state.router = LocalRouter(state.config_path)
```

Update the docstring at the top of `app.py` that says "shared a single
FlexRouter" to say `LocalRouter`.

In every test that imports `FlexRouter` **and expects in-process routing**,
change the import to `LocalRouter`. The affected files are
`tests/test_agenerate_stream.py`, `tests/test_flexrouter.py`,
`tests/test_hot_reload.py`, `tests/test_quarantine.py`,
`tests/test_quota_integration.py`, `tests/test_router_wiring.py`,
`tests/test_shared_home_e2e.py`, `tests/test_pinned_routing.py`.
`tests/test_app.py` and `tests/test_home.py` reach it only through the service
and may need no change — check rather than assuming.

- [ ] **Step 4: Add the error**

In `flexrouter/exceptions.py`:

```python
class ServiceNotRunning(RouterError):
    """Nothing is listening where the flexrouter service should be.

    A subclass of RouterError so code that already catches RouterError keeps
    working. Deliberately not handled by starting the service: auto-start was
    rejected twice, and a silent local fallback would give every process its
    own settings and its own private idea of how much of each provider's
    allowance was left.
    """
```

- [ ] **Step 5: Write the client**

```python
# flexrouter/_client_router.py
"""The importable FlexRouter: a thin client for the service on this machine.

It used to route in-process. That is what gave every project its own copy of
the settings, its own keys, and its own private idea of how much of each
provider's allowance was left — the fault the whole of v2 exists to fix. The
routing code still exists and is still what runs; it is just on the other end
of a local connection now, in one process, with one set of books.

Method signatures are unchanged on purpose, so existing code keeps working
without an edit.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional

import httpx

from flexrouter._router import (
    DeltaEvent, DoneEvent, ReasoningDeltaEvent, StreamEvent, ToolCallDeltaEvent,
)
from flexrouter.exceptions import RouterBusy, RouterError, ServiceNotRunning

_NOT_RUNNING = """flexrouter isn't running. Start it with:

    flexrouter serve

(then this will work). Checked {base_url} - nothing listening."""


class FlexRouter:
    def __init__(self, config_path: Optional[str] = None, *,
                 base_url: Optional[str] = None,
                 timeout: float = 300.0) -> None:
        from flexrouter import home
        from flexrouter.config import load_config

        # The port and the optional local key both live in the settings this
        # machine shares. Reading them is the only thing the client still
        # needs the settings file for: it does not read providers, models or
        # provider keys, and it never routes.
        try:
            cfg = load_config(config_path)
            port = cfg.port or home.DEFAULT_PORT
            self._token = cfg.auth_token
        except Exception:
            # No settings yet is not this class's problem to report. Either
            # the service will say so, or the connection fails with the
            # message above, which is the one the owner can act on.
            port, self._token = home.DEFAULT_PORT, None

        self._base_url = base_url or f"http://127.0.0.1:{port}"
        self._http = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -- the wire ---------------------------------------------------------

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def _payload(self, messages: list[dict], tier: str, stream: bool,
                 kwargs: dict) -> dict:
        # `tier` is the parameter's public name and always has been; on the
        # wire the same string is `model`, and it may name a bucket or one
        # specific provider's model. See flexrouter/wire.py.
        return {"model": tier, "messages": messages, "stream": stream, **kwargs}

    def _raise_for(self, response: httpx.Response) -> None:
        try:
            err = response.json().get("error") or {}
        except Exception:
            err = {}
        message = err.get("message") or response.text or f"HTTP {response.status_code}"
        if response.status_code == 503:
            raise RouterBusy(message)
        raise RouterError(message)

    # -- the same three methods as before ---------------------------------

    async def agenerate(self, messages: list[dict], tier: str,
                        wait: bool = True, vision: bool = False,
                        session_id: Optional[str] = None, **kwargs) -> dict:
        payload = self._payload(messages, tier, False, kwargs)
        try:
            response = await self._http.post("/v1/chat/completions",
                                             json=payload, headers=self._headers())
        except httpx.ConnectError as exc:
            raise ServiceNotRunning(
                _NOT_RUNNING.format(base_url=self._base_url)) from exc
        if response.status_code >= 400:
            self._raise_for(response)
        return response.json()

    async def agenerate_stream(self, messages: list[dict], tier: str,
                               vision: bool = False,
                               session_id: Optional[str] = None,
                               **kwargs) -> AsyncIterator[StreamEvent]:
        payload = self._payload(messages, tier, True, kwargs)
        request = self._http.build_request("POST", "/v1/chat/completions",
                                           json=payload, headers=self._headers())
        try:
            response = await self._http.send(request, stream=True)
        except httpx.ConnectError as exc:
            raise ServiceNotRunning(
                _NOT_RUNNING.format(base_url=self._base_url)) from exc

        try:
            if response.status_code >= 400:
                await response.aread()
                self._raise_for(response)

            text_parts: list[str] = []
            tool_calls: list[dict] = []
            finish = "stop"
            failure: Optional[str] = None

            async for line in response.aiter_lines():
                line = line.strip()
                if not line.startswith("data: "):
                    continue
                data = line[len("data: "):].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)

                if chunk.get("error"):
                    # The service's one error chunk. Everything already
                    # yielded stays yielded; the caller finds out by this
                    # generator raising once the stream ends, which is what a
                    # partial answer that failed actually is.
                    failure = chunk["error"].get("message") or "stream failed"
                    continue

                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                if choices[0].get("finish_reason"):
                    finish = choices[0]["finish_reason"]

                if delta.get("content"):
                    text_parts.append(delta["content"])
                    yield DeltaEvent(text=delta["content"])
                if delta.get("reasoning_content"):
                    yield ReasoningDeltaEvent(text=delta["reasoning_content"])
                for raw in delta.get("tool_calls") or []:
                    fn = raw.get("function") or {}
                    tool_calls.append(raw)
                    yield ToolCallDeltaEvent(
                        index=raw.get("index", 0), id=raw.get("id"),
                        name=fn.get("name"), arguments=fn.get("arguments"),
                        raw=raw)

            if failure is not None:
                raise RouterError(failure)

            message: dict = {"role": "assistant", "content": "".join(text_parts)}
            if tool_calls:
                message["tool_calls"] = _assemble(tool_calls)
            yield DoneEvent(result={
                "choices": [{"index": 0, "message": message,
                             "finish_reason": finish}]})
        finally:
            await response.aclose()

    def generate(self, messages: list[dict], tier: str, wait: bool = True,
                 vision: bool = False, session_id: Optional[str] = None,
                 **kwargs) -> dict:
        return self._run(self.agenerate(messages, tier, wait=wait, vision=vision,
                                        session_id=session_id, **kwargs))

    # -- housekeeping -----------------------------------------------------

    def _run(self, coro):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(coro)

    def reload(self) -> None:
        """Nothing to reload. The service picks up settings changes itself.

        Kept on the class so code written against the old in-process router
        keeps running unchanged.
        """
        return None

    def close(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.run_until_complete(self._http.aclose())
            self._loop.close()

    def __enter__(self) -> "FlexRouter":
        return self

    def __exit__(self, *_) -> None:
        self.close()


def _assemble(deltas: list[dict]) -> list[dict]:
    """Stitch streamed tool-call fragments back into whole calls.

    Keyed by the provider's own index, and each fragment's dictionary is
    merged rather than read for four known fields, so a provider-specific key
    survives into the assembled call.
    """
    calls: dict[int, dict] = {}
    for raw in deltas:
        idx = raw.get("index", 0)
        call = calls.setdefault(idx, {"id": "", "type": "function",
                                      "function": {"name": "", "arguments": ""}})
        for key, value in raw.items():
            if key in ("index", "function"):
                continue
            if value:
                call[key] = value
        fn = raw.get("function") or {}
        if fn.get("name"):
            call["function"]["name"] = fn["name"]
        if fn.get("arguments"):
            call["function"]["arguments"] += fn["arguments"]
    return [calls[k] for k in sorted(calls)]
```

In `flexrouter/__init__.py`, change the imports:

```python
from flexrouter._client_router import FlexRouter
from flexrouter._router import (
    AttemptEvent, AttemptFailedEvent, DeltaEvent, DoneEvent, LocalRouter,
    ReasoningDeltaEvent, ToolCallDeltaEvent,
)
from flexrouter.exceptions import (
    ConfigError, ConfigFieldError, ContextWindowWarning, RouterBusy,
    RouterError, ServiceNotRunning,
)
```

and add `"LocalRouter"` and `"ServiceNotRunning"` to `__all__`, keeping it
alphabetical as it already is.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_client_router.py -q`
Expected: PASS, 12 tests.

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest -q`
Expected: every previously passing test still passes. Anything still importing
`FlexRouter` and expecting in-process routing fails loudly — that is the point;
fix the import to `LocalRouter`.

- [ ] **Step 8: Commit**

```bash
git add flexrouter/_client_router.py flexrouter/_router.py flexrouter/app.py flexrouter/__init__.py flexrouter/exceptions.py tests/
git commit -m "feat(library): FlexRouter talks to the service instead of routing itself"
```

---

### Task 10: Prove it end to end, and write down what was decided

**Files:**
- Create: `tests/test_stage2_e2e.py`,
  `docs/adr/0009-wire-vocabulary-and-the-client-library.md`
- Modify: `CONTEXT.md`, and whichever docs show `FlexRouter` usage

**Interfaces:**
- Consumes: everything.
- Produces: no new names.

- [ ] **Step 1: Write the end-to-end test**

```python
# tests/test_stage2_e2e.py
"""The library, over a real socket, against the real service."""
import threading
import time

import pytest
import uvicorn

from flexrouter import FlexRouter, ServiceNotRunning
from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig

PORT = 4899


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
        port=PORT,
    )


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "pong"},
                             "finish_reason": "stop"}],
                "usage": {"total_tokens": 2}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    from flexrouter import app as app_mod
    app_mod.state.router = None
    server = uvicorn.Server(uvicorn.Config(
        create_app(str(tmp_path / "config.yaml")), host="127.0.0.1", port=PORT,
        log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "the service did not come up"
    yield f"http://127.0.0.1:{PORT}"
    server.should_exit = True
    thread.join(timeout=5)


def test_the_library_reaches_the_service(service):
    router = FlexRouter(base_url=service)
    out = router.generate([{"role": "user", "content": "ping"}], "smart")
    assert out["choices"][0]["message"]["content"] == "pong"
    router.close()


def test_nothing_listening_says_how_to_start_it():
    router = FlexRouter(base_url="http://127.0.0.1:4898")
    with pytest.raises(ServiceNotRunning) as exc:
        router.generate([{"role": "user", "content": "ping"}], "smart")
    assert "flexrouter serve" in str(exc.value)
    router.close()
```

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/test_stage2_e2e.py -q`
Expected: PASS, 2 tests. If `uvicorn` is not importable, check
`pyproject.toml` — it is already a runtime dependency because
`flexrouter serve` uses it.

- [ ] **Step 3: Write the ADR**

Create `docs/adr/0009-wire-vocabulary-and-the-client-library.md` following the
shape of `docs/adr/0008-*`. It records the six rulings from the top of this
plan, each with what it costs:

- Buckets advertised by plain name; old ids accepted forever. **Cost:** the
  parsing helper has legacy branches that can never be deleted, because a chat
  client's saved model name is not something we can migrate.
- A `/` means one specific model. **Cost:** a bucket may never be named with a
  slash in it, and nothing enforces that yet.
- Pinning uses a shadow engine rather than a change to `engine.py`. **Cost:**
  one extra `RoutingEngine` per router and a second config object, rebuilt on
  every reload; the two engines' configs can drift if a future change updates
  one and forgets the other.
- The local key guards the chat surface only. **Cost:** anything that can reach
  the dashboard on this machine can read the settings (masked) and change
  overrides without the key.
- The client emits no routing-progress events. **Cost:** a caller that showed
  "trying provider 2 of 3" loses it, and nothing replaces it until the request
  trace lands in Stage 3.
- `reload()` is a no-op on the client. **Cost:** it silently does nothing; a
  caller depending on it is not told.

Also record the redaction trade from Task 5: error text is scrubbed bluntly, so
a long model name in a provider's message comes back shortened. **Cost:** some
error messages read worse. **Why anyway:** a missed key is unrecoverable and a
shortened model name is not.

- [ ] **Step 4: Update CONTEXT.md**

Add these entries to the glossary in the existing style, and update the "Not
yet built" section to say Stages 1 and 2 are implemented:

- **Bucket** — extend the existing entry: on the wire a bucket is named plainly
  (`smart`); the older `auto-smart` and `smart::provider/model` spellings are
  still accepted on input and are no longer advertised (ADR 0009).
- **Pinned model** — a request naming `provider/model` instead of a bucket goes
  to exactly that model, with no failover to another. Served by a second
  routing engine over a one-model-per-bucket shadow config sharing the live
  rate-limit, penalty and quota stores (`LocalRouter._build_pin_engine`).
- **The service** — the one process, on port 4891 by default, that does all the
  routing for this machine (`flexrouter serve`, `flexrouter/app.py`).
- **The client library** — `FlexRouter`, which sends requests to the service
  and never routes. `LocalRouter` in `flexrouter/_router.py` is the routing
  code the service runs; importing it and using it directly reintroduces the
  per-project state this design removed.
- **Local key** — the optional `auth_token` in settings. When set, every chat
  request must carry it. Never written by the tool, masked wherever settings
  are shown, and not overridable from the dashboard.

- [ ] **Step 5: Update the usage docs**

Run: `grep -rn "FlexRouter" README.md docs/ --include=*.md`
Every example showing `FlexRouter()` as an in-process router needs a line above
it saying the service must be running, and the `flexrouter serve` command. Do
not change the examples' code — the signatures are the same.

- [ ] **Step 6: Run the whole suite one last time**

Run: `python -m pytest -q`
Expected: every test passes. Record the exact number in the commit message.

- [ ] **Step 7: Commit**

```bash
git add tests/test_stage2_e2e.py docs/ CONTEXT.md README.md
git commit -m "docs: record the stage 2 rulings and prove the library end to end"
```

---

## After the plan

1. Run the broad review (`superpowers:requesting-code-review`) across the whole
   branch, not per task. Stage 1's reviewers found two secret leaks and a
   command that silently did nothing; none were caught by implementer
   self-review. Budget for it.
2. File anything deferred as an issue under `.scratch/v2-stage2-followups/`,
   following `docs/agents/issue-tracker.md`, rather than only mentioning it in
   a final message.
3. There is **no git remote**, so a pull request is not an option. Finish with
   `superpowers:finishing-a-development-branch` and choose between a local
   merge into `master` and keeping the branch.
4. Tell the owner, in plain everyday English and in a few short lines, what was
   decided for him — described by what it means for him, not by its mechanism.
