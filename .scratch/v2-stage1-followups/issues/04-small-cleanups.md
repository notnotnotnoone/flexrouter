# Issue 04: Small cleanups deferred from the Stage 1 review

Status: ready-for-agent

Five independent one-liners, grouped because none justifies its own issue.

## 1. Duplicate masking helper

`flexrouter/app.py` carries its own `_mask()` (producing `...1234`) alongside
`flexrouter/keys.py`'s `mask()` (producing a leading ellipsis). Two masking functions
with different output contradicts the "only `mask()` output" rule in spirit, and
hardening applied to one was not applied to the other. Delete the one in `app.py` and
use `keys.mask`.

## 2. `mask()` shows all of a four-character secret

`keys.mask("abcd")` returns the whole thing. The sub-four case was fixed during the
stage; exactly four was not. Academic for real keys, but this is the single masking
primitive the whole tool depends on.

## 3. JSON null reads as "off"

`overrides._is_off()` treats `None` as "disable this model". Some clients send null to
mean "no opinion". If the dashboard ever does, a model silently disappears from
routing. Decide which it means, and record the decision in `CONTEXT.md` rather than
only in a docstring.

## 4. The build directory is tracked

`build/lib/flexrouter/` is a stale checked-in artifact predating the v2 work. It still
contains modules deleted from the source tree, including `onboard.py`, so repo-wide
searches turn up two versions of everything. Remove it and add it to `.gitignore`.

## 5. The repo-root flexrouter.yaml holds inline keys

It is the v1 settings file, superseded by the shared home. It has live keys typed into
it, so every test run that touches it prints deprecation warnings. Lift its keys with
`flexrouter keys import`, then delete it.
