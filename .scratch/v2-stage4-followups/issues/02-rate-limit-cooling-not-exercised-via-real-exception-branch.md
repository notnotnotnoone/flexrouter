# Issue 02: per-key `mark_cooling` on a real `RateLimitError` is untested via its actual except-branch

Status: ready-for-agent

## What

`flexrouter/_router.py` calls `self._key_states.mark_cooling(...)` from two
`except RateLimitError as exc:` blocks (the `agenerate` path and the
`agenerate_stream` path), each mirroring the pre-existing model-level penalty call
that already lived there — three lines, no new branching.

Neither `tests/test_key_selection_agenerate.py` nor
`tests/test_key_selection_agenerate_stream.py` drives a real `RateLimitError`
through those except-blocks to prove the call site fires and passes the right
arguments. `test_key_selection_agenerate.py` (line 90) only calls
`router._key_states.mark_cooling(...)` directly to set up a precondition for a
different assertion; `test_key_selection_agenerate_stream.py` does not reference
`mark_cooling` at all. So the two call sites in `_router.py` — the actual thing
stage 4 added — are only covered indirectly, by whatever generic exception-handling
tests already existed before per-key state was wired in.

## Why it is not urgent

The call sites are trivial (they mirror an existing, already-tested pattern for the
model-level penalty), so the functional risk is low. This is a coverage gap, not a
known bug.

## Done when

- `tests/test_key_selection_agenerate.py` has a test that makes the underlying
  client raise a real `RateLimitError` for a chosen key, drives it through
  `agenerate`, and asserts the key's state (via `router._key_states.get(...)`)
  shows `status == "cooling"` afterward — not by calling `mark_cooling` directly.
- `tests/test_key_selection_agenerate_stream.py` has the equivalent test for the
  `agenerate_stream` path.
