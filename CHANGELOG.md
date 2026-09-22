# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
