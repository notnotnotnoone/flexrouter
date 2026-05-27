from __future__ import annotations
import httpx
from flexrouter.engine import RouteResult
from flexrouter.exceptions import RouterError


class RateLimitError(Exception):
    pass

class ProviderError(Exception):
    pass


class AsyncClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=60.0)

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
