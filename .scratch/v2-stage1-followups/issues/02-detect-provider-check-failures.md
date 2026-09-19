# Issue 02: A dead provider reads as "checked, everything vanished"

Status: ready-for-human

## What

`flexrouter refresh` now records what it finds without touching the settings file, and
correctly omits a provider from the pending record when its check *raises*. But
`flexrouter/catalogue.py`'s `discover_models()` ends in `except Exception: return []`
and also returns `[]` for any error response from the provider.

So in practice a provider that is down, or whose key has been rejected, never raises.
It returns no models, counts as successfully checked, and every model configured
against it is reported as vanished. That is the exact failure the "omit errored
providers" fix was written to prevent — the bookkeeping is right, the detection is not
there.

`flexrouter/probe.py` already distinguishes "key rejected" from "no models" and is the
obvious source for the missing signal.

## Why it is not urgent

Nothing is applied automatically. The pending record is a proposal the owner accepts,
so a wrong proposal is visible and rejectable.

## Done when

- `discover_models()` distinguishes "this provider answered and has no models" from
  "this provider could not be reached" and from "this key was rejected".
- A provider in either failure state is excluded from the pending record and reported
  as an error instead.
- A test drives a provider returning an error response, not only one that raises.

## Relationship to the design

Spec section 4a's error brain is the eventual home for classifying these responses.
If Stage 5 lands first, do this there.
