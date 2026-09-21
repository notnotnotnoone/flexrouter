# Stage 8, sub-plan 1 — the frame and the Overview

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stale React bundle with plain server-rendered wireframe pages: nine real URLs sharing one frame, with the Overview area genuinely built and the other eight honest stubs.

**Architecture:** Three new modules under `flexrouter/dashboard/`. `facts.py` reads a `LocalRouter` and returns plain dicts and dataclasses — no HTML, no writes. `render.py` holds escaping and small HTML builders — no knowledge of flexrouter. `pages.py` joins them into FastAPI routes returning `HTMLResponse`. `app.py`'s SPA catch-all is replaced by explicit page routes. `/v1/*` and `/api/*` are untouched.

**Tech Stack:** Python 3.11+, FastAPI, Starlette `HTMLResponse`, pytest. **No new dependency. No template engine. No JavaScript.**

**Spec:** `docs/superpowers/specs/2026-09-18-flexrouter-v2-design.md` (§7)
**Sub-plan roadmap:** `docs/superpowers/plans/2026-09-21-v2-stage8-dashboard-roadmap.md`

## Global Constraints

Copied forward from Stages 3–7. These have not changed; every task's requirements implicitly include them.

- **`flexrouter/engine.py` is reused unchanged.** So are `recovery.py`, `window.py`, `quota.py`, `rate_limits.py`, `errors.py`, `client.py`, and `redact.py`. Compute what you need from outside them, or feed a signal through an interface they already read. Six stages found exactly one justified exception; that is the bar, not a precedent.
- **Nothing may write `config.yaml`.** Machine-made changes go to `overrides.json`. This sub-plan writes nothing at all.
- **No secret in full, anywhere.** `flexrouter/redact.py`'s `scrub()` is the only sanctioned mechanism. Provider-derived text in `error_brain.json` and trace entries is already scrubbed before it reaches disk, so it does not need scrubbing again — but every such string is still HTML-escaped on the way out (see Task 2).
- **`ModelConfig` requires `rpm` and `tpm` with no defaults,** and needs a real `context_window` in fixtures.
- **`respx` and FastAPI's `TestClient` collide in this repo.** Endpoint tests monkeypatch `flexrouter.client.AsyncClient.chat` / `.stream_chat` instead. This sub-plan's tests make no outbound calls, so the issue should not arise — if you find yourself reaching for `respx`, stop and reconsider the test.
- **`tests/conftest.py` has an autouse fixture pointing `FLEXROUTER_HOME` at a temp directory.** Do not remove it.
- **Run pytest synchronously.** Bash tool, `run_in_background` unset, `timeout` set to `600000`. Every stage of this run had at least one implementer background pytest and stall forever waiting for a notification that never came. The full suite currently takes 6–7 minutes and 744 tests pass with 1 skipped; that is expected, not a regression.
- **Never `git add -A` or `git add .`** — name files explicitly. `README.md` holds an uncommitted rewrite belonging to the owner. Do not stage, revert, or touch it. Check `git status` first, every time.

---

## File structure

| File | Responsibility |
|---|---|
| `flexrouter/dashboard/facts.py` (create) | Read-only views over a `LocalRouter`. Returns dataclasses and dicts. Imports nothing from `render.py` or FastAPI. |
| `flexrouter/dashboard/render.py` (create) | HTML escaping and small builders (page shell, nav, tiles, tables). Knows nothing about flexrouter. |
| `flexrouter/dashboard/pages.py` (create) | FastAPI `APIRouter` returning `HTMLResponse`. Joins facts to render. |
| `flexrouter/dashboard/wire.css` (create) | One stylesheet, deliberately plain. Served as a static file. |
| `flexrouter/app.py` (modify) | Replace the SPA catch-all with `app.include_router(pages)`. |
| `tests/test_dashboard_facts.py` (create) | Unit tests for `facts.py`. |
| `tests/test_dashboard_render.py` (create) | Unit tests for `render.py`, escaping especially. |
| `tests/test_dashboard_pages.py` (create) | `TestClient` tests for every one of the nine URLs. |

---

### Task 1: `render.py` — escaping and the page shell

Built first because it has no dependencies and every later task consumes it.

**Files:**
- Create: `flexrouter/dashboard/render.py`
- Test: `tests/test_dashboard_render.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `esc(value: object) -> str` — HTML-escapes any value, including quotes.
  - `attrs(mapping: dict) -> str` — renders escaped HTML attributes, skipping `None`/`False` values, rendering `True` as a bare attribute.
  - `tag(name: str, body: str = "", **kw) -> str` — one element; keyword `cls` maps to `class`.
  - `page(title: str, current: str, body: str) -> str` — the full document: the nine-area nav, a `<main>`, the stylesheet link.
  - `AREAS: list[tuple[str, str, str]]` — `(slug, label, group)` for all nine, in menu order.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_render.py`:

```python
from flexrouter.dashboard import render


def test_esc_escapes_angle_brackets_and_quotes():
    assert render.esc('<a href="x">&') == "&lt;a href=&quot;x&quot;&gt;&amp;"


def test_esc_handles_non_strings():
    assert render.esc(42) == "42"
    assert render.esc(None) == ""


def test_attrs_skips_none_and_false():
    out = render.attrs({"id": "a", "hidden": False, "title": None})
    assert out == ' id="a"'


def test_attrs_renders_true_as_bare_attribute():
    assert render.attrs({"hidden": True}) == " hidden"


def test_attrs_escapes_values():
    assert render.attrs({"title": 'he said "hi"'}) == ' title="he said &quot;hi&quot;"'


def test_tag_maps_cls_to_class():
    assert render.tag("p", "hi", cls="note") == '<p class="note">hi</p>'


def test_tag_does_not_escape_its_body():
    # The body is already-rendered HTML; callers escape their own text.
    assert render.tag("div", "<b>x</b>") == "<div><b>x</b></div>"


def test_areas_lists_all_nine_in_menu_order():
    slugs = [slug for slug, _, _ in render.AREAS]
    assert slugs == [
        "overview", "providers", "models", "buckets",
        "requests", "broken", "brain", "allowance", "settings",
    ]


def test_page_marks_the_current_area():
    html = render.page("Overview", "overview", "<p>hi</p>")
    assert 'aria-current="page"' in html
    assert html.count('aria-current="page"') == 1


def test_page_links_every_area():
    html = render.page("Overview", "overview", "")
    for slug, _, _ in render.AREAS:
        expected = 'href="/"' if slug == "overview" else f'href="/{slug}"'
        assert expected in html, slug


def test_page_escapes_its_title():
    html = render.page('<script>', "overview", "")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_page_is_a_complete_document():
    html = render.page("Overview", "overview", "<p>hi</p>")
    assert html.startswith("<!doctype html>")
    assert "</html>" in html
    assert "<p>hi</p>" in html
```

- [ ] **Step 2: Run it and watch it fail**

```bash
python -m pytest tests/test_dashboard_render.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'flexrouter.dashboard.render'`.

- [ ] **Step 3: Write `render.py`**

Create `flexrouter/dashboard/render.py`:

```python
"""HTML building blocks for the dashboard.

Deliberately not a template engine. The dashboard is a wireframe the owner
intends to restyle himself, so the markup stays semantic and the whole of
its appearance lives in one stylesheet. Nothing here knows anything about
flexrouter - it takes strings and returns strings.

Escaping rule: `esc` is applied to every value that came from outside this
module. `tag` does NOT escape its body, because a body is already-rendered
HTML; whoever builds that body escapes the text going into it.
"""
from __future__ import annotations

from html import escape

# (slug, label, group) for the nine areas, in the order they appear in the
# menu. The group is the heading a slug sits under.
AREAS: list[tuple[str, str, str]] = [
    ("overview", "Overview", "The router"),
    ("providers", "Providers & keys", "The router"),
    ("models", "Models", "The router"),
    ("buckets", "Buckets", "The router"),
    ("requests", "Requests", "Traffic"),
    ("broken", "What's broken", "Traffic"),
    ("brain", "Error brain", "Traffic"),
    ("allowance", "Allowance", "Traffic"),
    ("settings", "Settings", "System"),
]


def esc(value: object) -> str:
    """HTML-escape any value, quotes included. `None` becomes empty."""
    if value is None:
        return ""
    return escape(str(value), quote=True)


def attrs(mapping: dict) -> str:
    """Render HTML attributes, leading space included when non-empty.

    `None` and `False` drop the attribute entirely; `True` renders it bare.
    """
    out = []
    for name, value in mapping.items():
        if value is None or value is False:
            continue
        if value is True:
            out.append(f" {esc(name)}")
        else:
            out.append(f' {esc(name)}="{esc(value)}"')
    return "".join(out)


def tag(name: str, body: str = "", **kw) -> str:
    """One element. `cls=` renders as `class=`. The body is NOT escaped."""
    if "cls" in kw:
        kw["class"] = kw.pop("cls")
    return f"<{name}{attrs(kw)}>{body}</{name}>"


def _nav(current: str) -> str:
    out = []
    seen_group = None
    for slug, label, group in AREAS:
        if group != seen_group:
            out.append(tag("h2", esc(group), cls="nav-group"))
            seen_group = group
        here = slug == current
        out.append(tag(
            "a", esc(label),
            # The Overview lives at the root, not at /overview, so that the
            # address the owner is given is just the service's own address.
            href="/" if slug == "overview" else f"/{slug}",
            **{"aria-current": "page" if here else None},
        ))
    return tag("nav", "".join(out), cls="nav")


def page(title: str, current: str, body: str) -> str:
    """A complete document. `body` is already-rendered HTML."""
    return (
        "<!doctype html>"
        '<html lang="en">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)} - flexrouter</title>"
        '<link rel="stylesheet" href="/wire.css">'
        "</head>"
        "<body>"
        + tag("div", _nav(current) + tag("main", body, cls="main"), cls="shell")
        + "</body></html>"
    )
```

- [ ] **Step 4: Run it and watch it pass**

```bash
python -m pytest tests/test_dashboard_render.py -q
```

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add flexrouter/dashboard/render.py tests/test_dashboard_render.py
git commit -m "feat(dashboard): plain HTML building blocks, with escaping"
```

---

### Task 2: `facts.py` — what the Overview reads

**Files:**
- Create: `flexrouter/dashboard/facts.py`
- Test: `tests/test_dashboard_facts.py`

**Interfaces:**
- Consumes: nothing from Task 1. Reads a `LocalRouter`'s `_cfg`, `_penalties`, `_key_states`, `_error_brain`, `_model_facts`.
- Produces:
  - `@dataclass ProviderSummary` with fields `name, base_url, key_count, keys_live, keys_cooling, keys_parked, models_total, quarantined, quarantine_reason, state`.
  - `provider_summaries(router, now: float | None = None) -> list[ProviderSummary]`
  - `overview(router, now: float | None = None) -> dict` with keys `providers`, `keys`, `models`, `learned`, `service`.

**Reading the key records:** a provider's keys are `ProviderConfig.keys`, a list of `KeyRecord` (`flexrouter/keys.py`) each with `id`, `label`, `enabled`. Per-key live state comes from `router._key_states.get(provider, key_id, now)`, a `KeyState` whose `status` is one of `"live"`, `"cooling"`, `"benched"`, `"disabled"`. A provider whose `api_keys` is non-empty but whose `keys` list is empty (inline or env-sourced credentials) counts its keys from `api_keys` and reports them all live — `KeyStateStore` has nothing recorded for a key with no id.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_facts.py`:

```python
import pytest

from flexrouter._router import LocalRouter
from flexrouter.dashboard import facts


@pytest.fixture
def router(config_file):
    r = LocalRouter(str(config_file))
    yield r
    r.close()


def test_provider_summaries_lists_every_configured_provider(router):
    names = [p.name for p in facts.provider_summaries(router)]
    assert names == ["groq"]


def test_provider_summary_reports_base_url_and_model_count(router):
    summary = facts.provider_summaries(router)[0]
    assert summary.base_url == "https://api.groq.com/openai/v1"
    assert summary.models_total == 1


def test_provider_with_an_env_key_counts_it_as_live(router):
    summary = facts.provider_summaries(router)[0]
    assert summary.key_count == 1
    assert summary.keys_live == 1
    assert summary.keys_cooling == 0
    assert summary.keys_parked == 0


def test_healthy_provider_reads_as_ok(router):
    assert facts.provider_summaries(router)[0].state == "ok"


def test_quarantined_provider_reads_as_bad_and_carries_its_reason(router):
    router._engine._penalties.quarantine_provider("groq", "key rejected")
    summary = facts.provider_summaries(router)[0]
    assert summary.state == "bad"
    assert summary.quarantined is True
    assert summary.quarantine_reason == "key rejected"


def test_overview_counts_providers_by_state(router):
    out = facts.overview(router)
    assert out["providers"] == {"total": 1, "ok": 1, "warn": 0, "bad": 0}


def test_overview_counts_keys(router):
    out = facts.overview(router)
    assert out["keys"]["total"] == 1
    assert out["keys"]["live"] == 1


def test_overview_counts_models_and_how_many_are_reachable(router):
    out = facts.overview(router)
    assert out["models"]["total"] == 1
    assert out["models"]["available"] == 1


def test_overview_counts_a_quarantined_model_as_unavailable(router):
    router._engine._penalties.quarantine("groq", "llama-3.1-8b-instant", "gone")
    out = facts.overview(router)
    assert out["models"]["total"] == 1
    assert out["models"]["available"] == 0


def test_overview_reports_what_has_been_learned(router):
    out = facts.overview(router)
    assert out["learned"]["error_kinds"] == 0
    assert out["learned"]["awaiting_you"] == 0
    assert out["learned"]["models_with_facts"] == 0


def test_overview_names_the_buckets_and_the_state_directory(router):
    out = facts.overview(router)
    assert out["service"]["buckets"] == ["low"]
    assert out["service"]["state_dir"].endswith(".flexrouter")


def test_overview_never_returns_a_secret(router):
    import json
    blob = json.dumps(facts.overview(router), default=str)
    assert "test-key" not in blob
```

- [ ] **Step 2: Run it and watch it fail**

```bash
python -m pytest tests/test_dashboard_facts.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'flexrouter.dashboard.facts'`.

- [ ] **Step 3: Write `facts.py`**

Create `flexrouter/dashboard/facts.py`:

```python
"""Read-only views of a running router, for the dashboard to render.

Every fact here is computed from objects `LocalRouter` already builds. This
module never writes, never calls a provider, and never teaches `engine.py`
anything - the established pattern across ADRs 0009-0014.

It returns plain data. Nothing here knows what HTML is.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ProviderSummary:
    name: str
    base_url: str
    key_count: int
    keys_live: int
    keys_cooling: int
    keys_parked: int
    models_total: int
    quarantined: bool
    quarantine_reason: Optional[str]
    state: str  # "ok" | "warn" | "bad"


def _models_of(router, provider: str) -> list:
    """Every configured model belonging to one provider, across all buckets."""
    seen = []
    for model_configs in router._cfg.tiers.values():
        for mc in model_configs:
            if mc.provider == provider and mc.model not in seen:
                seen.append(mc.model)
    return seen


def provider_summaries(router, now: Optional[float] = None) -> list[ProviderSummary]:
    penalties = router._engine._penalties
    states = router._key_states
    out = []
    for name, pcfg in router._cfg.providers.items():
        records = list(pcfg.keys or [])
        if records:
            live = cooling = parked = 0
            for record in records:
                status = states.get(name, record.id, now).status
                if not record.enabled or status in ("benched", "disabled"):
                    parked += 1
                elif status == "cooling":
                    cooling += 1
                else:
                    live += 1
            key_count = len(records)
        else:
            # Credentials supplied inline or from the environment carry no
            # id, so `KeyStateStore` has nothing recorded for them.
            key_count = live = len(pcfg.api_keys or [])
            cooling = parked = 0

        quarantined = penalties.is_quarantined(name, "*")
        reason = penalties.quarantine_reason(name, "*")
        if quarantined:
            state = "bad"
        elif key_count and live == 0:
            state = "bad"
        elif cooling or parked:
            state = "warn"
        else:
            state = "ok"

        out.append(ProviderSummary(
            name=name,
            base_url=pcfg.base_url,
            key_count=key_count,
            keys_live=live,
            keys_cooling=cooling,
            keys_parked=parked,
            models_total=len(_models_of(router, name)),
            quarantined=quarantined,
            quarantine_reason=reason,
            state=state,
        ))
    return out


def overview(router, now: Optional[float] = None) -> dict:
    """Everything the front page shows."""
    summaries = provider_summaries(router, now)
    penalties = router._engine._penalties

    models_total = 0
    models_available = 0
    for name in router._cfg.providers:
        for model in _models_of(router, name):
            models_total += 1
            if not (penalties.is_quarantined(name, model)
                    or penalties.is_quarantined(name, "*")
                    or penalties.is_penalized(name, model)):
                models_available += 1

    entries = getattr(router._error_brain, "_entries", {}) or {}
    awaiting = sum(1 for e in entries.values()
                   if getattr(e, "flagged_for_review", False))

    facts_store = getattr(router._model_facts, "_facts", {}) or {}

    return {
        "providers": {
            "total": len(summaries),
            "ok": sum(1 for s in summaries if s.state == "ok"),
            "warn": sum(1 for s in summaries if s.state == "warn"),
            "bad": sum(1 for s in summaries if s.state == "bad"),
        },
        "keys": {
            "total": sum(s.key_count for s in summaries),
            "live": sum(s.keys_live for s in summaries),
            "cooling": sum(s.keys_cooling for s in summaries),
            "parked": sum(s.keys_parked for s in summaries),
        },
        "models": {"total": models_total, "available": models_available},
        "learned": {
            "error_kinds": len(entries),
            "awaiting_you": awaiting,
            "models_with_facts": len(facts_store),
        },
        "service": {
            "buckets": list(router._cfg.tiers),
            "state_dir": router._cfg.state_dir,
            "port": router._cfg.port or router._cfg.dashboard_port,
        },
    }
```

- [ ] **Step 4: Run it and watch it pass**

```bash
python -m pytest tests/test_dashboard_facts.py -q
```

Expected: 12 passed.

**If `_entries` or `_facts` is not the real attribute name** on `ErrorBrain` / `ModelFactsStore`, read those two files and use whatever they actually call their in-memory store. Do not add a new public method to either class to satisfy this — reading a private attribute from a sibling module inside the same package is the smaller change, and both stores load eagerly in `__init__`. Note the correction in your handoff back.

- [ ] **Step 5: Commit**

```bash
git add flexrouter/dashboard/facts.py tests/test_dashboard_facts.py
git commit -m "feat(dashboard): read-only view of providers, keys and models"
```

---

### Task 3: `wire.css` — the deliberately plain stylesheet

**Files:**
- Create: `flexrouter/dashboard/wire.css`

No test: a stylesheet has no behaviour. Task 4 asserts it is served.

- [ ] **Step 1: Write the stylesheet**

Create `flexrouter/dashboard/wire.css`:

```css
/* A wireframe, on purpose. The owner restyles this himself later; the job
   here is legible structure, not appearance. System fonts only - no network
   request, nothing to fail. */

* { box-sizing: border-box; }

body {
  margin: 0;
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  color: #111;
  background: #fff;
}

.shell { display: flex; align-items: flex-start; }

.nav {
  width: 190px;
  flex: none;
  padding: 16px 10px;
  border-right: 1px solid #ccc;
  min-height: 100vh;
}
.nav h2 {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: #666;
  margin: 14px 0 4px;
}
.nav h2:first-child { margin-top: 0; }
.nav a {
  display: block;
  padding: 4px 6px;
  color: #06c;
  text-decoration: none;
}
.nav a:hover { text-decoration: underline; }
.nav a[aria-current] { font-weight: 700; color: #111; background: #eee; }

.main { flex: 1; min-width: 0; padding: 16px 20px 60px; }
.main h1 { font-size: 20px; margin: 0 0 4px; }
.main h3 { font-size: 14px; margin: 22px 0 6px; }
.lede { color: #444; margin: 0 0 18px; max-width: 70ch; }

table { border-collapse: collapse; margin: 8px 0; }
th, td { border: 1px solid #ccc; padding: 4px 8px; text-align: left; font-size: 13px; }
th { background: #eee; font-weight: 600; }

.tiles { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
.tile { border: 1px solid #ccc; padding: 8px 10px; min-width: 130px; }
.tile b { display: block; font-size: 18px; }
.tile span { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: #666; }

.state-ok::before    { content: "[ok] "; }
.state-warn::before  { content: "[!] "; }
.state-bad::before   { content: "[X] "; }

.stub { border: 1px dashed #999; padding: 14px; color: #444; max-width: 70ch; }
.note { color: #666; font-size: 12px; max-width: 70ch; }
```

- [ ] **Step 2: Commit**

```bash
git add flexrouter/dashboard/wire.css
git commit -m "feat(dashboard): the wireframe stylesheet"
```

---

### Task 4: `pages.py` and wiring — nine real URLs

The largest task, and deliberately one task: the routes, the stylesheet route, the `app.py` change and the removal of the SPA fallback are one reviewable deliverable. Half of it merged alone leaves the dashboard unreachable.

**Files:**
- Create: `flexrouter/dashboard/pages.py`
- Modify: `flexrouter/app.py` — replace the `@app.get("/{full_path:path}")` SPA handler
- Test: `tests/test_dashboard_pages.py`

**Interfaces:**
- Consumes: `render.page`, `render.tag`, `render.esc`, `render.AREAS` (Task 1); `facts.overview`, `facts.provider_summaries` (Task 2).
- Produces: `pages` — a FastAPI `APIRouter` with no prefix, carrying `GET /`, `GET /{slug}` for the eight remaining areas, and `GET /wire.css`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_pages.py`:

```python
import pytest
from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.dashboard.render import AREAS


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def test_overview_is_served_at_the_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_every_area_has_a_page(client):
    for slug, _, _ in AREAS:
        path = "/" if slug == "overview" else f"/{slug}"
        assert client.get(path).status_code == 200, slug


def test_every_page_carries_the_whole_menu(client):
    body = client.get("/").text
    for _, label, _ in AREAS:
        assert label.replace("&", "&amp;").replace("'", "&#x27;") in body


def test_the_stylesheet_is_served(client):
    r = client.get("/wire.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]


def test_overview_shows_the_real_counts(client):
    body = client.get("/").text
    assert "Providers" in body
    assert "Models" in body


def test_unbuilt_areas_say_so_rather_than_pretending(client):
    body = client.get("/models").text
    assert "not built yet" in body.lower()


def test_an_unknown_path_is_a_404_not_the_dashboard(client):
    r = client.get("/no-such-page")
    assert r.status_code == 404


def test_the_openai_surface_still_works(client):
    assert client.get("/v1/models").status_code == 200


def test_the_private_api_still_works(client):
    assert client.get("/api/providers").status_code == 200


def test_a_provider_name_is_escaped_not_injected(client, config_file):
    # Guards the whole rendering approach: a provider named with markup must
    # not reach the browser as markup.
    import yaml
    raw = yaml.safe_load(config_file.read_text())
    raw["providers"]["<script>bad</script>"] = {
        "base_url": "https://example.invalid/v1",
        "api_keys": [{"env": "GROQ_API_KEY"}],
    }
    config_file.write_text(yaml.dump(raw))
    with TestClient(create_app(str(config_file))) as c:
        body = c.get("/").text
    assert "<script>bad</script>" not in body
```

- [ ] **Step 2: Run it and watch it fail**

```bash
python -m pytest tests/test_dashboard_pages.py -q
```

Expected: `ModuleNotFoundError: No module named 'flexrouter.dashboard.pages'`.

- [ ] **Step 3: Write `pages.py`**

Create `flexrouter/dashboard/pages.py`:

```python
"""The dashboard's pages.

Server-rendered HTML, no build step, no JavaScript. Every area is a real URL
and every future control will be a real form, so there is no client state
that can disagree with the service - see the Stage 8 roadmap, ruling R2.

Areas not yet built return an honest stub rather than an empty shell. Each
lands in its own sub-plan.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from flexrouter.dashboard import facts
from flexrouter.dashboard.render import AREAS, esc, page, tag

pages = APIRouter()

CSS_PATH = Path(__file__).parent / "wire.css"

# Which sub-plan builds each area, so a stub can say something true.
_PLANNED = {
    "providers": "sub-plan 3",
    "models": "sub-plan 4",
    "buckets": "sub-plan 5",
    "requests": "sub-plan 6",
    "broken": "sub-plan 2",
    "brain": "sub-plan 6",
    "allowance": "sub-plan 6",
    "settings": "sub-plan 7",
}


def _tile(label: str, value: object, note: str = "") -> str:
    return tag(
        "div",
        tag("span", esc(label)) + tag("b", esc(value)) + tag("small", esc(note)),
        cls="tile",
    )


def _overview_body(router) -> str:
    data = facts.overview(router)
    p, k, m, learned = data["providers"], data["keys"], data["models"], data["learned"]

    tiles = tag("div", "".join([
        _tile("Providers", p["total"],
              f"{p['ok']} fine, {p['warn']} unsettled, {p['bad']} down"),
        _tile("Keys held", k["total"],
              f"{k['live']} in use, {k['cooling']} resting, {k['parked']} parked"),
        _tile("Models", f"{m['available']} of {m['total']}", "reachable right now"),
        _tile("Failures understood", learned["error_kinds"],
              f"{learned['awaiting_you']} waiting for you"),
        _tile("Models it has learned about", learned["models_with_facts"], ""),
    ]), cls="tiles")

    rows = [tag("tr", "".join([
        tag("th", "Provider"), tag("th", "State"), tag("th", "Address"),
        tag("th", "Keys"), tag("th", "Models"), tag("th", "Why"),
    ]))]
    for s in facts.provider_summaries(router):
        rows.append(tag("tr", "".join([
            tag("td", esc(s.name)),
            tag("td", esc(s.state), cls=f"state-{s.state}"),
            tag("td", esc(s.base_url)),
            tag("td", esc(f"{s.keys_live} of {s.key_count} in use")),
            tag("td", esc(s.models_total)),
            tag("td", esc(s.quarantine_reason or "")),
        ])))

    buckets = ", ".join(data["service"]["buckets"]) or "none set up"

    return (
        tag("h1", "Overview")
        + tag("p", "Everything at a glance. Apps point here instead of at "
                   "OpenAI, and this is what the router can reach for them.",
              cls="lede")
        + tiles
        + tag("h3", "Every provider")
        + tag("table", "".join(rows))
        + tag("h3", "Buckets your apps can ask for")
        + tag("p", esc(buckets))
        + tag("p", "The problems only you can fix will appear here once "
                   "What's broken is built.", cls="note")
    )


def _stub_body(slug: str, label: str) -> str:
    where = _PLANNED.get(slug, "a later sub-plan")
    return (
        tag("h1", esc(label))
        + tag("div",
              tag("p", f"This area is not built yet. It arrives in {esc(where)}.")
              + tag("p", "The menu is real so nothing later has to guess at "
                         "the frame, and so a half-finished dashboard is "
                         "never mistaken for a finished one."),
              cls="stub")
    )


@pages.get("/wire.css", include_in_schema=False)
def stylesheet() -> Response:
    return Response(CSS_PATH.read_text(encoding="utf-8"),
                    media_type="text/css; charset=utf-8")


@pages.get("/", response_class=HTMLResponse, include_in_schema=False)
def overview_page() -> HTMLResponse:
    from flexrouter.app import get_router
    return HTMLResponse(
        page("Overview", "overview", _overview_body(get_router()))
    )


def _register_stub(slug: str, label: str) -> None:
    @pages.get(f"/{slug}", response_class=HTMLResponse,
               include_in_schema=False, name=f"page_{slug}")
    def _handler() -> HTMLResponse:
        return HTMLResponse(page(label, slug, _stub_body(slug, label)))


for _slug, _label, _ in AREAS:
    if _slug != "overview":
        _register_stub(_slug, _label)
```

- [ ] **Step 4: Wire it into `app.py`**

In `flexrouter/app.py`, add the import near the other dashboard imports at the top of the file:

```python
from flexrouter.dashboard.pages import pages as dashboard_pages
```

Then, inside `create_app`, **delete the entire `@app.get("/{full_path:path}")` SPA handler** and replace it with one line, placed after `app.include_router(api)`:

```python
    app.include_router(dashboard_pages)
```

The `return app` that followed the deleted handler stays.

Removing the catch-all is the point of the change, not a side effect: it is what makes an unknown path a plain 404 instead of silently handing back a stale React bundle. `STATIC_DIR` and its `FileResponse` import become unused — leave them in place for now, they are removed in sub-plan 7 along with `dashboard/frontend/`.

- [ ] **Step 5: Run the new tests**

```bash
python -m pytest tests/test_dashboard_pages.py -q
```

Expected: 10 passed.

- [ ] **Step 6: Run the whole suite**

```bash
python -m pytest -q
```

Run it with the Bash tool, `run_in_background` unset, `timeout` set to `600000`. It takes 6–7 minutes. Expect **744 passed, 1 skipped plus this sub-plan's new tests**, with these exceptions to investigate rather than accept:

- Anything in `tests/test_app.py` asserting the old SPA fallback (a request to an unknown path returning `index.html`, or the 503 "dashboard not built" body). Those assertions are now wrong by design. **Update them to expect a 404** and say so in your commit message. Do not delete a test to make the suite green.
- Anything in `tests/test_dashboard_api.py` — that file covers `/api/*`, which this sub-plan does not touch. A failure there means you changed something you should not have.

- [ ] **Step 7: Commit**

```bash
git add flexrouter/dashboard/pages.py tests/test_dashboard_pages.py flexrouter/app.py
git commit -m "feat(dashboard): nine real pages, server-rendered, no build step"
```

Add `tests/test_app.py` to that `git add` if step 6 required changing it.

---

### Task 5: Make the CLI point at the new dashboard

`flexrouter/cli.py` and the removed SPA handler both told the owner to run `npm run build` when the dashboard was missing. That instruction is now wrong.

**Files:**
- Modify: `flexrouter/cli.py`
- Test: `tests/test_dashboard_pages.py` (append)

- [ ] **Step 1: Find every place that still mentions the build**

```bash
grep -rn "npm run build\|dashboard/frontend\|not built" flexrouter/ --include=*.py
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_dashboard_pages.py`:

```python
def test_nothing_still_tells_the_owner_to_build_the_front_end():
    from pathlib import Path
    import flexrouter
    root = Path(flexrouter.__file__).parent
    offenders = [
        path.name
        for path in root.rglob("*.py")
        if "npm run build" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
```

- [ ] **Step 3: Run it and watch it fail**

```bash
python -m pytest tests/test_dashboard_pages.py::test_nothing_still_tells_the_owner_to_build_the_front_end -q
```

Expected: FAIL, naming whichever file still carries the instruction.

- [ ] **Step 4: Fix each offender**

Replace any "dashboard not built; run `npm run build` in dashboard/frontend" wording with a message that is now true — the dashboard is served by the service itself, so the only thing to say is where it is. For example, where the CLI prints the address on start:

```python
print(f"Dashboard: http://127.0.0.1:{port}/")
```

- [ ] **Step 5: Run it and watch it pass**

```bash
python -m pytest tests/test_dashboard_pages.py -q
```

Expected: 11 passed.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/cli.py tests/test_dashboard_pages.py
git commit -m "fix(dashboard): stop telling the owner to build a front end that no longer exists"
```

---

### Task 6: Prove it against a real running service

Every stage of this run has had at least one thing that passed every test and did not work when actually run — Stage 7's startup refresh most notably. A dashboard is especially easy to ship broken, because a rendering fault raises nothing.

**Files:** none. This task produces evidence, not code.

- [ ] **Step 1: Start the service for real**

```bash
python -m flexrouter.cli serve --port 4899
```

Run it with the Bash tool and `run_in_background: true` — this one genuinely is a long-running server, unlike pytest.

- [ ] **Step 2: Fetch every page and check each is real HTML**

```bash
for p in "" providers models buckets requests broken brain allowance settings wire.css; do printf '%s -> ' "/$p"; curl -s -o /dev/null -w '%{http_code} %{content_type}\n' "http://127.0.0.1:4899/$p"; done
```

Expected: `200 text/html; charset=utf-8` for all nine areas and `200 text/css; charset=utf-8` for the stylesheet. **Any 500 here is the finding this task exists to catch** — `get_router()` behaves differently under a real uvicorn lifespan than under `TestClient`, which is exactly how Stage 7's bug hid.

- [ ] **Step 3: Confirm the Overview shows real data, not an empty frame**

```bash
curl -s http://127.0.0.1:4899/ | grep -c "state-"
```

Expected: at least 1. Zero means the provider table rendered empty.

- [ ] **Step 4: Confirm an unknown path 404s**

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:4899/no-such-page
```

Expected: `404`.

- [ ] **Step 5: Stop the service and record the evidence**

Kill the background process. Paste the actual output of steps 2–4 into your handoff. **Do not claim this task passed without that output** — see `superpowers:verification-before-completion`.

---

## Done when

- All nine URLs return HTML under both `TestClient` and a real `flexrouter serve`.
- The Overview shows real provider, key and model counts from the running router.
- An unknown path returns 404; the stale React bundle is no longer reachable.
- `/v1/*` and `/api/*` are unchanged in behaviour.
- The full suite passes, with any `test_app.py` SPA assertions deliberately updated and called out.
- No secret and no unescaped provider-derived string reaches the page.

## Deferred out of this sub-plan, on purpose

File each under `.scratch/v2-stage8-followups/issues/` if not already covered by the roadmap:

- The Overview's "wants you" strip — sub-plan 2 owns the facts it needs.
- Charts — the first area that needs two of them creates `charts.py`.
- `dashboard/frontend/` deletion and `STATIC_DIR` removal — sub-plan 7.
- A page-refresh interval on the Overview — decide in sub-plan 2, once there is something on it worth watching change.
