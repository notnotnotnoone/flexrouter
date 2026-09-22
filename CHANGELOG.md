# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

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
