# Issue 05: The dashboard front end was not rebuilt or re-checked

Status: needs-triage

## What

Stage 1 changed what the dashboard's settings endpoint returns: credential fields now
come back masked rather than raw, settings are validated against an allow-list, and
the endpoint refuses anything holding a credential.

The React front end under `dashboard/frontend/` was not rebuilt, and its own test
suite does not run as part of the Python suite, so none of this was exercised against
the real interface. One specific risk is known and unverified: if the settings screen
reads a value and writes it straight back, it would now be posting a masked
placeholder rather than the real value. Writing a credential through that endpoint has
always been refused outright, so the path was already broken — but nobody has
confirmed what the screen actually does.

The Setup tab's instructions were rewritten in source but the bundle was not
regenerated, so a stale build still shows the old text.

## Done when

- The front end builds and its own tests run.
- The settings screen is exercised against the current interface, and whatever it does
  with a masked value is either correct or fixed.
- The shipped bundle reflects the rewritten Setup tab.

## Note

Spec section 7 rebuilds this dashboard to an approved mock-up in a later stage, so
weigh how much is worth fixing now against being rewritten then.
