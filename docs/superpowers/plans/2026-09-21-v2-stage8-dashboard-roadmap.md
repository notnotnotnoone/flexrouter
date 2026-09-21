# v2 Stage 8 — the dashboard: sub-plan roadmap

> Stage 8 is not sized like Stages 3–7. The v2 roadmap flagged this before any
> of them were built: it is a full visual application, closer in scope to
> several other stages combined. It is therefore broken into seven sub-plans,
> each with its own task list, its own per-task reviews, its own final review,
> and its own merge into `master`.

**Spec:** `docs/superpowers/specs/2026-09-18-flexrouter-v2-design.md` §7
**Stage roadmap:** `docs/superpowers/plans/2026-09-18-v2-roadmap.md`
**Approved mock-up:** `claude.ai/artifact/DNVgao1ibNhXfbPsEP48xw`
**Owner-approved nine-area skeleton:** `claude.ai/artifact/Q13nnZgB3nrHkMNFoXXj8M`

---

## Rulings taken before building (record, do not re-ask)

These were decided by the controller and stated plainly to the owner. Each
becomes an ADR when the sub-plan that depends on it lands.

### R1 — Nine areas, not one screen

The spec's §7 describes one screen. The owner reviewed a nine-area skeleton
on 2026-09-21 and approved it (*"THIS IS GREAT!"*), asking for more data,
more charts and more controls. Stage 8 therefore builds nine areas:
Overview, Providers & keys, Models, Buckets, Requests, What's broken, Error
brain, Allowance, Settings.

**Cost:** roughly three times the screen surface §7 alone implies. Mitigated
by the fact that four of the nine (What's broken, Error brain, Allowance,
and half of Models) are unfiltered views of tables the Providers area must
build anyway.

### R2 — Plain server-rendered HTML. The React/Vite front end is retired.

The existing dashboard is React 18 + Vite + Tailwind 4 + shadcn, built into
`flexrouter/dashboard/static/`. It is **not** used for v2.

Reasons, in order of weight:

1. **It requires a build step nobody runs.** Stage 1 changed the settings
   endpoint's contract and the bundle was never rebuilt; the shipped bundle
   has been stale since (`.scratch/v2-stage1-followups/issues/05-frontend-not-rebuilt.md`).
   A dashboard that can silently disagree with its own service is the exact
   failure mode Stage 7 was nearly shipped with.
2. **Its tests do not run in the Python suite.** Nothing in `pytest` exercises
   a single line of it. Server-rendered pages are tested by the existing
   suite through `TestClient`, so the dashboard gets the same gate as
   everything else in the project.
3. **The owner asked for "purely wireframe … least effort and most
   robust"** (2026-09-21) and intends to restyle it himself later. One CSS
   file over semantic HTML is a far easier restyling target than a React
   app he cannot build.
4. **No new dependency.** No template engine either — a handful of escaping
   helpers in `render.py`. FastAPI already ships `HTMLResponse`.

**Cost, stated honestly:**
- Anything genuinely live (a progress bar ticking, a table updating without
  a reload) needs either a page refresh or a small piece of hand-written
  JavaScript. Accepted: nothing in the nine areas needs sub-second liveness,
  and a `<meta refresh>` covers the Overview.
- Rich client-side interactions (drag-to-reorder a bucket) become
  button-per-row instead. Accepted; the skeleton already shows up/down
  buttons rather than drag handles.
- The existing chart work in `dashboard/frontend/src/components/charts/` is
  discarded. Charts are re-drawn as inline SVG generated in Python. Accepted:
  those charts are v1-shaped and would have been rewritten regardless.

**`dashboard/frontend/` is not deleted in the first sub-plan.** It stops
being served in sub-plan 1 and is deleted in sub-plan 7, once the
replacement demonstrably covers everything. Deleting it earlier would remove
the only reference for anything overlooked.

### R3 — No JavaScript for navigation or forms

Every area is a real URL. Every control is a real `<form>` that posts and
redirects. JavaScript is permitted only for genuinely optional polish and
never for anything load-bearing. This is what makes the wireframe robust:
there is no client state to get out of sync, and every page is testable with
one `TestClient` call.

### R4 — The owner's settings file stays read-only to the machine

The owner's instruction was *"I WANT EVERYTHING THAT I CAN CHANGE VIA VSCODE
TO BE CHANGABLE HERE."* This collides with the standing rule (spec §1,
`docs/adr/`) that nothing may write `config.yaml`.

**Resolution:** the rule stands; the screen writes to `overrides.json`, which
layers on top. Every control on every settings screen renders a provenance
marker — *typed by you* vs *changed here* — and a "put it back" action that
removes the override. The owner was told this plainly and did not object.

**Cost:** `overrides.py`'s `ALLOWED_FIELDS` must be widened (sub-plan 7), and
three things `overrides.json` cannot express at all today — adding a model,
adding a bucket, adding a provider — need genuinely new representation. That
work is sub-plan 7 and is the largest single piece of new backend in Stage 8.

### R5 — `auth_token` stays out of the screen

The local password an app must send is deliberately absent from
`ALLOWED_FIELDS` ("a key that could be set from the dashboard could be set by
anything that reached the dashboard"). Stage 8 keeps it that way, with one
narrowing exception considered and **rejected for now**: a "make a new one"
button that rotates it to a fresh random value without accepting input.

**Cost:** the owner still edits that one value by hand. He was told and
offered the unlock; if he takes it, it is a sub-plan 7 task, not a silent
change.

### R6 — Money is out of scope; "Spend" becomes "Allowance"

Nothing in the service records cost. A Spend area would show £0.00 forever
and require new accounting to show anything else. The area is renamed
Allowance and shows free-tier headroom, which *is* already tracked.

### R7 — The pending catalogue tray ships read-only first

`.scratch/v2-stage7-followups/issues/01-...` is real and blocks the accept /
reject buttons. Sub-plans 3 and 4 render the tray read-only. Sub-plan 7
closes the follow-up and wires the buttons. This is the explicit decision
the Stage 8 handoff demanded be made and recorded rather than left open.

### R8 — `engine.py` is still not touched

Unchanged from Stages 2–7. Every fact the screens need is reachable by
reading objects `LocalRouter` already builds (`_engine`, `_penalties`,
`_key_states`, `_error_brain`, `_model_facts`, `_traces`, `_quota_tracker`)
or by calling `explain_unavailable()`, which exists for exactly this.

---

## The seven sub-plans

Each produces working, merged software on its own.

| # | Sub-plan | Delivers | Depends on |
|---|---|---|---|
| 1 | **Frame and Overview** | `facts.py`, `render.py`, `pages.py`; nine real URLs; Overview built; the other eight honest stubs; React no longer served | — |
| 2 | **What's broken** | the two-pile problem list, shared by the Overview's "wants you" strip | 1 |
| 3 | **Providers & keys** | provider table, per-provider detail, every key reading, real-request test buttons | 1, 2 |
| 4 | **Models** | the wide model table, capability rendering by source, the read-only pending tray | 1, 3 |
| 5 | **Buckets** | bucket cards, ordering, the "who would answer and why" explainer | 1, 4 |
| 6 | **Requests, Error brain, Allowance** | the three remaining read-mostly areas | 1, 2 |
| 7 | **Settings and writing back** | widened `ALLOWED_FIELDS`; add a model / bucket / provider; pending accept-reject; `dashboard/frontend/` deleted | all |

**Charts** are inline SVG generated in Python, added to each area by the
sub-plan that owns that area, not built up front. A shared `charts.py` is
created by whichever sub-plan needs the second chart, not the first.

---

## Why this order

Sub-plan 1 proves the whole approach end to end on the cheapest possible
area, and makes navigation real immediately so nothing later has to guess at
the frame. Sub-plan 2 comes second because the "what's broken" facts are
consumed by four later areas — building it early stops it being reinvented.
Sub-plan 7 is last because it is the only one that writes, and writing is
the only part that can damage anything.

Sub-plans are written one at a time, immediately before execution — not all
seven up front. A sub-plan written now against a code shape three sub-plans
away would be stale before anyone read it. This matches how Stages 3–7 were
each planned.
