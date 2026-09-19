# Issue 01: Override checking validates field names but not value types

Status: ready-for-agent

## What

`flexrouter/overrides.py`'s `ALLOWED_FIELDS` / `check_fields()` stops the dashboard
writing a field that settings loading cannot understand, but it only checks the
field's *name*. The value can still be any JSON type.

Confirmed live during review: `POST /api/config {"settings": {"state_dir": 5}}` is
accepted, `load_config()` then returns `state_dir=5`, and constructing `FlexRouter()`
raises `TypeError: expected str, bytes or os.PathLike object, not int` — which is not
a `ConfigError`, so the friendly handlers miss it. The same shape applies to `hooks`
(accepts a bare string where a list is expected) and to a model's `score` (accepts
`"abc"`).

`load_config`'s `_number()` helper already turns numeric coercion failures into a
`ConfigError` naming the setting. That covers only the fields it touches.

## Why it is not urgent

`flexrouter config reset` recovers a wedged home, and `flexrouter doctor` still runs
and shows the offending change, so nothing is unrecoverable.

## Done when

- `check_fields` validates each field's expected type as well as its name, and refuses
  with a message naming the field and what it expected.
- Anything that still slips through surfaces as a `ConfigError` naming the setting,
  never a bare `TypeError`.
- Tests cover a wrong type in each of the three sections.
