# Issue 01: `AsyncClient.stream_chat()` — per-provider streaming HTTP call

Status: ready-for-agent

## What

Add a new method to `flexrouter/client.py`'s `AsyncClient`, alongside the
existing `chat()` (unchanged):

```python
async def stream_chat(
    self,
    route: RouteResult,
    messages: list[dict],
    **kwargs,
) -> AsyncIterator[str]:
    """Yields text deltas as they arrive. Raises the same RateLimitError /
    RouterError / ProviderError as chat(), and — critically — only before
    the first delta is yielded. Once this generator has yielded at least
    one chunk, any later failure must raise mid-iteration (the caller sees
    it as an exception from the generator, same as any Python generator
    that errors), NOT be swallowed or retried here. Retry policy is the
    router's job, not the client's — see Issue 02."""
```

All the existing providers are reached through the same `{base_url}/chat/completions`
shape `chat()` already uses uniformly (see how `chat()` treats every provider
identically today — no provider-specific branching exists anywhere in this
file). Assume the same is true for streaming: request with `"stream": true`
in the payload, and parse the response as OpenAI-compatible SSE
(`data: {"choices":[{"delta":{"content":"..."}}]}` lines, terminated by a
literal `data: [DONE]` line). This is the de facto standard essentially every
OpenAI-compatible endpoint (Groq, OpenRouter, etc.) implements, which is
exactly why `chat()` never needed provider-specific code either.

**If a provider doesn't actually support streaming** (returns a normal
non-chunked JSON body even with `stream: true` set, or errors on the
parameter): don't special-case it in this method. If parsing SSE from a
non-SSE response fails, let it raise `ProviderError` the same way malformed
JSON already does in `chat()` — the router's retry loop (Issue 02) will
rotate to a different model exactly like it does for any other
`ProviderError` today. No provider allow-list or capability flag needed for
a first version; let real failures surface and get handled by the existing
retry mechanism rather than guessing in advance which of the 55 configured
models support streaming.

## Implementation sketch

Use `httpx.AsyncClient.stream("POST", url, json=payload, headers=headers)`
as an async context manager (not the plain `.post()` `chat()` uses — that
buffers the whole body before returning). Check `resp.status_code` right
after entering the context (before iterating lines) and raise the same
`RateLimitError`/`RouterError`/`ProviderError` `chat()` already raises for
429 / 401,403 / >=500 / >=400 — this must happen *before* any content is
yielded, so Issue 02's retry loop can tell "failed before we got anything"
apart from "failed after we started getting content" the same way it does
for `chat()` today.

Then iterate `resp.aiter_lines()`, skip blank lines and anything not
starting with `data: `, strip that prefix, break on `[DONE]`, `json.loads`
the rest, pull `chunk["choices"][0]["delta"].get("content")`, and `yield` it
if non-empty (a delta can legitimately carry an empty content string or only
a role field, e.g. the very first chunk — skip yielding when there's nothing
to add, don't yield empty strings).

Wrap the whole iteration in a `try`/`except` for `httpx.HTTPError` /
`json.JSONDecodeError` / `KeyError`/`IndexError` (malformed chunk shape) and
raise `ProviderError` — same spirit as `chat()`'s existing
`except Exception as exc: raise ProviderError(...)` around `resp.json()`.

Rate-limit-header bookkeeping (the `self._rate_limit_store` update block in
`chat()`) is response-header-based, available as soon as the streamed
response's headers arrive — do the same header reads here right after
confirming the status code, before iterating the body.

## Test coverage

Add to wherever `client.py`'s existing tests for `chat()` live (find and
match that file's structure — don't invent a new test file layout). Cover:
successful stream (multiple chunks, `[DONE]` terminator, assert the yielded
text sequence and that final content matches the concatenation), a 429
before any content raises `RateLimitError` without yielding anything, a
malformed SSE line raises `ProviderError`, and — the important one — a
generator that yields one real delta and then the underlying stream errors
raises from within the iteration rather than being swallowed (assert the
caller sees an exception after consuming at least one item, not a silently
truncated stream).

## Comments
