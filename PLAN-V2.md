# flexrouter V2 — the plan

Written 18 Sep 2026. Supersedes the direction in `PLAN.md`, which was about
patching V1. This is the rebuild.

---

## What V2 is, in one paragraph

One background service running on this machine. It pretends to be OpenAI, so any
app just points at it instead of at a provider and needs no flexrouter code at
all. It holds every key, every setting and every scrap of history in one place —
no app carries its own copy of anything. The Python library survives as a thin
convenience wrapper that talks to the service, and says so plainly if the
service isn't running.

---

## What was actually wrong with V1

1. **Every app had its own settings and its own keys.** Change something and you
   change it five times.
2. **It couldn't tell you what was broken.** Provider error text was thrown away
   at the point of failure, so every debugging session started from nothing.
3. **A third of the model list was dead** and nothing noticed. 30 of 96.
4. **One bad key took out a whole provider**, including the good keys next to it.
5. **Scores were hand-typed guesses** that went stale and were never revisited.

Everything below exists to fix one of those five.

---

## Decisions already made — do not reopen

| Question | Answer |
|---|---|
| How much of V1 survives | Keep the good parts, rebuild around them |
| Who it's for | Him first, tidy enough to share |
| Shape | Service first, library second |
| Wire format | OpenAI-shaped, so any app can point at it |
| Asking for a model | Named buckets ("smart", "fast"), not descriptions |
| If the service is down | Fail loudly with the exact start command. **Never auto-start.** |
| Config file | Stays his. Nothing rewrites it without asking first. |
| Model list | Refreshed automatically, but changes are proposed, not applied |

---

## Build order

Each stage should be usable on its own. Don't start the next until the previous
one is actually working.

### 1 — One home for everything

- A single fixed location for settings. No searching the current directory.
  On Windows: `%LOCALAPPDATA%\flexrouter\`.
- Keys live in their own file next to it, locked down to the current user, and
  **never inside the settings file**. V1 has live keys sitting in
  `flexrouter.yaml` — that is the single worst thing in the repo right now.
- Order of lookup for a key: typed into the dashboard → keys file → environment
  variable. First hit wins.
- The settings file is read, not rewritten. Anything the dashboard changes goes
  into its own file. Comments in the hand-written config survive forever.

### 2 — The service speaks OpenAI

- `POST /v1/chat/completions`, `GET /v1/models`, streaming included.
- The `model` field in the request names either a bucket ("smart") or a specific
  model. Buckets are the normal path.
- Forward streaming chunks through untouched where the shapes already match.
  Re-parsing them is where LiteLLM keeps breaking tool calls — don't repeat it.
- When a stream fails partway through, send a proper error chunk and close.
  **Do not switch models mid-answer.** Every project that tries it regrets it.
- The library becomes a thin client. If the service isn't running it raises one
  clear error containing the exact command to start it.

### 3 — Remember everything about every request

This is the foundation. Nothing later works without it.

One record per request, appended as a single line to a file:

- what was asked for (bucket, whether it needed vision or tools, rough size)
- every model that was **considered and skipped, and why** — the engine already
  computes this, V1 just dropped it on the floor
- every model that was **tried**, in order, and for each: which key, the status,
  the provider's **exact error text**, how long it took
- what finally answered, tokens in and out, cost, time to first token
- the request id, so a row in the dashboard can be clicked open

The single biggest complaint from every competitor review was the same: the
routing logic is fine, the blindness is the problem. Fix the blindness first.

### 4 — The small-decisions layer

One small, fast, structured-output model doing the boring classification jobs
that would otherwise be hand-maintained forever. Two jobs, same machinery.

First classifier: the plan named `typesafe/jev-1.13`. **That model could not be
shown to exist** -- it appears nowhere in Artificial Analysis' catalogue of 656
models across 59 creators, and no session ever produced an endpoint, an auth
shape or a price for it. So no vendor is hard-wired at all: `HttpDecider` talks
to any OpenAI-compatible endpoint held to a JSON schema, and `decider_base_url`
/ `decider_model` are settings. Choosing a vendor is configuration, not code,
which is what "keep it behind a small interface so it can be swapped" asked for
in the first place.
Everything it decides is written to disk, is correctable by hand, and a manual
correction is never overwritten.

#### 4a — Understanding errors

Providers word their failures however they like. Rather than hand-writing a
pattern for each one, teach it once and remember.

- A table on disk keyed by a normalised fingerprint of the error text (lowercase,
  numbers and ids stripped, whitespace collapsed).
- Obvious cases are matched by built-in rules first and never reach the
  classifier — a plain 401 or 429 is already unambiguous.
- Anything unrecognised is sent **once** to a small classifier model, which
  returns one of a fixed set of verdicts:
  `too_fast` · `bad_key` · `needs_payment` · `model_gone` · `their_end_temporary`
  · `message_too_long` · `bad_request` · `unknown`
- The verdict, the confidence, the date and what produced it are stored. Every
  later occurrence is free.
- **Below a confidence threshold, flag it for review rather than act on it.**
- Manual corrections always win and are never overwritten by a later
  classification.

Cost is negligible: it fires only on error text never seen before. Expect a
handful of calls in the first week and near zero after that.

#### 4b — Working out what a model can do

> **Status 2026-09-21 — specified, deliberately not built.** `4a` ships with a
> real classifier; `Decider.describe_model` still returns `{}`. Two reasons,
> neither of them "ran out of time": nothing calls it (there is no caller that
> hands it a newly-discovered model), and its return shape -- six attributes
> each carrying its own value/source/confidence -- belongs to Stage 6, which
> has not defined it. Building it now would mean inventing that shape twice.
> The interface is already declared, so adding it later is an implementation,
> not a migration. V2 is complete with this boundary drawn, not despite it.

Same idea, pointed at the model list instead of at errors. When the daily
refresh finds a model that isn't known yet, one call works out what it is:

- can it see images
- can it use tools
- is it a reasoning model
- how big is its context window
- rough size class — small / medium / large / frontier
- is this the free variant of something
- a provisional quality score, so a new model isn't stuck unusable at "unranked"

**Read before guessing.** Some of this is published — most providers state the
context window, and several state capabilities, in their own model list. Take
the published value every time. The classifier is only for the gaps, which in
practice means inferring from the model's name and family.

**Then let reality correct it — slowly.** Every attribute carries where it came
from:

| Source | Meaning |
|---|---|
| `published` | the provider said so |
| `guessed` | worked out from the name — provisional |
| `observed` | earned by what actually happened |
| `manual` | he set it, and nothing may overwrite it |

This is where the two halves connect. But a single failure is not proof — a
malformed tool call on our side, a provider having a bad five minutes, or a
rate limit wearing the wrong clothes all look the same from here. So the
correction is deliberately reluctant:

- A capability is **yes**, **doubted**, or **no**, not just yes or no.
- One contradicting failure moves it to **doubted**, never straight to no.
  Doubted still gets used, just last.
- It takes **two more** contradicting failures, on separate requests, before it
  becomes **no**.
- **A success wipes the slate.** One call that works resets the count to zero
  and puts it back to yes. Success is stronger evidence than failure, because
  something that works cannot be something that doesn't.
- A failure only counts as evidence if the error brain is **confident** it means
  this model can't do that thing. Anything ambiguous is ignored for this
  purpose.
- Overriding a **published** value takes more than overriding a guess — and
  when it happens, say so. A provider's own documentation being wrong is worth
  knowing about.
- A **manual** value is never overridden. If reality disagrees with him,
  it says so and leaves it alone.
- **No is not forever.** An observed `no` goes stale after about a month and
  drops back to doubted so it gets another chance. Providers quietly upgrade
  models; something that couldn't handle tools in June may well handle them in
  September.

When a request *needs* a capability, prefer a model where it's `published` or
`observed`, then `guessed`, then `doubted` as a last resort. Never a `no`.

Cost: one call per newly discovered model. A few dozen on first setup, then
roughly nothing.

### 5 — Per-key handling

- State is tracked per key, not per provider. A dead key gets benched; the other
  keys at the same provider keep working.
- A key that just got rate-limited is skipped until the provider's own stated
  reset time, not put straight back into the queue the way V1 does.
- Choosing between healthy keys: default to whichever has the most allowance
  left. Also offer take-turns, fastest, and manual weights.
- Per-key model allow-lists, so a free-tier account can be limited to free
  models.
- This state survives a restart. A key that was dead before the restart is still
  dead after it.

### 6 — Keep the model list honest, cheaply

- Once a day, **one** call per provider asking for its own list of models.
- Compare against what's configured. Anything gone, anything new.
- Results go into a pending tray. Nothing is added or removed until accepted.
- **No pinging of individual models.** modelrelay does this and it costs
  thousands of calls a day out of the same free allowance the router exists to
  protect. The daily list refresh catches the same problem for one call.
- Everything else is learned from real traffic, which is free.

### 7 — The screen

Built to the mock-up. The provider/key/model view is the centre of gravity:

- One row per provider: status, address, keys, models alive vs gone, allowance
  left, when it was last checked.
- Expand for per-key detail — masked value with show/hide, usage against its cap,
  whether it's in rotation or cooling down and why, allowed models, failures.
- Expand for per-model detail — score and where the score came from, what it can
  do **and how that was established** (published / guessed / observed / set by
  hand), state, recent real-traffic history, typical speed, last problem.
- A "what it's learned" view covering both halves of the small-decisions layer:
  the errors it has filed, and the model details it worked out. Anything it is
  unsure about is surfaced for confirmation rather than acted on.
- A "needs you" strip at the top listing only the things he can fix and the
  service can't, each with the provider's own words quoted.

### 8 — Ranking models with an AI

- A button that builds a ready-made set of instructions from the current model
  list plus whatever benchmark material he supplies (pasted in, or just a note
  saying where to look).
- Two ways to run it: the service sends it itself, or he copies it into whatever
  chat he's already in and pastes the answer back.
- Either way the result is shown as current-vs-proposed with tick boxes, and
  nothing is applied until he says so.
- Every applied score keeps a note of where the number came from and when.
- Scores he has pinned by hand are left alone.
- This is the considered, benchmark-backed pass. It replaces the provisional
  score stage 4b hands a brand-new model, which exists only so a new model isn't
  stuck unusable until he gets round to ranking it.

---

## Deliberately not doing

- **Pinging every model on a schedule.** Burns the free allowance this tool
  exists to stretch.
- **Publishing a self-updating model catalogue.** FreeLLMAPI can do this because
  it has contributors. This project has one person who touches it when he needs
  it. A feed nobody maintains is worse than no feed.
- **Switching model mid-answer.** Fragile, wastes tokens, mangles tool calls.
- **Caching "similar" questions.** Needs extra machinery and returns confidently
  wrong answers.
- **Teams, permissions, multi-user anything.**
- **A nested configuration language.** Flat and simple is a feature.
- **Starting the service automatically.** Explicitly rejected, twice.

---

## Carried over from V1 untouched

The failure handling. Deleted models quarantined rather than retried forever,
auth failures separated from billing failures separated from rate limits,
exponential backoff, strict separation between buckets. Three independent
reviews of the commercial products all landed on the same conclusion: this part
is already better than LiteLLM's and Portkey's. Don't touch it.

---

## Still open

- ~~What the buckets should be called and what goes in each.~~ **Closed
  2026-09-21: ratify what is already in use.** The running config answers this
  on its own -- seven buckets, 96 models: `default` (55), `quick` (12),
  `chat` (9), `deep` (6), `utility` (5), `frontier` (5), `judgment` (4).
  These names were arrived at in use rather than designed up front, which is
  the better provenance. Inventing a fresh vocabulary now would rename
  something that already works and invalidate every score attached to it.

- ~~Whether the old 13.5KB config is imported once or rebuilt from scratch in
  the new screen.~~ **Closed 2026-09-21: import once.** The file is 12,950
  bytes describing 5 providers and 96 models across 7 buckets. Rebuilding that
  through the dashboard means re-entering 96 models by hand to arrive at what
  already exists, and every hand-pinned score is lost on the way. The dashboard
  writes through `overrides.json` and never touches the hand-written config, so
  importing costs nothing it would otherwise protect.
- ~~Whether `flexrouter/server.py`, `tests/test_server.py`,
  `flexrouter/dashboard/server.py` and `tests/test_dashboard_server.py` get
  deleted.~~ **Closed 2026-09-21: delete — and already done.** Daniel answered
  "delete them"; the files had in fact been removed back in `28ccb65`, and
  `tests/test_public_surface.py::test_the_old_server_modules_are_gone` guards
  them staying gone. This question was stale, not open. Nothing to do.
