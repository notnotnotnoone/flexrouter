# Issue 01: "no such model" can hide a real bug

Status: ready-for-agent

## What

`chat_completions` and `_stream_chat` in `flexrouter/app.py` each wrap the routing call
in `except KeyError` and answer with a 404 saying the named bucket or model does not
exist. That is correct for the case it was written for: the pin engine signals an
unconfigured `provider/model` with a plain `KeyError`.

But `KeyError` is not a distinguishable signal. A genuine `KeyError` raised anywhere
inside `agenerate`'s call graph — routing, provider selection, quota bookkeeping — is
caught by the same handler and reported to the caller as "there is no bucket or model
named X". The real defect is swallowed and the message is actively misleading.

## Why it is not urgent

The pinned-model path is well covered by tests, and a stray `KeyError` from deeper in
the router has not been observed. The cost is diagnostic, not functional.

## Done when

- An unconfigured pinned model is signalled by something narrower than a bare
  `KeyError` — a dedicated exception type, or a check in `app.py` against the
  configured models before routing.
- A `KeyError` from anywhere else reaches the generic handler and is reported as a
  server error, not as a missing model.
- A test drives a `KeyError` raised from inside routing and asserts it is *not*
  reported as a missing model.

## Constraint

`flexrouter/engine.py` is reused-unchanged by spec decree. The fix belongs in
`flexrouter/_router.py` or `flexrouter/app.py`, not there.
