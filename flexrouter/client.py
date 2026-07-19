from __future__ import annotations
import json as _json
import re as _re
from typing import AsyncIterator
import httpx
from flexrouter.engine import RouteResult
from flexrouter.exceptions import RouterError


class RateLimitError(Exception):
    pass

class ProviderError(Exception):
    pass


def _parse_duration_ms(s) -> int | None:
    if s is None:
        return None
    text = str(s).strip()
    if not text:
        return None
    try:
        return int(float(text) * 1000)  # bare number = seconds
    except ValueError:
        pass
    m = _re.fullmatch(r"(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?(?:(\d+)ms)?", text)
    if not m or not any(m.groups()):
        return None
    ms = 0.0
    if m.group(1):
        ms += int(m.group(1)) * 60_000
    if m.group(2):
        ms += float(m.group(2)) * 1000
    if m.group(3):
        ms += int(m.group(3))
    return int(ms) if ms else None


def _parse_int_header(headers, name: str) -> int | None:
    val = headers.get(name)
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


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
            raise ProviderError(f"{resp.status_code} from {route.provider}/{route.model}")
        if resp.status_code >= 400:
            raise ProviderError(f"{resp.status_code} from {route.provider}: {resp.text[:200]}")

        if self._rate_limit_store is not None:
            rpm = _parse_int_header(resp.headers, "x-ratelimit-limit-requests")
            tpm = _parse_int_header(resp.headers, "x-ratelimit-limit-tokens")
            if rpm is not None or tpm is not None:
                self._rate_limit_store.update(route.provider, route.model, rpm, tpm)
            import time as _time
            rem_r = _parse_int_header(resp.headers, "x-ratelimit-remaining-requests")
            rem_t = _parse_int_header(resp.headers, "x-ratelimit-remaining-tokens")
            reset_r = _parse_duration_ms(resp.headers.get("x-ratelimit-reset-requests"))
            reset_t = _parse_duration_ms(resp.headers.get("x-ratelimit-reset-tokens"))
            now = _time.time()
            self._rate_limit_store.update_headroom(
                route.provider, route.model,
                remaining_requests=rem_r, remaining_tokens=rem_t,
                reset_requests_at=(now + reset_r / 1000) if reset_r is not None else None,
                reset_tokens_at=(now + reset_t / 1000) if reset_t is not None else None,
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
    ) -> AsyncIterator[str]:
        """Yields text deltas as they arrive. Raises the same RateLimitError /
        RouterError / ProviderError as chat(), and — critically — only before
        the first delta is yielded. Once this generator has yielded at least
        one chunk, any later failure raises mid-iteration (the caller sees
        it as an exception from the generator, same as any Python generator
        that errors), NOT swallowed or retried here. Retry policy is the
        router's job, not the client's — see Issue 02."""
        url = f"{route.base_url.rstrip('/')}/chat/completions"
        payload = {"model": route.model, "messages": messages, "stream": True, **kwargs}
        headers = {"Authorization": f"Bearer {route.api_key}"} if route.api_key else {}

        try:
            async with self._client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code == 429:
                    raise RateLimitError(f"429 from {route.provider}/{route.model}")
                if resp.status_code in (401, 403):
                    raise RouterError(f"Auth failure for provider {route.provider!r}: {resp.status_code}")
                if resp.status_code >= 500:
                    raise ProviderError(f"{resp.status_code} from {route.provider}/{route.model}")
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise ProviderError(f"{resp.status_code} from {route.provider}: {body[:200]!r}")

                if self._rate_limit_store is not None:
                    rpm = _parse_int_header(resp.headers, "x-ratelimit-limit-requests")
                    tpm = _parse_int_header(resp.headers, "x-ratelimit-limit-tokens")
                    if rpm is not None or tpm is not None:
                        self._rate_limit_store.update(route.provider, route.model, rpm, tpm)
                    import time as _time
                    rem_r = _parse_int_header(resp.headers, "x-ratelimit-remaining-requests")
                    rem_t = _parse_int_header(resp.headers, "x-ratelimit-remaining-tokens")
                    reset_r = _parse_duration_ms(resp.headers.get("x-ratelimit-reset-requests"))
                    reset_t = _parse_duration_ms(resp.headers.get("x-ratelimit-reset-tokens"))
                    now = _time.time()
                    self._rate_limit_store.update_headroom(
                        route.provider, route.model,
                        remaining_requests=rem_r, remaining_tokens=rem_t,
                        reset_requests_at=(now + reset_r / 1000) if reset_r is not None else None,
                        reset_tokens_at=(now + reset_t / 1000) if reset_t is not None else None,
                    )

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

                    try:
                        delta = chunk["choices"][0]["delta"]
                    except (KeyError, IndexError, TypeError) as exc:
                        raise ProviderError(
                            f"{route.provider}/{route.model}: malformed response, missing choices[0].delta ({exc})"
                        ) from exc

                    content = delta.get("content") if isinstance(delta, dict) else None
                    if content:
                        yield content
        except httpx.HTTPError as exc:
            # Connection failures, malformed requests, timeouts, or a dropped
            # connection mid-stream — anything below the HTTP-response level.
            # Whether this happens before or after content has been yielded,
            # it must surface as ProviderError from the generator (not be
            # swallowed): if it's before the first delta, the router's retry
            # loop rotates to another model; if it's after, the partial
            # stream must fail loudly rather than be silently truncated.
            raise ProviderError(f"{route.provider}/{route.model}: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self.aclose()
