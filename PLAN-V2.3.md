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

### ☑ Session 4 — Test budget + reasoning split 🔗 (after 3)
**Model:** Sonnet
**Decisions:** §13, §7. **US:** 13–14, 69. **P:** 34.
- Every test call (key Test in `dashboard/keytest.py`, the rate-limit probe in `dashboard/pages.py` `probe_one`) sends "hi" with **~512 max tokens**, not 1.
- An empty reply with `finish_reason=length` and a small caller `max_tokens` = a **caller budget problem**: no penalty, and the error says "the model used all N tokens thinking, raise max_tokens". It still fails over in buckets. The existing detection is around the empty-response branch in `_router.py`.
- **Reasoning splitter:** move `<thought>…</thought>` / `<think>…</think>` out of `content` into `reasoning_content`. It must be streaming-safe, since tags can split across chunks.

**Done when:**
- [x] The Gemma-style inline thought is split in both stream and non-stream.
- [x] The splitter's chunk-boundary tests pass.
- [x] A `max_tokens:1` probe no longer counts as a model failure.

**Starter prompt:**
> Do Session 4 of PLAN-V2.3.md. Read that block and grill-decisions.md §7 and §13.

### ☑ Session 5 — Always read the real model list ⚡
**Model:** Sonnet
**Decisions:** §12, §6 (validation). **US:** 63–65, 81.
- Split discovery:
  - **Reading** each provider's `GET /models` is always on: at startup, and when "Add models with AI" opens. Read-only, cached.
  - **Auto-add** stays opt-in and off, renamed in Settings to "Add new models automatically". The old `experimental_model_discovery` is still read as an alias.
- A **model-list checker** with `is_real(provider, id)` and `did_you_mean(provider, id) → [ids]` (close matches, e.g. `gemini-3-flash` → `gemini-3-flash-preview`).
- At startup, list configured IDs not in the real list, for Session 8 to show.
- Files: `catalogue.py`, `refresh.py`, `config.py`, the `_router.py` startup refresh, and the gates added in commit `3e0abbc`.

**Done when:**
- [x] With discovery off, startup still reads the lists and adds nothing.
- [x] The did-you-mean test uses the real Google IDs in `evidence/google-live-model-ids-2026-09-25.txt`.

**Starter prompt:**
> Do Session 5 of PLAN-V2.3.md. Read that block and grill-decisions.md §6 and §12.

**End of phase 1:**
- [ ] Full suite + browser suite green.
- [ ] Rerun `.scratch/polish/evidence/say_hi.py` against the running service and save the results next to the old ones. Success = every failure states its real reason and nothing waits pointlessly. It is not a pass-rate target.

---

## Phase 2 · One status

### ☑ Session 6 — One status per model and key 🔗
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
- [x] Each status transition has a test.
- [x] `/v1/models` and the facts layer report the new status.
- [x] Old state files are migrated or ignored cleanly.

**Done 26 Sep 2026.** Choices the plan left open:
- The store is `flexrouter/status.py` (`state/status.json`). `recovery.py` (penalty box + quarantine) and `bench.py` (auto-bench) are deleted. Keys keep their own status in `key_state.py`, now in the same words (`ready | busy | needs_you | off`); old words are translated on load.
- Migration: a quarantine becomes Needs you (by its status code); an "auto-benched" one is dropped (it was mostly Google's overload, only ever Busy now); a penalty becomes Busy capped at 60s. The old files are renamed `*.migrated-v2.3`, so it runs once.
- Busy reads `Retry-After`, `x-ratelimit-reset-*` and Google's `retryDelay` / "retry in Ns" (`status.parse_retry_after`). A 429 whose body says `limit: 0` is Needs you, "Not on your plan", on the model only (the key stays Ready).
- Struggling (~1h, [Try now]) is set on an empty reply. A caller-budget empty reply (§13) changes no status; the request skips that model for the rest of the call instead of picking it again.
- "Off" can't live in the store (a disabled model is dropped from the live config), so the facts layer counts it from overrides (`facts.status_counts`).
- `/api/quarantine` is replaced by `GET /api/statuses` and `DELETE /api/statuses/{provider}/{model}` ([Try now] / [Retry]); `/api/status` was already the service status. `/v1/models` entries carry `flexrouter.status` `{value, reason, until, action, kind, detail, …}`.
- `facts.broken()` now puts a model that needs you in the Needs-you pile (it used to be "handling it"). The pages only got the renames they needed; Session 8 rebuilds them.
- `penalty_*` / `quarantine_seconds` / `retry.*` are still parsed but do nothing; Session 10 removes them.

**Starter prompt:**
> Do Session 6 of PLAN-V2.3.md. Read that block, grill-decisions.md §3, and the mapping table at the bottom of grill-log.md.

### ☑ Session 7 — Did-you-mean + the error brain decides 🔗 (after 6)
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
- [x] The four real 400s in §4 each land on the right status, tested with a fake JEV.
- [x] A slow or down JEV never blocks for more than 0.5s.
- [x] [Use it] produces the right overrides.

**Done 26 Sep 2026.** Choices left open by the plan:
- Session 5's `catalogue.is_real`/`did_you_mean` and status.py's `"use:<id>"` action placeholder were already built - this session only had to wire them together: `classify_failure()`'s 404/410 branch and a `model_gone` verdict on *any* status both go through the same `_gone_failure()`, so a did-you-mean from an unrecognized 400 works exactly like a literal 404.
- `decide_failover(400, verdict=...)`: `None` or `"bad_request"` still returns to the caller (the no-classifier-configured default is unchanged); every other named verdict fails over. An unlisted 4xx (422, 413, ...) keeps the old flat "return" - out of scope.
- `_handle_provider_error()` gained `key_id`/`verdict` params; a `bad_key` verdict on *any* status now delegates to `_handle_auth_failure()` (the same key-scoped path a real 401/403 already used) instead of writing a model-level status.
- The 0.5s cap wraps the existing `asyncio.to_thread(...)` call in `asyncio.wait_for()` (`JEV_TIMEOUT_SECONDS`, `_router.py`); a timeout returns a synthetic `unknown`/0.0-confidence verdict for *this* attempt only - the real classify() call is not cancelled and still saves what it learns for the next sighting of that fingerprint.
- `fingerprint()` now takes `provider`/`model` and strips them (and `"provider/model"`) before normalizing, so the same shape of error across different models is one fingerprint.
- `[Use it]`: `POST /statuses/{provider}/{model}/use` (new; no request body - the suggested id comes only from that model's own live status, never the client) → `settings_write.use_suggested_model()`, which copies the old model's score/rpm/tpm/context_window/vision/quotas/prices/tokens_per_second onto the new id via `overrides.add_model()` in every bucket it was in, then disables the old one.
- "Not sure" (JEV unsure/down) has no new mechanism: `ErrorBrainEntry.flagged_for_review` already exists for this from Session 4-era work. Session 8 groups the status page by it.
- The three decider confidence knobs stop being read from `self._cfg.decider.*` in both `LocalRouter.__init__` and `reload()`; `ErrorBrain`'s own constructor defaults apply. The config/settings fields themselves are untouched here (Session 10's job).

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
- [x] Dashboard page tests and browser tests pass.
- [x] It matches the approved mockup.

*Built 2026-09-27 (after Phase 4's other sessions, which had worked around its absence):*
- **The page** is `dashboard/status_page.py` at `/status`. `/broken` and `/brain` answer 301 to it, and the menu has one "Status" entry. `broken_page.py` and `brain_page.py` are gone, and their tests were ported to `tests/test_dashboard_status.py`.
- **The rows** come from `facts.broken()`, so Explain with AI (Session 12) and Test all (Session 18) moved over unchanged. Ready rows come from every configured model, and Off rows from `enabled: false`.
- **Row buttons** go to `POST /status/<use|retry|remove|turn_on>/<provider>/<model>`. The JSON answer names the status the row settles into (`flex.settle`). [Remove] turns the model off, because config.yaml is never rewritten.
- **"Not sure"** rows are error-brain entries flagged for review. Opening one shows where it happened, the requests it hit, and the classifier's odds, with a one-time "It means… [Tell it]" form (`/brain/<fp>/verdict`, which now redirects to Status).
- **Classifier telemetry** is one footer line.
- **Same statuses everywhere:**
  - Overview's verdict tags are the same counts as Status: N ready · busy · struggling · N need you.
  - Providers and Models show `ui.pill()` statuses. `ui.status()`'s "● OK / ▲ BROKEN" is no longer used on those pages.

**Starter prompt:**
> Do Session 8 of PLAN-V2.3.md. Read that block, the approved mockup in grill-log.md, and .scratch/polish/showcase/PORTING.md. Show me in the browser pane before finishing.

### ☑ Session 9 — Fastest = time to first word ⚡
**Model:** Sonnet
**Decisions:** §14. **US:** 66–68. **P:** 17, 18.
- A speed tracker: median time to first token over the last ~20 **successful** requests per model (non-stream: fall back to total time, or only measure streams; decide and note it).
- The typed `tokens_per_second` is only a starting guess.
- The Buckets page shows "ranked by: first word, your last 20 requests", with seconds per model and "not measured yet, will try".
- "How fast" shows sample counts.
- Files: `engine.py` (`_pick`, the fastest strategy), `buckets_page.py`.

**Done 26 Sep 2026.** Choices left open by the plan:
- New `flexrouter/speed.py` (`SpeedTracker`, in-memory like `SlidingWindow` — reset on restart, not persisted): a per-model deque capped at 20 samples, median on read.
- Fell back to total latency on the non-stream path (no separate first-token moment there); the stream path uses the existing `first_token_at` timing.
- `engine._rank_value("fastest")`: a model's own measured median wins once it has samples; the typed `tokens_per_second` only applies with zero samples; a model with neither ranks with the fastest known (unchanged "tried, not skipped" behavior), via a new `_fastest_unmeasured()` shared by `_score_candidates` and `explain_unavailable` so the dashboard can't disagree with what `select()` would do.
- `explain_unavailable()` now reports `fastest_rank`, `ttft_ms`, `ttft_samples` per model; `facts.BucketModelRow` and the ladder's threshold/sort use `fastest_rank` instead of the raw typed value.
- Buckets page: a "ranked by: first word, your last 20 requests" caption on fastest-strategy buckets; each row shows "Ns (N samples)", "~N tok/s (guess)", or "not measured yet, will try".

**Starter prompt:**
> Do Session 9 of PLAN-V2.3.md. Read that block and grill-decisions.md §14.

### ☑ Session 10 — Settings cleanup 🔗 (after 3, 6)
**Model:** Sonnet
**Decisions:** §15. **US:** 78–81. **P:** 35.
- Remove from code and Settings:
  - `retry.retries`, `retry.backoff_seconds`
  - `penalty_base_seconds`, `penalty_max_seconds`
  - `quarantine_seconds`
  - the decider confidence knobs
- Add: "Give up after" (30s), "Save conversations" (on, 7 days; wired in Session 11), "Add new models automatically", "Show quickstart".
- Retired names still in `config.yaml` / `overrides.json` → ignored, with **one** notice in `flexrouter doctor` and on Settings ("…3 settings v2.3 no longer uses… you can delete them"). `config.yaml` is never rewritten (ADR 0002).

**Done 26 Sep 2026.** Choices left open by the plan:
- `RetryConfig`/`RETRY_PRESETS`/`retry_policy` are gone entirely, not just unread - a fixed retry count was already dead weight since Session 3 removed the loop bound they used to set. `_router.py`'s `max_attempts` (an informational figure only, never a loop bound) is now the actual number of models in the tier instead of a leftover retry-preset number.
- `penalty_base_seconds`/`penalty_max_seconds`/`quarantine_seconds` and the four decider confidence knobs (`confidence_threshold`, `rule_prior_confidence`, `confidence_ceiling`, `contested_statuses`) are deleted from `FlexConfig`/`DeciderConfig` outright, not just hidden from Settings - `decider.py`'s `build_decider()` already read them with a safe `getattr(..., default)` fallback, so nothing broke.
- New `FlexConfig.failover_budget_seconds` (default 30.0) replaces the old module-level `FAILOVER_BUDGET_SECONDS` constant in `_router.py` - "Give up after" is a real setting now, not a hardcoded number.
- `save_conversations`/`save_conversations_days`/`show_quickstart` are new `FlexConfig` fields and Settings rows with no behaviour behind them yet - Session 11 and Session 18 wire them up. "Add new models automatically" already existed as `auto_add_models` (Session 5's rename of `experimental_model_discovery`) and needed no new work.
- `config.retired_settings_notice(*settings_dicts)`: one sentence naming every `RETIRED_SETTINGS` name found in any of config.yaml's `settings:` block or overrides.json's, or `None` if there's nothing to say. Wired into both `flexrouter doctor` (`tui/facts.doctor_report`) and the Settings page header (`settings_page._retired_notice`) - config.yaml is still never rewritten (ADR 0002), so an old file with a retired name just gets the one notice, forever, until the owner edits it themselves.

**Starter prompt:**
> Do Session 10 of PLAN-V2.3.md. Read that block and grill-decisions.md §15.

**End of phase 2:**
- [ ] Full suite + browser suite green.
- [ ] Rerun the say-hi sweep.

---

## Phase 3 · See everything

### ☑ Session 11 — Saved conversations + the request sheet
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

### ☑ Session 12 — Playground + Explain errors with AI (after 4, 8)
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
- *Done 2026-09-26, ahead of Session 8:* the buttons sit on What's broken (`broken_page.py`) for now; the prompt lives in `dashboard/explain.py`, so Session 8 just calls it from the status page. Busy rows are left out of the prompt (they clear by themselves). Markdown is a small escaped subset in `app.js` (`md()`), no new vendor file.

**Starter prompt:**
> Do Session 12 of PLAN-V2.3.md. Read that block, grill-decisions.md §8, and the example prompt in grill-log.md.

### ☑ Session 13 — AI paste hardening + parked models (after 5)
**Model:** Sonnet
**Decisions:** §6, §18. **US:** 57–62.
- The "Add models with AI" prompt (`dashboard/add_models.py`) includes the provider's **real ID list**: "match each row to one of these or leave it out".
- Review screen: every row is validated. A row not in the list gets red "not a real ID, did you mean X? [use]", and it can't be saved until fixed.
- Rate limits:
  - null → "unknown, learning" (no limit enforced; learned from headers and 429s)
  - explicit 0 → "Not on your plan", added switched off
  - **delete the invented 60 RPM / 60K TPM default** (in `dashboard/pages.py`, the AI-paste save path)
- Parked (non-chat) models: keep them, in a collapsed "Not chat models yet (N)" list at the bottom of Models, reading "Saved for later. flexrouter only routes chat models today." Validate their IDs too.
- *Done 2026-09-26:* the prompt already carried the real ID list (Session 5). `rpm`/`tpm` are now `Optional` end to end (`ModelConfig`, `SlidingWindow`, `validate_config`), so null means no local limit until one is learned. A not-real ID is blocked twice: `app.js` disables Apply while a checked row is wrong, and the apply handler skips it with the reason. "Not on your plan" is any explicit 0 among a row's limits.

**Starter prompt:**
> Do Session 13 of PLAN-V2.3.md. Read that block and grill-decisions.md §6 and §18.

### ☑ Session 14 — Provider facts + honest Allowance ⚡
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
- *Done 2026-09-26:* reset times live in `flexrouter/resets.py`, which falls back to the US daylight-saving rule because Windows has no tzdata. Only Google's facts are evidence-backed (`checked: 2026-09-25`). The other presets' URLs, notes and quirks were written from memory, so they have `checked: null` and `counts_failed_requests: null`, and the page says "provider facts not checked yet" until someone checks them. An attempt counts once the provider answered it (a status code, or an empty reply). The "failed attempts save real quota" docs note is left for Session 19.

**Starter prompt:**
> Do Session 14 of PLAN-V2.3.md. Read that block and grill-decisions.md §19.

**End of phase 3:**
- [x] Full suite + browser suite green (2026-09-26: 1632 passed, 1 skipped; browser 14 passed). Three tests that were already failing before this phase were fixed as stale: two assumed a one-model bucket retries after a 500/429 (not true since Session 3), and one expected Models at `/models`. The Add-with-AI browser test also still pasted the old pipe format.

---

## Phase 4 · Polish, first run, docs

### ☑ Session 15 — Port the showcase everywhere (after 1)
**Model:** Sonnet
**US:** 86–87. **P:** 20 (button parts), 36.
- Replace every remaining button and form control on every page with the showcase kinds, following `showcase/PORTING.md`:
  - working → done/failed feedback everywhere
  - undo on dangerous actions
  - Danger zone moved off Providers
- Keep `tests/test_dashboard_css.py` green: add rules, don't loosen it.
- *Done 2026-09-26:*
  - Every form button is `ui.submit()` (kind + `data-working`/`data-done`).
  - Forms stay htmx-boosted: `app.js` hooks `htmx:beforeRequest`/`beforeSwap`, so a write that failed (the redirect's `ok=0`) stays on the page and shows its reason beside the button, keeping what was typed. A write that worked swaps the page in as before.
  - Undo:
    - `dashboard/undo.py` keeps the exact bytes of the files a dangerous write is about to change, in memory for 60s.
    - The redirect carries `undo=<token>`, the success toast offers Undo, and `POST /undo/<token>` puts the files back.
    - It covers: key remove, service-key remove, disable/put back a model, a provider's "Put it all back", "Reset everything I changed" (its confirm dialog is gone) and the model resets.
    - The typed-phrase guard on model resets stays: it's a typed guard, not a dialog.
  - The reset-all Danger zone is on Settings. A provider's own reset is folded shut at the bottom of its page.
  - Settings' on/off rows are `.switch`es that save themselves (`data-post`).
  - Drag is pointer-based: drag, tap then tap, or Enter then Enter.
  - The light theme is ported (it follows the OS). `test_dark_only` became a test that both light blocks redefine every colour token.
  - The status list, Test all and quickstart JS is ported ahead of Sessions 8 and 18.

**Starter prompt:**
> Do Session 15 of PLAN-V2.3.md. Read that block and .scratch/polish/showcase/PORTING.md.

### ☑ Session 16 — No jumping, no swapping, every width (after 15)
**Model:** Sonnet
**US:** 88–90. **P:** 26–30.
- Overview layout shift under 0.1 (it's 0.32 today).
- Live refresh never replaces a row under the cursor or mid-click.
- No horizontal overflow at 1014px (Overview, Providers, Models, Allowance).
- 375px is usable (a sidebar that collapses).
- Readable chart axis labels, and traffic not squeezed into one bar.

**Done when:**
- [x] Measured in the browser pane (layout-shift observer).
- [x] Browser tests added for the no-swap rule.

*Done 2026-09-26*, measured on a sandbox home with fake traffic:
- **Before:** at 1014px every page was 1100px wide, because of `body { min-width: 1100px }`. At 375px six pages were 480–660px wide.
- **After:** no page is wider than the window at 1014px or 375px.
- **Layout shift:** 0.000 on every page. The 0.32 didn't reproduce with warm fonts; both fonts are now preloaded anyway.
- **Phones:** under 760px the sidebar is a top bar with a Menu button.
- **Live refresh:** a poll is skipped while the pointer rests on a row, while a field has focus, or for 400ms after a click. The next tick tries again.
- **Chart labels:** sized in screen pixels by `app.js` (`--tick`, 11px).
- **Chart start:** the chart begins at the first request, with at least 6 bars.
- **Browser tests:** no-swap, no overflow at both widths, the Menu, label size, layout shift, failed save, and Undo.

**Starter prompt:**
> Do Session 16 of PLAN-V2.3.md. Read that block and papercuts.md items 26–30. Measure before and after in the browser pane.

### ☑ Session 17 — Words and numbers ⚡ 🪶
**Model:** Haiku or Sonnet
**US:** 91–95, 100. **P:** 15, 16, 20, 21, 22, 24.
- Timestamps in local time, one format (no more `+00:00Z`).
- Pinned models not counted as buckets on Requests and Overview.
- Remove jargon: "resting", "parked" keys, "outranked", "overturned the rule", "Sideline a failing provider".
- Plurals: "1 failovers", "1 models", "1 reqs".
- One name pattern for the three "…with AI" features.
- Readable "Can do" chips.
- The Logs page says "Logging is off. Start with `--log`" when it is (§10).
- *Done 2026-09-27:*
  - **Times.** The `+00:00Z` came from `_router.py` appending "Z" to an offset timestamp. The reader then failed to parse it and printed the raw text. Both ends are fixed, and `overview.parse_utc` still reads old traces. `overview.clock()` is the one format: local 24-hour, with the day only when it isn't today. Allowance and a key's "Last used" use it too.
  - **Named models.** A pinned request is logged under `provider/model`. It now shows as "named model" on Requests and the request sheet. Overview's Buckets box sums these into one "Plus N requests that named one model" line.
  - **Words.**
    - "outranked" → "a better one goes first".
    - The Error brain "overturned the rule" → "disagreed with the quick check".
    - "resting", "parked" keys and "Sideline" were already gone.
  - **Plurals.** A new `ui.plural()`.
  - **Names.** The three features are "Add models with AI", "Rank models with AI" and "Find rate limits with AI", and all three are in Ctrl+K.
  - **Chips.** Two rule sets fought: the later one put white text on the green "published" fill. The duplicate is gone, and each chip names its source with a mark: ✓ seen, ~ guessed, ✎ yours.
  - **Logs off.** A full page, still a 404.

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
- *Built 2026-09-27; waiting for the owner's look in the browser before the box is ticked.*
  - **The card** (`dashboard/quickstart.py`) sits inside Overview's live block, so steps tick themselves on the next refresh. A refresh waits while Test all is running.
  - **What ticks each step:**
    - Step 1: any provider is configured. Until then it lists every preset as Free or Paid, with "Get a key ↗" and the preset's free-tier note on hover.
    - Step 2: a key Test passed (kept in `state/quickstart.json`), or any request has been answered.
    - Step 3: any bucket has a model.
    - Step 4: every model was asked and at least one answered. A run where none answered says so and stays open.
    - Step 5: a request arrived that wasn't the dashboard's own. Playground, Try it and the rate-limit probe are now tagged `client=playground` / `dashboard-test`.
  - **Test all:**
    - It is `POST /test-model/<provider>/<model>` (`keytest.test_model`, 512 tokens, direct, and it changes no model status). The last answer per model is remembered.
    - It is on the card and in a box on What's broken, because the Session 8 status page doesn't exist yet.
  - **Hide** posts `show_quickstart=false`, with Undo. The card comes back by itself when no model works.

**Starter prompt:**
> Do Session 18 of PLAN-V2.3.md. Read that block, the Q18 mockup in grill-log.md, and showcase/PORTING.md. Show me in the browser pane before finishing.

### ☑ Session 19 — Docs, ADRs, release 🪶 (last)
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
- [x] Full suite + browser suite green. On 2026-09-27: 1657 passed, 5 skipped; browser 27 passed. Browser runs on a loaded machine occasionally time out one test that passes alone. Two stale tests were fixed on the way. The did-you-mean tests read an untracked `.scratch` file, now a tracked fixture in `tests/fixtures/`. The discovery toggle test looked for the old checkbox.
- [ ] Final say-hi sweep. **Not run:** it sends real requests with your keys to the running service, which is still the old code until you restart it from this branch.
- [x] Light mode checked. A contrast scan of every page found the faintest grey at 2.6:1; the light `--ink-4` is now `#74747d`, and nothing is under 3:1.
- [x] Every write button checked for visible feedback. All 62 POST forms across 14 pages use a `.btn` that `app.js` drives through working → done/failed (a failed write stays on the page with its reason). Test all, the Status fixes, switches, copy, drag and Undo have their own feedback, and the browser tests cover a failed save, Undo, Test all, a Status fix and the copy button.

*Session 19 notes (2026-09-27):*
- ADR 0018 was already taken ("request options are headers"), so v2.3's six ADRs are **0019–0024**, not 0018–0023: error brain decides, one status, model list always read, saved conversations, quickstart checklist, fastest = first word. The ADRs they supersede or amend (0007, 0010, 0012, 0013, 0014, 0016) have updated Status lines.
- The version is bumped to 2.3.0 and the CHANGELOG has a `[2.3.0]` entry. It isn't tagged or pushed.
- The plan stays in the repo root, not `docs/archive/`, until Sessions 8 and 18 get the owner's look.

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
