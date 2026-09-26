# flexrouter v2.3 — the plan

Written 25 Sep 2026. The honesty and polish bugfix release (current version: 2.2.0).

**Where the detail lives:**
- `.scratch/polish/PRD.md`: the problem, 105 numbered user stories (US-n below), modules, and testing approach.
- `.scratch/polish/grill-decisions.md`: every decision (§0–§19). **This wins if anything disagrees.**
- `.scratch/polish/grill-log.md`: the owner's verbatim answers, the approved mockups, and the sample prompts.
- `.scratch/polish/papercuts.md`: the 35 dashboard papercuts (P-n below).
- `.scratch/polish/evidence/`: the say-hi sweep script, results, traces, and Google's live model IDs.

When every session is ticked, move this file to `docs/archive/` (see `docs/agents/plan-lifecycle.md`).

---

## How to run a session

1. Open a **new** session and paste that session's **Starter prompt**.
2. The agent reads **only** this plan's session block, the listed decision sections, and the files named. Not the whole grill log.
3. While working, run **only the listed tests**. Run the full suite (`uv run pytest`, ~11 min) plus `uv run pytest -m browser` once, at the **end of each phase**.
4. Tick the session's box here when done, and commit.

**⚡ parallel-safe** = touches different files from everything else in its phase, so it can run in its own session at the same time.
**🔗 in order** = edits the routing core (`_router.py`), so run it after the one before it or the sessions collide.
**👤** = needs the owner (design review). **🪶** = mechanical work, so a cheaper model is fine.
**Model:** each session names one. Opus for the routing core and design sessions (1, 3, 6, 7, 8, 18); Sonnet for the rest; Haiku is fine for pure wording. The *what* is fully decided everywhere, and the model only matters for *how*. If a session leaves a choice open, the agent picks one and writes it down in the session block.

Tokens: parallel sessions don't cost fewer tokens, they just finish sooner. Only parallelise ⚡ sessions.

---

## Phase 0 · The showcase

### ☑ Session 1 — Button & widget showcase 👤
**Model:** Opus (design taste)
**Goal:** design every interactive part **before** building, so the phases copy it in instead of designing twice.

**Build** a standalone page in `.scratch/polish/showcase/` (open it straight in a browser, no server):
- **8 button kinds, each with every state** (idle, hover, pressed, working, done, failed with the reason, disabled):

  | Kind | Examples |
  |---|---|
  | main action | Save |
  | one-click fix | [Use gemini-3-flash-preview], [Retry] |
  | run a test | Test key, [Test all] with per-model progress |
  | copy | Copy prompt, Copy curl |
  | dangerous | Remove, Reset, **with undo, not "are you sure?"** |
  | toggle | |
  | expand/collapse | Thinking ▸, Tried first ▸ |
  | drag | model → bucket |

- **New widgets:**
  - status pill (🟢 Ready / 🟡 Busy · back in 42s live countdown / 🟠 Struggling / 🔴 Needs you / ⚪ Off)
  - the one-row-one-button status list with summary strip (the approved mockup in `grill-log.md`)
  - the self-ticking quickstart card (mockup in `grill-log.md` Q18)
  - the request sheet (the Q8 sample)
  - the Allowance provider group (§19 mockup)
  - the toast

**The rule that makes the port copy-paste:**
- plain HTML shaped **exactly** like `flexrouter/dashboard/ui.py` output (same class names and `data-*` attributes)
- colours and spacing only from `flexrouter/dashboard/static/app.css` variables
- plain JS in the style of `static/app.js`
- no React, no Tailwind, no new libraries

**Feel:**
- flashy in *feedback* (spinner → ✓, a shake on fail, the countdown ticking), never in clutter
- everything respects `prefers-reduced-motion`
- light and dark both work

**Done when:**
- [x] Every button and state is pressable on one page.
- [x] Works at 375px and 1014px wide.
- [x] Reduced motion is honoured.
- [x] The owner has pressed everything and signed off.
- [x] A short `showcase/PORTING.md` says which CSS/JS/markup goes where.

**Done 25 Sep 2026.** Choices the plan left open are written in `showcase/PORTING.md` → "Decisions made here". Added after review: four good-news moments (quickstart finished, Test all passing, a Needs-you fix, the last one cleared); everyday buttons stay plain. The showcase lives in `.scratch/` (gitignored, local only).

**Read:** `ui.py`, `app.css` (variables and existing button rules), `app.js` (its conventions), `grill-log.md` mockups. Use the `ui-animation` skill for the motion.

**Starter prompt:**
> Do Session 1 of PLAN-V2.3.md (the button & widget showcase). Read only that session block, the mockups in .scratch/polish/grill-log.md, and the dashboard files it names. Build it, show it to me in the browser pane, and iterate until I sign off.

---

## Phase 1 · Stop lying (backend, no new pages)

### ☑ Session 2 — Honest errors 🔗
**Model:** Sonnet
**Decisions:** §1, §0 bug list. **US:** 1–3, 55–56. **P:** 11, 12.
- Every error returns the upstream status plus the provider's exact text in OpenAI `error.message`, plus `error.flexrouter.request_id` and `error.flexrouter.attempts[]` (model, status, provider_message, ms, waited_ms, verdict). Shape in PRD "Contracts".
- A request ID (`req_…`) on every response header and error, matching the trace ID.
- Never say "tier" to callers again.
- 429 bodies are kept, including Google's quota text.
- Find why provider text still gets mangled (`redact.py` `_LONG_RUN` / `_tail`) even though `redact_errors` is off by default (commit `2b5de2b`). Suspects: the always-on trace scrub (ADR 0010), or rows stored before the change. Fix it so provider text is readable everywhere. Keys flexrouter holds stay masked by exact match.

**Done when:**
- [x] A fake-upstream 404/429/503 test shows the exact provider text and every attempt in the error body.
- [x] The header is present on success and on failure.

**Tests:** the router/app error tests, and a new error-body test.

**Starter prompt:**
> Do Session 2 of PLAN-V2.3.md. Read that block and grill-decisions.md §1. TDD it.

### ☑ Session 3 — Failover with no sleeping 🔗 (after 2)
**Model:** **Opus** (hardest: stream and non-stream copies of the retry loop in `_router.py`)
**Decisions:** §2, §1 (pinned). **US:** 4–12, 15.
- Build a **failover policy** as one pure, table-tested function (§2 table):
  - 429/503 → next model instantly
  - 404/402/403 → next instantly
  - message too long → next model with a bigger context window
  - genuine bad request → return now
  - empty reply → next
- Try **every** model in the bucket, stopping at about **30s total**. Delete all `asyncio.sleep(backoff)` retry sleeps, in both the stream and non-stream paths.
- All failed → one line per model.
- **Pinned** `provider/model`: busy or broken → fail immediately ("429: busy, retry in 40s"), no fallback.
- Don't wait ~4s before failing a model already known to be unusable.
- A model whose provider isn't set up (the zhipu ghosts in overrides) → a clear error, "no zhipu provider set up", not "no bucket or model named…". **Not done** - deferred as a follow-up (out of scope of the failover-loop rewrite; lives in the model-lookup/config-validation path, not `_router.py`'s retry loop).

**Done when:**
- [x] The policy table test passes.
- [x] A fake-upstream bucket with [404, 503, ok] answers in <1s and reports both failures.
- [x] A pinned 429 returns in <1s.

**Starter prompt:**
> Do Session 3 of PLAN-V2.3.md. Read that block and grill-decisions.md §2. TDD the failover policy first.

### ☐ Session 4 — Test budget + reasoning split 🔗 (after 3)
**Model:** Sonnet
**Decisions:** §13, §7. **US:** 13–14, 69. **P:** 34.
- Every test call (key Test in `dashboard/keytest.py`, the rate-limit probe in `dashboard/pages.py` `probe_one`) sends "hi" with **~512 max tokens**, not 1.
- An empty reply with `finish_reason=length` and a small caller `max_tokens` = a **caller budget problem**: no penalty, and the error says "the model used all N tokens thinking, raise max_tokens". It still fails over in buckets. The existing detection is around the empty-response branch in `_router.py`.
- **Reasoning splitter:** move `<thought>…</thought>` / `<think>…</think>` out of `content` into `reasoning_content`. It must be streaming-safe, since tags can split across chunks.

**Done when:**
- [ ] The Gemma-style inline thought is split in both stream and non-stream.
- [ ] The splitter's chunk-boundary tests pass.
- [ ] A `max_tokens:1` probe no longer counts as a model failure.

**Starter prompt:**
> Do Session 4 of PLAN-V2.3.md. Read that block and grill-decisions.md §7 and §13.

### ☐ Session 5 — Always read the real model list ⚡
**Model:** Sonnet
**Decisions:** §12, §6 (validation). **US:** 63–65, 81.
- Split discovery:
  - **Reading** each provider's `GET /models` is always on: at startup, and when "Add models with AI" opens. Read-only, cached.
  - **Auto-add** stays opt-in and off, renamed in Settings to "Add new models automatically". The old `experimental_model_discovery` is still read as an alias.
- A **model-list checker** with `is_real(provider, id)` and `did_you_mean(provider, id) → [ids]` (close matches, e.g. `gemini-3-flash` → `gemini-3-flash-preview`).
- At startup, list configured IDs not in the real list, for Session 8 to show.
- Files: `catalogue.py`, `refresh.py`, `config.py`, the `_router.py` startup refresh, and the gates added in commit `3e0abbc`.

**Done when:**
- [ ] With discovery off, startup still reads the lists and adds nothing.
- [ ] The did-you-mean test uses the real Google IDs in `evidence/google-live-model-ids-2026-09-25.txt`.

**Starter prompt:**
> Do Session 5 of PLAN-V2.3.md. Read that block and grill-decisions.md §6 and §12.

**End of phase 1:**
- [ ] Full suite + browser suite green.
- [ ] Rerun `.scratch/polish/evidence/say_hi.py` against the running service and save the results next to the old ones. Success = every failure states its real reason and nothing waits pointlessly. It is not a pass-rate target.

---

## Phase 2 · One status

### ☐ Session 6 — One status per model and key 🔗
**Model:** **Opus** (replaces 5 mechanisms and migrates state)
**Decisions:** §3, the mapping table at the end of `grill-log.md`. **US:** 16–20, 24–28.
- A **status store** replacing quarantine / penalty / bench / cooling / auto-bench (`recovery.py`, `key_state.py`, the auto-bench, `quarantine.json`, `penalties.json`). Values: `ready | busy | struggling | needs_you | off`, each with a one-sentence `reason`, an `until`, and at most one `action`.
- **Busy:** duration from the provider's retry-after / rate-limit headers, else 60s, never doubling. 503/500 → only ever Busy (no 7-day bench).
- **Struggling:** only for weird failures (empty replies, bad output), ~1h, [Try now].
- **Needs you:** no timer. 402 / 403 / 429 with limit 0 → "Not on your plan" / "Balance empty" immediately. A bad key → Needs you on that key only.
- **Off** = disabled.
- **No "no speed data" skip:** unmeasured models get tried.
- Models whose provider isn't set up → Needs you, "No zhipu provider set up", [Remove].
- Migrate the existing `quarantine.json` / `penalties.json` on first start. Don't strand old state.

**Done when:**
- [ ] Each status transition has a test.
- [ ] `/v1/models` and the facts layer report the new status.
- [ ] Old state files are migrated or ignored cleanly.

**Starter prompt:**
> Do Session 6 of PLAN-V2.3.md. Read that block, grill-decisions.md §3, and the mapping table at the bottom of grill-log.md.

### ☐ Session 7 — Did-you-mean + the error brain decides 🔗 (after 6)
**Model:** **Opus** (timeouts, fake JEV, overturns 2 ADRs)
**Decisions:** §3 (did-you-mean), §4. **US:** 21–23, 30–35.
- A 404 → the model-list checker (Session 5):
  - close match → Needs you, "Did you mean X? [Use it]". [Use it] writes a **new** model to overrides and turns the old one off. No identity patch, no automatic rename.
  - no match → Gone, [Remove], out of routing until the list shows it again.
- **The error brain decides** (overturns ADR 0012):
  - the verdict → status mapping in §4
  - 400s and unrecognised text go to JEV for the deciding vote (this also overturns ADR 0013's "bare 400 = bad_request at 1.0")
  - the fingerprint **drops the model name**
  - the first sighting waits ≤0.5s for JEV, then answers from memory
  - JEV unsure or down → the model's problem, fail over, listed under "Not sure"
- The decider's confidence knobs become internal constants.

**Done when:**
- [ ] The four real 400s in §4 each land on the right status, tested with a fake JEV.
- [ ] A slow or down JEV never blocks for more than 0.5s.
- [ ] [Use it] produces the right overrides.

**Starter prompt:**
> Do Session 7 of PLAN-V2.3.md. Read that block and grill-decisions.md §3 and §4.

### ☐ Session 8 — The status page 👤 (after 1, 6, 7)
**Model:** Opus (design, you review)
**Decisions:** §3 (page), §4 (page), §10. **US:** 29, 36–42. **P:** 1–4, 8, 19.
- One page replaces "What's broken" + "Error brain" (`broken_page.py`, `brain_page.py`, `facts.broken()`), built from **Session 1's showcase parts**:
  - summary strip
  - red rows on top: one plain sentence and one button each
  - Busy rows: countdown only
  - a "Not sure" group
  - click a row → the full provider response and past requests
  - classifier telemetry as a one-line footer
- Overview's "wants you" strip and the Providers/Models status columns use the same statuses. No more "OK" on dead models, and no more "5 of 5 fine" next to "7 need you".
- Redirect the old URLs.

**Done when:**
- [ ] The owner has reviewed it in the browser.
- [ ] Dashboard page tests and browser tests pass.
- [ ] It matches the approved mockup.

**Starter prompt:**
> Do Session 8 of PLAN-V2.3.md. Read that block, the approved mockup in grill-log.md, and .scratch/polish/showcase/PORTING.md. Show me in the browser pane before finishing.

### ☐ Session 9 — Fastest = time to first word ⚡
**Model:** Sonnet
**Decisions:** §14. **US:** 66–68. **P:** 17, 18.
- A speed tracker: median time to first token over the last ~20 **successful** requests per model (non-stream: fall back to total time, or only measure streams; decide and note it).
- The typed `tokens_per_second` is only a starting guess.
- The Buckets page shows "ranked by: first word, your last 20 requests", with seconds per model and "not measured yet, will try".
- "How fast" shows sample counts.
- Files: `engine.py` (`_pick`, the fastest strategy), `buckets_page.py`.

**Starter prompt:**
> Do Session 9 of PLAN-V2.3.md. Read that block and grill-decisions.md §14.

### ☐ Session 10 — Settings cleanup 🔗 (after 3, 6)
**Model:** Sonnet
**Decisions:** §15. **US:** 78–81. **P:** 35.
- Remove from code and Settings:
  - `retry.retries`, `retry.backoff_seconds`
  - `penalty_base_seconds`, `penalty_max_seconds`
  - `quarantine_seconds`
  - the decider confidence knobs
- Add: "Give up after" (30s), "Save conversations" (on, 7 days; wired in Session 11), "Add new models automatically", "Show quickstart".
- Retired names still in `config.yaml` / `overrides.json` → ignored, with **one** notice in `flexrouter doctor` and on Settings ("…3 settings v2.3 no longer uses… you can delete them"). `config.yaml` is never rewritten (ADR 0002).

**Starter prompt:**
> Do Session 10 of PLAN-V2.3.md. Read that block and grill-decisions.md §15.

**End of phase 2:**
- [ ] Full suite + browser suite green.
- [ ] Rerun the say-hi sweep.

---

## Phase 3 · See everything

### ☐ Session 11 — Saved conversations + the request sheet
**Model:** Sonnet
**Decisions:** §7. **US:** 48–53. **P:** 9, 13, 14.
- A conversation store: prompt, reply and reasoning per request ID, in `state/`. Long messages cut at ~20 KB, auto-deleted after 7 days, with the Settings off switch. Keys flexrouter holds are masked by exact match. Separate from the scrubbed trace.
- The request sheet (built from showcase parts):
  - header: id, bucket → model, time, result
  - ▸ Thinking
  - You
  - Reply
  - ▸ Tried first, listing each failed attempt **and the time spent waiting**
- `/requests/<id>` works as a real page.

**Starter prompt:**
> Do Session 11 of PLAN-V2.3.md. Read that block, grill-decisions.md §7, and the Q8 sample in grill-log.md.

### ☐ Session 12 — Playground + Explain errors with AI (after 4, 8)
**Model:** Sonnet
**Decisions:** §7, §8. **US:** 43–47, 54. **P:** 10, 23.
- Playground:
  - a collapsible Thinking section
  - the reply labelled with the model that answered, not "FLEXROUTER"
  - rendered markdown
- **"Explain errors with AI"**: a copy-prompt button on the status page (all problems), plus a small per-row version. The prompt contents (see the approved example in `grill-log.md`):
  - a preamble
  - per problem: status, how often and when, the **full unmangled provider response**, the model's settings, its last few requests, and similar real IDs
  - the list of clickable buttons

  Keys masked, no paste-back.

**Starter prompt:**
> Do Session 12 of PLAN-V2.3.md. Read that block, grill-decisions.md §8, and the example prompt in grill-log.md.

### ☐ Session 13 — AI paste hardening + parked models (after 5)
**Model:** Sonnet
**Decisions:** §6, §18. **US:** 57–62.
- The "Add models with AI" prompt (`dashboard/add_models.py`) includes the provider's **real ID list**: "match each row to one of these or leave it out".
- Review screen: every row is validated. A row not in the list gets red "not a real ID, did you mean X? [use]", and it can't be saved until fixed.
- Rate limits:
  - null → "unknown, learning" (no limit enforced; learned from headers and 429s)
  - explicit 0 → "Not on your plan", added switched off
  - **delete the invented 60 RPM / 60K TPM default** (in `dashboard/pages.py`, the AI-paste save path)
- Parked (non-chat) models: keep them, in a collapsed "Not chat models yet (N)" list at the bottom of Models, reading "Saved for later. flexrouter only routes chat models today." Validate their IDs too.

**Starter prompt:**
> Do Session 13 of PLAN-V2.3.md. Read that block and grill-decisions.md §6 and §18.

### ☐ Session 14 — Provider facts + honest Allowance ⚡
**Model:** Sonnet
**Decisions:** §19. **US:** 71–77, 98. **P:** 5, 6, 25.
- `flexrouter/data/presets.json` gains:
  - `key_url`, `rate_limit_page_url`, `docs_url`
  - `daily_reset` (Google: `00:00 America/Los_Angeles`)
  - `counts_failed_requests` (Google: true, observed 2026-09-25)
  - `limit_scope`
  - `known_quirks`
  - free-tier notes
  - a `checked` date per preset

  **Remove `seed_rpm` / `seed_tpm`.** Update `presets.py` and its tests.
- Usage counting (`quota.py`, `_router.py` record sites): count **every attempt that reached the provider**, failures included, on windows aligned to the provider's reset. Show provider-reported remaining figures as the truth.
- Allowance page:
  - grouped per provider, with no inflated grand total
  - one row per real limit
  - "resets in 5h (midnight PT)" in local time
  - the note "counts only what flexrouter sent"

**Starter prompt:**
> Do Session 14 of PLAN-V2.3.md. Read that block and grill-decisions.md §19.

**End of phase 3:**
- [ ] Full suite + browser suite green.

---

## Phase 4 · Polish, first run, docs

### ☐ Session 15 — Port the showcase everywhere (after 1)
**Model:** Sonnet
**US:** 86–87. **P:** 20 (button parts), 36.
- Replace every remaining button and form control on every page with the showcase kinds, following `showcase/PORTING.md`:
  - working → done/failed feedback everywhere
  - undo on dangerous actions
  - Danger zone moved off Providers
- Keep `tests/test_dashboard_css.py` green: add rules, don't loosen it.

**Starter prompt:**
> Do Session 15 of PLAN-V2.3.md. Read that block and .scratch/polish/showcase/PORTING.md.

### ☐ Session 16 — No jumping, no swapping, every width (after 15)
**Model:** Sonnet
**US:** 88–90. **P:** 26–30.
- Overview layout shift under 0.1 (it's 0.32 today).
- Live refresh never replaces a row under the cursor or mid-click.
- No horizontal overflow at 1014px (Overview, Providers, Models, Allowance).
- 375px is usable (a sidebar that collapses).
- Readable chart axis labels, and traffic not squeezed into one bar.

**Done when:**
- [ ] Measured in the browser pane (layout-shift observer).
- [ ] Browser tests added for the no-swap rule.

**Starter prompt:**
> Do Session 16 of PLAN-V2.3.md. Read that block and papercuts.md items 26–30. Measure before and after in the browser pane.

### ☐ Session 17 — Words and numbers ⚡ 🪶
**Model:** Haiku or Sonnet
**US:** 91–95, 100. **P:** 15, 16, 20, 21, 22, 24.
- Timestamps in local time, one format (no more `+00:00Z`).
- Pinned models not counted as buckets on Requests and Overview.
- Remove jargon: "resting", "parked" keys, "outranked", "overturned the rule", "Sideline a failing provider".
- Plurals: "1 failovers", "1 models", "1 reqs".
- One name pattern for the three "…with AI" features.
- Readable "Can do" chips.
- The Logs page says "Logging is off. Start with `--log`" when it is (§10).

**Starter prompt:**
> Do Session 17 of PLAN-V2.3.md. Read that block and the listed papercuts.md items.

### ☐ Session 18 — Quickstart checklist + Test all 👤 (after 1, 4, 13, 14)
**Model:** Opus (design, you review)
**Decisions:** §12, §13. **US:** 70, 82–85.
- A "Get started · N of 5 done" card on top of Overview, built from the showcase:
  1. add a provider: every preset listed free/paid, with "Get a key ↗" and free-tier notes from the presets
  2. paste its key: ticks when the key test passes
  3. Add models with AI
  4. [Test all]
  5. point your app at `localhost:4891/v1`, with [Copy Python] / [Copy curl]
- Steps tick themselves; no Next buttons.
- Shown on first start and whenever no model works. [Hide], plus "Show quickstart" in Settings.
- **[Test all]**: says hi to every model once (~512 tokens), with per-model progress and results. It also lives on the status page. Only runs on click, no background pings.

**Starter prompt:**
> Do Session 18 of PLAN-V2.3.md. Read that block, the Q18 mockup in grill-log.md, and showcase/PORTING.md. Show me in the browser pane before finishing.

### ☐ Session 19 — Docs, ADRs, release 🪶 (last)
**Model:** Sonnet for ADRs and glossary; Haiku is fine for README/CHANGELOG prose
**Decisions:** §11, §16. **US:** 101–105.
- **README** Quickstart + `docs/1-Getting-Started.md`: the dashboard path (add key → Add models with AI → Test all → point your app). YAML moves to `docs/2-Configuration-Guide.md` as the by-hand option.
- Document:
  - the error format and request IDs
  - the statuses
  - pinned vs bucket failover
  - saved conversations and their **privacy trade-off**
  - `--log`
  - "Google counts failed attempts"
  - the model list vs auto-add
- **ADRs 0018–0023** (list and what each supersedes/amends in grill-decisions §16).
- Update the **CONTEXT.md** glossary: statuses replace Quarantine / Cooldown / penalty; the Error brain now decides; Catalogue refresh; Preset fields; the Dashboard page list.
- **CHANGELOG** `[2.3.0]`, and bump `pyproject.toml` to 2.3.0.
- Move this plan to `docs/archive/`.

**Starter prompt:**
> Do Session 19 of PLAN-V2.3.md. Read that block and grill-decisions.md §11 and §16. Use a cheap model for prose where possible.

**End of phase 4:**
- [ ] Full suite + browser suite green.
- [ ] Final say-hi sweep.
- [ ] Light mode checked.
- [ ] Every write button checked for visible feedback (the two items the papercut sweep never covered).

---

## At a glance

```
Phase 0   1 showcase 👤
Phase 1   2 → 3 → 4   (in order)          5 ⚡
Phase 2   6 → 7 → 8 👤 → 10               9 ⚡
Phase 3   11 · 12 · 13                    14 ⚡
Phase 4   15 → 16 · 18 👤 · 19 (last)     17 ⚡ (any time)
```
19 sessions, 3 of which need the owner.
