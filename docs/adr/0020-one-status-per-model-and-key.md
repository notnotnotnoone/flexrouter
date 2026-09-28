# 0020. One status per model and key

Date: 2026-09-26
Status: Accepted. Amends ADR 0016.

## Context

Five mechanisms could take a model or key out of routing: quarantine (24h),
penalties (doubling back-offs), benching, cooling, and the auto-bench (up to
7 days, mostly for Google's 503 overloads). Each had its own file, timer and
wording. The dashboard showed "OK" on dead models and "5 of 5 fine" next to
"7 need you".

## Decision

Every model and every key has exactly one status, stored in
`state/status.json` (`flexrouter/status.py`) and `key_state.py`:

| Status | Meaning | Action |
|---|---|---|
| Ready | will be used | none |
| Busy | rate limit or overload; clears on its own, with a countdown | none |
| Struggling | fails in odd ways (empty replies); clears after about an hour | Try now |
| Needs you | wrong ID, not on plan, no balance, key rejected; no timer | the one fix |
| Off | you turned it off | Turn on |

Busy reads the provider's own retry time (`Retry-After`, rate-limit headers,
Google's `retryDelay`), else 60s, never doubling. 500/503 are only ever
Busy. 402, 403 and a 429 whose quota is 0 make the model Needs you at once.
ADR 0016 still holds, in that they refuse a model, not an account, but the
model now waits for the owner instead of sitting in a 24-hour quarantine.
"No speed data" is not a status: unmeasured models are tried.

The old state files are migrated once on first start and renamed
`*.migrated-v2.3`.

## Consequences

One page (Status) and one vocabulary everywhere: Overview, Providers,
Models, `/v1/models` and `/api/statuses` all say the same word for the same
model. Nothing is benched for days by an overload.
