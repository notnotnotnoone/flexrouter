# 0015. A generated app password lives in the keys file and wins over auth_token

Date: 2026-09-22
Status: Accepted

## Context

`/v1` can require a password (`auth_token` in the settings file, ADR 0009). The owner wanted to be able to set one from the dashboard's Settings page. Two standing rules are in the way: the machine never rewrites the settings file (ADR 0002), and credentials should not live in it at all (ADR 0003). Separately, the Stage 8 roadmap (ruling R5) rejected a dashboard field that *accepts* a password: anything that could reach the dashboard could then set a password it already knows.

## Decision

The dashboard can only **generate** a password, never accept one. `flexrouter/app_password.py::generate` makes a random `fr-…` token (`secrets.token_urlsafe(32)`), stores it in `keys.json` under the pseudo-provider `flexrouter-app-password` (masked everywhere and locked like every other key, ADR 0003), and returns it once so the Settings page can show it a single time for copying.

The password `/v1` checks against is resolved in this order, in exactly one function (`app_password.effective`), which both the server (`app._check_token`) and the client library (`_client_router`) call:

1. A generated password in `keys.json`.
2. `auth_token` in the settings file.
3. Neither: `/v1` is open, as before.

## Consequences

- An owner who already uses `auth_token` sees no change until they press "Make a new password". After that, the generated one wins, and the Settings page says so. Apps still sending the old `auth_token` start getting 401s.
- "Forget the generated password" removes it, and the settings file's `auth_token` applies again.
- The settings file stays read-only to the machine and free of credentials.
- `/v1` now reads `keys.json` on every request to resolve the password. The file is small and already read on hot reload, so this was judged cheaper than a cache that could serve a stale password after a regenerate.
