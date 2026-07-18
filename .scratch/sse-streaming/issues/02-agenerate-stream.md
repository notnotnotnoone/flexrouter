# Issue 02: `agenerate_stream()` — retry-loop visibility + streamed output

Status: ready-for-agent

Depends on Issue 01 (`AsyncClient.stream_chat()`).

## What

Add a new method to the router class in `flexrouter/_router.py`, alongside
the existing `generate()`/`agenerate()` (unchanged — other consumers depend
on the blocking contract):

```python
async def agenerate_stream(
    self,
    messages: list[dict],
    tier: str,
    vision: bool = False,
    session_id: Optional[str] = None,
    **kwargs,
) -> AsyncIterator[StreamEvent]
```

Reuse `agenerate()`'s existing retry loop almost exactly (`for attempt in
range(retries + 1): route = self._engine.select(...)` — same penalty/backoff/
health-recording calls on failure) but yield progress instead of looping
silently, and call `self._client.stream_chat(route, messages, **kwargs)`
(Issue 01) instead of `self._client.chat(...)` for the actual request.

## Event shape

Four small dataclasses (matching the existing convention of typed results
like `RouteResult` in `engine.py` — not bare dicts):

```python
@dataclass
class AttemptEvent:
    attempt: int
    max_attempts: int
    provider: str
    model: str

@dataclass
class AttemptFailedEvent:
    attempt: int
    max_attempts: int
    provider: str
    model: str
    reason: Literal["rate_limited", "provider_error"]

@dataclass
class DeltaEvent:
    text: str

@dataclass
class DoneEvent:
    result: dict  # identical shape to what agenerate() returns today
```

`StreamEvent = AttemptEvent | AttemptFailedEvent | DeltaEvent | DoneEvent`

Yield an `AttemptEvent` right before each `self._client.stream_chat(...)`
call (same place `agenerate()` currently does `start = time.monotonic()`
before `await self._client.chat(...)`). On `RateLimitError`/`ProviderError`
from `stream_chat` — which per Issue 01 can only happen before any delta was
yielded — do the exact same penalize/record/audit/history calls
`agenerate()` already does, then yield an `AttemptFailedEvent` instead of
just `continue`-ing silently, then continue the loop as before.

Once `stream_chat` yields its first delta without error, this method starts
yielding `DeltaEvent(text=chunk)` for each chunk `stream_chat` produces.
Accumulate the full text (needed to build the final `DoneEvent.result` —
mirror whatever shape `agenerate()`'s successful return already has: usage,
latency, audit log call, etc., same as the non-streaming path, just built
from the accumulated deltas instead of one blocking response body). Yield
one `DoneEvent` at the end.

**On total exhaustion, raise `RouterBusy`** — do not yield a terminal
"failed" event type. This matches `agenerate()`'s existing behavior exactly
(`raise RouterBusy(f"All retries exhausted for tier {tier!r}")`) so the two
methods stay consistent, and it's the natural Python idiom for an async
generator anyway: the exception propagates out of whatever `async for
event in agenerate_stream(...)` loop the caller has, same as any generator
that errors. Callers that want a non-raising terminal signal (Stash's SSE
endpoint, for instance) wrap the loop in a `try`/`except RouterBusy` and
translate it into whatever their own terminal signal is — that's the
caller's framing to choose, not this library's.

**No retry once a delta has been yielded** (see the PRD's "No retry after a
model commits" section) — this falls out naturally from the structure above
since `stream_chat`'s own contract (Issue 01) only raises before the first
yield; a failure after that point propagates as an ordinary exception out of
`agenerate_stream()` itself, ending the generator. Do not wrap
`stream_chat`'s iteration in a broad try/except that would catch and retry
a mid-stream failure — that would silently violate the no-retry-after-commit
requirement.

## Test coverage

Add to wherever `_router.py`'s existing `agenerate()` tests live (match that
file's mocking approach for `self._client` and `self._engine.select`). Cover:
a clean single-attempt success (one `AttemptEvent`, some `DeltaEvent`s, one
`DoneEvent`, matching what `agenerate()` would have returned for the same
mocked response); N failed attempts (rate-limited, then provider error, then
success) producing the exact interleaved `AttemptEvent`/`AttemptFailedEvent`
sequence before the eventual `DeltaEvent`s/`DoneEvent`; total exhaustion
raising `RouterBusy` after the expected number of `AttemptFailedEvent`s: and
a mid-stream failure (mock `stream_chat` to yield one chunk then raise)
propagating as an exception from the `agenerate_stream()` generator itself,
with no further `AttemptEvent` following it (proving no retry happened).

## Comments
