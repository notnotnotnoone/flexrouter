from __future__ import annotations
import json as _json
import logging
from dataclasses import dataclass
from typing import AsyncIterator
import httpx
from flexrouter.engine import RouteResult
from flexrouter.errors import describe_http_error
from flexrouter.exceptions import RouterError
from flexrouter.headers import parse_headers

logger = logging.getLogger(__name__)


class RateLimitError(Exception):
    pass

# Statuses that mean "this route will never work again", as opposed to "try
# again later". A model the provider has deleted answers 404 forever, so
# backing off 30s and retrying it is an infinite loop, not a recovery.
PERMANENT_STATUSES = frozenset({404, 410})

# Failures that are true of the whole account, not one model: 402 means the
# billing tab needs attention, and every model on that provider will answer
# the same way. Retrying each of them in turn just multiplies the wait.
PROVIDER_WIDE_STATUSES = frozenset({402})


class ProviderError(Exception):
    """A provider-side failure.

    ``status_code`` is the HTTP status when the failure came from a response,
    and None when it came from below the HTTP layer (connection reset,
    timeout, unparseable body). The router uses it to decide between a
    temporary penalty and a quarantine.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def is_permanent(self) -> bool:
        return self.status_code in PERMANENT_STATUSES

    @property
    def is_provider_wide(self) -> bool:
        return self.status_code in PROVIDER_WIDE_STATUSES


@dataclass
class StreamChunk:
    content: str | None = None
    reasoning: str | None = None
    tool_call_delta: dict | None = None
    usage: dict | None = None


class AsyncClient:
    def __init__(self, rate_limit_store=None) -> None:
        self._client = httpx.AsyncClient(timeout=60.0)
        self._rate_limit_store = rate_limit_store

    async def chat(
        self,
        route: RouteResult,
        messages: list[dict],
        **kwargs,
    ) -> dict:
        url = f"{route.base_url.rstrip('/')}/chat/completions"
        payload = {"model": route.model, "messages": messages, **kwargs}
        headers = {"Authorization": f"Bearer {route.api_key}"} if route.api_key else {}

        try:
            resp = await self._client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            # Connection failures, malformed requests, timeouts — anything below
            # the HTTP-response level. Must surface as ProviderError so the
            # router's retry loop (which only catches RateLimitError/
            # ProviderError/RouterError) rotates to another model instead of
            # this one dead route crashing the whole call.
            raise ProviderError(f"{route.provider}/{route.model}: {exc}") from exc

        if resp.status_code == 429:
            raise RateLimitError(f"429 from {route.provider}/{route.model}")
        if resp.status_code in (401, 403):
            raise RouterError(f"Auth failure for provider {route.provider!r}: {resp.status_code}")
        if resp.status_code >= 500:
            raise ProviderError(
                describe_http_error(resp.status_code, route.provider, route.model, resp.text),
                status_code=resp.status_code)
        if resp.status_code >= 400:
            raise ProviderError(
                describe_http_error(resp.status_code, route.provider, route.model, resp.text),
                status_code=resp.status_code)

        if self._rate_limit_store is not None:
            parsed = parse_headers(route.header_parser, resp.headers)
            if parsed.limit_requests is not None or parsed.limit_tokens is not None:
                self._rate_limit_store.update(route.provider, route.model, parsed.limit_requests, parsed.limit_tokens)
            self._rate_limit_store.update_headroom(
                route.provider, route.model,
                remaining_requests=parsed.remaining_requests, remaining_tokens=parsed.remaining_tokens,
                reset_requests_at=parsed.reset_requests_at, reset_tokens_at=parsed.reset_tokens_at,
            )

        try:
            data = resp.json()
        except Exception as exc:
            raise ProviderError(f"Invalid JSON from {route.provider}/{route.model}: {exc}") from exc

        try:
            _ = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"{route.provider}/{route.model}: malformed response, missing choices[0].message.content ({exc})"
            ) from exc
        return data

    async def stream_chat(
        self,
        route: RouteResult,
        messages: list[dict],
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """Yields text deltas as they arrive. Raises the same RateLimitError /
        RouterError / ProviderError as chat(), and — critically — only before
        the first delta is yielded. Once this generator has yielded at least
        one chunk, any later failure raises mid-iteration (the caller sees
        it as an exception from the generator, same as any Python generator
        that errors), NOT swallowed or retried here. Retry policy is the
        router's job, not the client's — see Issue 02."""
        url = f"{route.base_url.rstrip('/')}/chat/completions"
        payload = {"model": route.model, "messages": messages, "stream": True,
                   "stream_options": {"include_usage": True}, **kwargs}
        headers = {"Authorization": f"Bearer {route.api_key}"} if route.api_key else {}

        # Deliberately NOT `async with self._client.stream(...)`. That helper
        # is an @asynccontextmanager, and if this generator gets GeneratorExit
        # thrown into it while suspended inside that context manager's aexit
        # (e.g. the HTTP client on the other end disconnects mid-stream), the
        # inner generator can fail to unwind synchronously, and contextlib
        # raises "RuntimeError: generator didn't stop after athrow()" —
        # which corrupts this AsyncClient's connection pool for every request
        # after it (live-verified: this instance-level poisoning outlives the
        # request that caused it and silently hangs unrelated future calls).
        # A plain try/finally around client.send(..., stream=True) has no
        # such intermediate generator to fail to unwind, so cancellation just
        # runs the finally block like any other coroutine.
        request = self._client.build_request("POST", url, json=payload, headers=headers)
        try:
            resp = await self._client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{route.provider}/{route.model}: {exc}") from exc

        try:
            if resp.status_code == 429:
                raise RateLimitError(f"429 from {route.provider}/{route.model}")
            if resp.status_code in (401, 403):
                raise RouterError(f"Auth failure for provider {route.provider!r}: {resp.status_code}")
            if resp.status_code >= 400:
                # Read the body for 5xx too: a streamed 500 often carries the
                # only explanation we will ever get about why this model is
                # failing, and it used to be discarded.
                body = await resp.aread()
                raise ProviderError(
                    describe_http_error(resp.status_code, route.provider, route.model, body),
                    status_code=resp.status_code)

            if self._rate_limit_store is not None:
                parsed = parse_headers(route.header_parser, resp.headers)
                if parsed.limit_requests is not None or parsed.limit_tokens is not None:
                    self._rate_limit_store.update(route.provider, route.model, parsed.limit_requests, parsed.limit_tokens)
                self._rate_limit_store.update_headroom(
                    route.provider, route.model,
                    remaining_requests=parsed.remaining_requests, remaining_tokens=parsed.remaining_tokens,
                    reset_requests_at=parsed.reset_requests_at, reset_tokens_at=parsed.reset_tokens_at,
                )

            try:
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[len("data: "):].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = _json.loads(data_str)
                    except _json.JSONDecodeError as exc:
                        raise ProviderError(
                            f"{route.provider}/{route.model}: malformed SSE chunk: {exc}"
                        ) from exc

                    # Groq (live-verified) reports a malformed tool-call
                    # generation as an HTTP-200 stream carrying an in-band
                    # `data: {"error": {...}}` chunk with no "choices" key at
                    # all. Left unhandled, that chunk falls through to the
                    # `delta is None and usage is None: continue` branch below
                    # and is silently dropped — indistinguishable from the
                    # model having generated nothing, which masks a real,
                    # named failure as a generic empty response.
                    if "error" in chunk:
                        err = chunk["error"]
                        detail = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                        code = err.get("code") if isinstance(err, dict) else None
                        logger.warning(
                            "in-band stream error",
                            extra={
                                "event": "client.stream.inband_error",
                                "provider": route.provider, "model": route.model,
                                "code": code, "detail": detail,
                            },
                        )
                        raise ProviderError(
                            f"{route.provider}/{route.model}: {code or 'stream error'}: {detail}"
                        )

                    usage = chunk.get("usage")
                    choices = chunk.get("choices") or []
                    delta = choices[0].get("delta") if choices else None
                    if delta is None and usage is None:
                        continue

                    content = delta.get("content") if isinstance(delta, dict) else None
                    # OpenAI-compatible providers vary on the key for reasoning
                    # text: most use "reasoning_content", Cerebras (zai-glm-4.7,
                    # live-verified) uses "reasoning". Check both.
                    reasoning = (
                        delta.get("reasoning_content") or delta.get("reasoning")
                        if isinstance(delta, dict) else None
                    )
                    tool_calls = delta.get("tool_calls") if isinstance(delta, dict) else None

                    if tool_calls:
                        for tc in tool_calls:
                            logger.debug(
                                "raw tool_call delta",
                                extra={
                                    "event": "client.stream.tool_call_delta",
                                    "provider": route.provider, "model": route.model,
                                    "raw": tc,
                                },
                            )
                            yield StreamChunk(tool_call_delta=tc)
                        continue

                    if content or reasoning or usage:
                        yield StreamChunk(content=content or None, reasoning=reasoning or None, usage=usage)
            except httpx.HTTPError as exc:
                # Connection failures, malformed requests, timeouts, or a dropped
                # connection mid-stream — anything below the HTTP-response level.
                # Whether this happens before or after content has been yielded,
                # it must surface as ProviderError from the generator (not be
                # swallowed): if it's before the first delta, the router's retry
                # loop rotates to another model; if it's after, the partial
                # stream must fail loudly rather than be silently truncated.
                raise ProviderError(f"{route.provider}/{route.model}: {exc}") from exc
        finally:
            await resp.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self.aclose()
