# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [2.3.0] - 2026-09-27

The honesty and polish release: errors say what really happened, failover
stops sleeping, every model has one status, and the dashboard gets a first-
run guide and a working button for everything.

### Added

- **Status page** (`/status`) replaces "What's broken" and "Error brain"
  (their old addresses redirect). Every model is Ready, Busy, Struggling,
  Needs you or Off. Needs-you rows say one sentence and offer one fix
  ([Use X], [Retry], [Remove]); Busy rows count down; errors the classifier
  isn't sure about are asked once under "Not sure". Press a row for the
  provider's whole response and recent tries.
- **Get started** checklist on Overview: add a provider (every preset listed
  Free or Paid, with "Get a key ↗"), paste its key, Add models with AI,
  Test all, point your app at `localhost:4891/v1`. Steps tick themselves
  (ADR 0023).
- **Test all**: says "hi" to every model once, with room for 512 tokens, and
  shows what each answered or why it didn't. Only on a click.
- **Did you mean?** A 404 is checked against the provider's real model list;
  [Use X] files the right ID and turns the wrong one off (ADR 0021).
- **Saved conversations**: each request's prompt, reply and reasoning, kept
  7 days in `state/conversations/`, shown on the request sheet. On by
  default, with an off switch; see the privacy note in the docs (ADR 0022).
- **Request sheet** (`/requests/<id>`): the model that answered, Thinking,
  what you sent, the reply, and every model tried first with the time spent
  waiting.
- **Explain errors with AI**: copies a detailed prompt about every problem
  (or one row) to discuss with any chatbot.
- **Undo** instead of "are you sure?" on removing a key, disabling a model,
  putting changes back, "Reset everything I changed" and model resets.
- **Provider facts** in presets: key and rate-limit page links, each
  provider's daily reset time, whether failed attempts count, limit scope,
  free-tier notes, and the date they were checked.
- Playground renders markdown, folds the model's reasoning into "Thinking",
  and labels each reply with the model that answered.
- Light theme (follows your system), a phone-width layout with a Menu
  button, and a drag that works with a finger, a tap, or the keyboard.

### Changed

- **Errors are honest.** Every error carries the providers' own words,
  `error.flexrouter.request_id` (also the `x-flexrouter-request-id` header on
  every response) and `error.flexrouter.attempts[]`, one entry per model
  tried. Provider text is no longer mangled.
- **Failover never sleeps.** A bucket tries every model straight away and
  gives up after 30 seconds ("Give up after"). A pinned `provider/model`
  fails at once when busy, with no fallback.
- **One status per model and key** replaces quarantine, penalties, benching
  and cooldowns (ADR 0020). Overloads (500/503) are only ever Busy; nothing
  is benched for days. Old state files are migrated once.
- **The error brain decides** what an unfamiliar error means, and a bare 400
  goes to it instead of always blaming the caller (ADR 0019).
- **The real model list is always read** at startup; adding new models
  automatically stays optional and off (ADR 0021).
- **"Fastest" means measured time to the first word** over your last 20
  requests; unmeasured models are tried, not skipped (ADR 0024).
- **Allowance** is grouped per provider, resets on each provider's own
  clock (Google: midnight Pacific, shown in your time), and counts failed
  attempts — Google counts them too, so failing over at once saves real
  daily quota.
- Rate limits left empty mean "unknown, learning", not an invented 60/60K.
  `0` means "not on your plan" and adds the model switched off. Add models
  with AI rejects IDs that aren't on the provider's real list; non-chat
  models are kept in a folded "Not chat models yet" list.
- Every button shows working, then done or the reason it failed; a failed
  save stays on the page with what you typed.
- The reset-all Danger zone moved from Providers to Settings.
- Times are shown in your own time, in one format. Plainer words throughout
  ("a better one goes first", "named model"), correct plurals, and one name
  for the three "… with AI" tools.
- Docs: the Quickstart and Getting Started guide use the dashboard path; the
  settings file is the by-hand option.

### Removed

- The retry, penalty, quarantine and decider-confidence settings
  (`retries`, `backoff_seconds`, `retry_policy`, `penalty_*`,
  `quarantine_seconds`, `decider_confidence_*`, …). Left in a settings file
  they are ignored, and `flexrouter doctor` and Settings say so once.
- Seed rate limits in presets.

### Fixed

- Timestamps printed as `…+00:00Z` in UTC.
- Test calls used `max_tokens=1`, so a reasoning model always looked broken;
  an empty reply from a caller's own tiny budget no longer counts against
  the model.
- Inline `<think>` / `<thought>` reasoning is split out of the reply.
- Pages wider than the window at 1014px and unusable at phone width; a live
  refresh replacing the row under your cursor.
- Pinned models listed as buckets on Requests and Overview.
- The Logs page without `--log` now says logging is off and how to turn it on.

## [2.2.0] - 2026-09-23

### Added

- **Add models with AI** (`/models/add-with-ai`). A copy-paste flow: the
  dashboard builds a prompt, you run it in any AI, paste the answer back,
  review every field and apply. No network calls. Records `kind`; embedding,
  speech and image models are saved to `state/parked_models.json` and never
  routed.
- **JEV via OpenRouter's decisions API.** `typesafe/*` decider models use
  `/api/alpha/decisions` and report a verdict with probabilities and cost.
- **Error brain shows everything:** HTTP status, the provider's full response
  body, where each error happened with links to requests, every classifier
  probability, and a classifier health panel.
- Groq and Mistral rate-limit header parsers.

### Changed

- **Automatic model discovery is opt-in** (`experimental_model_discovery`,
  default off): no startup refresh, no discovery buttons or Pending tray, no
  model listing when a key is added, `flexrouter refresh` is a no-op.
- **402 and 403 refuse one model, not the whole key**; only 401 benches a key
  (ADR 0016).
- Discovery follows each provider's docs (llm7 free tier, Mistral chat,
  Groq active text, Google free) and raises instead of returning nothing.
- Artificial Analysis scores match on exact name tokens.
- Error text keeps known provider and model names and full bodies; your own
  keys are still masked exactly (ADR 0017).
- Dashboard: themed native controls, new logo, tidier Models page.

### Fixed

- Pinging many models failed almost every time: a key at its concurrency cap
  was treated as a failure. Busy keys now wait without penalty.
- Cancelled requests now leave a failed trace.
- Meter bars rendered empty on every page.
- Test suite passes on Linux CI.

## [2.1.0] - 2026-09-22

### Added

- **Redesigned dashboard.** A new shell and design system, rebuilt pages
  (Overview, Requests, Allowance, What's broken, Error brain, Settings,
  Buckets, Models), a Playground, a Ctrl+K command bar with keyboard
  shortcuts, and live updates.
- **Provider presets.** The provider catalogue ships as data; add a provider
  from a preset and import the models its key can reach. Your own presets
  layer over the shipped set.
- **Key management from the dashboard.** Add, remove, disable and edit keys
  (label, weight, globs, enabled) from a provider's page.
- **Terminal UI** (`flexrouter tui`) with overview, keys, requests and doctor
  tabs.
- **Request ids and pricing.** Every attempt carries a request id, failovers
  are counted from it, and models are priced with selectable time ranges.

- **Error classifier (spec §4a).** `HttpDecider` sends an error text the
  built-in rules cannot name to any OpenAI-compatible endpoint, held to a JSON
  schema, and caches the verdict by fingerprint so each novel error costs one
  call ever. No vendor is hard-wired: `decider_base_url`, `decider_model` and a
  `decider` service key are all configuration.
- **Rules are now a prior, not a verdict, for `400`/`403`/`429`.** Providers
  overload these -- a `429` means "out of credits" as often as "slow down" --
  and a rule answering at full confidence meant the router backed off and
  retried against an account that needed topping up. A configured classifier
  may now overturn them. Unconfigured, behaviour is byte-identical to before.
- **Eight behaviour knobs became settings**, defaults unchanged:
  `decider_confidence_threshold`, `decider_rule_prior_confidence`,
  `decider_confidence_ceiling`, `decider_contested_statuses`,
  `quarantine_seconds`, `probe_timeout_seconds`, `error_max_length`,
  `unscored_fallback_score`. The first was previously read in nine places to
  decide whether to act on a verdict, while nothing could set it.

### Fixed

- **`python-multipart` was never declared as a dependency**, but the dashboard
  reads HTML form posts in 11 places. On a clean install every dashboard write
  -- add provider, add key, save settings, apply rankings -- returned a 500.
- **Artificial Analysis scoring was silently dead.** The unversioned
  `/data/llms/models` endpoint now 404s for everyone; scoring moved to
  `/api/v2/data/llms/models`.
- **A null score from AA discarded every other score.** Nine of 656 entries
  carry an explicit `null` intelligence index. `.get(key, 50)` does not defend
  against that -- the default only applies when the key is absent -- so
  `int(None)` raised and took all 647 already-built scores with it.
- **AA failures no longer fail silently.** The fallback to a flat score is
  deliberate, so a flaky vendor cannot break routing, but it now logs.
- **Unmatched models no longer outrank almost everything.** AA rescaled their
  index; it now runs 3..53 with a median of 11, so the old hardcoded 50 put any
  model flexrouter could not match above 98.9% of the catalogue -- ahead of
  GPT-4o at 9. The fallback is now the median of the live set.
- **Concurrent error classification could crash.** With classification moved
  off the event loop, two requests can record a novel error at once; on Windows
  the competing atomic saves failed with `PermissionError`.

### Changed

- Error classification runs via `asyncio.to_thread`, so a slow or unreachable
  classifier cannot stall the event loop for every in-flight request.
- The test suite runs in under a minute instead of about seven and a half:
  the router's own waits advance a virtual clock under test, the startup
  catalogue refresh no longer calls real providers, and `pytest -n auto` is
  supported.

## [0.1.0]

Initial public release.

- OpenAI-compatible server (`flexrouter serve` / `flexrouter dashboard`), so any
  OpenAI-speaking client can point at flexrouter with no code changes.
- Bucket-based routing across providers, with score-weighted selection,
  rate-limit and spend-cap awareness, and automatic failover.
- One shared settings file per machine (`config.yaml`), with keys stored
  separately and never written back by flexrouter.
- Live dashboard: telemetry, chat, request logs, account status, settings,
  and setup guide.
- Python client (`FlexRouter`) for calling the router in-process without the
  HTTP layer.
- `flexrouter refresh` to discover available models and real rate limits per
  provider.
