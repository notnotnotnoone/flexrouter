# Issue 03: `tests/test_config.py` shallow-copies the shared MINIMAL_CONFIG

Status: ready-for-agent

## What

Stage 8 fixed one instance of this: a fixture did `dict(MINIMAL_CONFIG)` — a
shallow copy — then wrote into a nested dict, mutating the shared constant in
`tests/conftest.py`. It survived only because `tests/` has no `__init__.py`,
so pytest loads the conftest twice under two names and the mutation landed on
the copy nobody used.

`tests/test_config.py` (around lines 71-72 and 91-92) still does its own
`from tests.conftest import MINIMAL_CONFIG` followed by `dict(MINIMAL_CONFIG)`.
Same bug class, untouched file, out of Stage 8's scope.

## Why it is not urgent

It does not fail today, for the same accidental reason.

## Done when

- `tests/test_config.py` uses the `minimal_config` fixture that Stage 8 added
  to `tests/conftest.py` (it returns a `copy.deepcopy`), or deep-copies itself.
- Ideally: add `tests/__init__.py` or switch to `importmode=importlib`, which
  would collapse the double-load and make this class of bug fail loudly. Do
  that only after every shallow copy is fixed, or a pile of unrelated tests
  will start failing at once.
