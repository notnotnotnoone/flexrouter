# Issue 02: Treat a malformed 200 response as `ProviderError`, not a crash

Status: ready-for-agent

## What

`flexrouter/client.py`'s `AsyncClient.chat()` currently validates HTTP
status codes (429, 401/403, >=500, >=400) and that the body is valid JSON
(the `except Exception as exc: raise ProviderError(...)` around
`resp.json()`), but never checks that a 200 response actually has the
expected OpenAI-compatible shape — `response["choices"][0]["message"]["content"]`.
A provider that returns 200 with a differently-shaped or empty body (seen
live during the investigation that surfaced this: a `200 OK` missing the
`choices` key entirely) causes a `KeyError`/`IndexError` at whatever call
site indexes into the result, uncaught by anything in this library.

That's a real gap in the retry contract: from the router's retry loop's
point of view (`_router.py`'s `agenerate()`, and the new `agenerate_stream()`
from the sibling `sse-streaming` feature if that's landed by the time this
is picked up), the only things that trigger a retry-and-rotate-to-next-model
are `RateLimitError`/`ProviderError`. A malformed-but-200 response doesn't
raise either — it raises whatever bare exception the caller's own indexing
produces, which the retry loop was never written to catch, so it escapes
the loop and looks like a hard crash instead of "that model gave a bad
response, try the next one."

## Fix

In `chat()`, after confirming `resp.ok`, validate the parsed body has the
shape callers actually need before returning it:

```python
data = resp.json()
try:
    _ = data["choices"][0]["message"]["content"]
except (KeyError, IndexError, TypeError) as exc:
    raise ProviderError(
        f"{route.provider}/{route.model}: malformed response, missing choices[0].message.content ({exc})"
    ) from exc
return data
```

(Keep returning the full `data` dict, not just the extracted content —
callers use other fields like `usage`, `model`, etc. This is purely a
shape-check before trusting the body, not a change to what gets returned.)

If the sibling `sse-streaming` feature's `stream_chat()` has already landed
by the time this is picked up, apply the same validation there too — each
parsed SSE chunk should be checked for the expected
`choices[0].delta` shape before extracting `.content`, raising
`ProviderError` on a malformed chunk the same way. If `stream_chat()`
doesn't exist yet, just fix `chat()` here and note in a comment (or in this
issue's Comments section) that `stream_chat()` needs the same treatment
when it's built.

## Test coverage

Add a test alongside `chat()`'s existing tests: mock a 200 response with a
body missing `choices` entirely (or with `choices: []`), assert `chat()`
raises `ProviderError` rather than letting `KeyError`/`IndexError` escape.
Also confirm the existing happy-path test (well-formed response) still
passes unchanged.

## Comments
