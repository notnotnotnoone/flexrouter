from __future__ import annotations
import re as _re
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
        headers = {"Authorization": f"Bearer {route.api_key}"}

        resp = await self._client.post(url, json=payload, headers=headers)

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
            return resp.json()
        except Exception as exc:
            raise ProviderError(f"Invalid JSON from {route.provider}/{route.model}: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self.aclose()
