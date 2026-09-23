"""The importable FlexRouter: a thin client for the service on this machine.

It used to route in-process. That is what gave every project its own copy of
the settings, its own keys, and its own private idea of how much of each
provider's allowance was left — the fault the whole of v2 exists to fix. The
routing code still exists and is still what runs; it is just on the other end
of a local connection now, in one process, with one set of books.

Method signatures are unchanged on purpose, so existing code keeps working
without an edit.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional

import httpx

from flexrouter._router import (
    DeltaEvent, DoneEvent, ReasoningDeltaEvent, StreamEvent, ToolCallDeltaEvent,
)
from flexrouter.exceptions import RouterBusy, RouterError, ServiceNotRunning

_NOT_RUNNING = """flexrouter isn't running. Start it with:

    flexrouter serve

(then this will work). Checked {base_url} - nothing listening."""

_CUT_OFF = ("The answer from flexrouter at {base_url} was cut off part-way "
            "through: the connection dropped while it was still answering. "
            "Everything received before the drop was delivered.")


def _read_settings(config_path: Optional[str]):
    """Read the shared settings, reporting a broken home as a ConfigError.

    Dropping the old blanket `except` around this uncovered failures that are
    not read failures at all: if the shared settings folder cannot be created
    or reached, `ensure_home()` raises a bare `OSError` (a `FileExistsError`,
    say) straight out of the constructor. That is not a project error type and
    a caller has nothing to catch. Map it onto the one the project already
    has.
    """
    from flexrouter import home
    from flexrouter.config import load_config
    from flexrouter.exceptions import ConfigError

    try:
        home.ensure_home()
    except ConfigError:
        raise
    except OSError as exc:
        raise ConfigError(
            f"Cannot open the shared settings folder at {home.home_dir()}: "
            f"{type(exc).__name__}") from exc
    return load_config(config_path)


class FlexRouter:
    def __init__(self, config_path: Optional[str] = None, *,
                 base_url: Optional[str] = None,
                 timeout: float = 300.0) -> None:
        from flexrouter import home

        # The port and the optional local key both live in the settings this
        # machine shares. Reading them is the only thing the client still
        # needs the settings file for: it does not read providers, models or
        # provider keys, and it never routes.
        #
        # A settings file that cannot be read at all is not swallowed. It used
        # to be, and the fallback then guessed the default port -- so an owner
        # whose service was running happily on a configured port was told
        # "flexrouter isn't running", the one case where that message is
        # actively wrong. A broken settings file is its own problem and says
        # so. `load_config` creates a starter home when there is none, so
        # "no settings yet" never reaches here; only a genuinely unreadable
        # file does. Settings that parse but name no port still fall back
        # quietly, which is what the `or` below is for.
        #
        # The exception is an explicit `base_url`. A caller who names the
        # address has already said where the service is and needs nothing from
        # the settings but the key; an unrelated broken settings file must not
        # stop them. In that one case a failure is tolerated and we carry on
        # with no key.
        try:
            cfg = _read_settings(config_path)
        except Exception:
            if base_url is None:
                raise
            cfg = None

        port = (cfg.port if cfg is not None else None) or home.DEFAULT_PORT
        # Same order the server checks in (ADR 0015): a password generated
        # from the dashboard first, the settings file's auth_token second.
        from flexrouter import app_password
        self._token = app_password.effective(cfg.auth_token if cfg is not None else None)

        self._base_url = base_url or f"http://127.0.0.1:{port}"
        self._http = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -- the wire ---------------------------------------------------------

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def _payload(self, messages: list[dict], tier: str, stream: bool,
                 kwargs: dict) -> dict:
        # `tier` is the parameter's public name and always has been; on the
        # wire the same string is `model`, and it may name a bucket or one
        # specific provider's model. See flexrouter/wire.py.
        return {"model": tier, "messages": messages, "stream": stream, **kwargs}

    def _raise_for(self, response: httpx.Response) -> None:
        try:
            err = response.json().get("error") or {}
        except Exception:
            err = {}
        message = err.get("message") or response.text or f"HTTP {response.status_code}"
        if response.status_code == 503:
            raise RouterBusy(message)
        raise RouterError(message)

    # -- the same three methods as before ---------------------------------

    async def agenerate(self, messages: list[dict], tier: str,
                        wait: bool = True, vision: bool = False,
                        session_id: Optional[str] = None, **kwargs) -> dict:
        payload = self._payload(messages, tier, False, kwargs)
        try:
            response = await self._http.post("/v1/chat/completions",
                                             json=payload, headers=self._headers())
        except httpx.TransportError as exc:
            # TransportError is the common parent of connect, read, write and
            # protocol errors. Nothing listening, listening but never
            # answering, and a reset mid-request are all "could not get an
            # answer out of the service", and all want the one message that
            # says how to start it and which address was tried. Catching only
            # ConnectError left a connect timeout, a 300s read timeout and a
            # reset escaping as raw httpx errors, which are not RouterError
            # either, so `except RouterError` did not see them.
            raise ServiceNotRunning(
                _NOT_RUNNING.format(base_url=self._base_url)) from exc
        if response.status_code >= 400:
            self._raise_for(response)
        return response.json()

    async def agenerate_stream(self, messages: list[dict], tier: str,
                               vision: bool = False,
                               session_id: Optional[str] = None,
                               **kwargs) -> AsyncIterator[StreamEvent]:
        payload = self._payload(messages, tier, True, kwargs)
        request = self._http.build_request("POST", "/v1/chat/completions",
                                           json=payload, headers=self._headers())
        try:
            response = await self._http.send(request, stream=True)
        except httpx.TransportError as exc:
            raise ServiceNotRunning(
                _NOT_RUNNING.format(base_url=self._base_url)) from exc

        try:
            if response.status_code >= 400:
                await response.aread()
                self._raise_for(response)

            text_parts: list[str] = []
            tool_calls: list[dict] = []
            finish = "stop"
            failure: Optional[str] = None

            try:
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data: "):
                        continue
                    data = line[len("data: "):].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)

                    if chunk.get("error"):
                        # The service's one error chunk. Everything already
                        # yielded stays yielded; the caller finds out by this
                        # generator raising once the stream ends, which is what a
                        # partial answer that failed actually is.
                        failure = chunk["error"].get("message") or "stream failed"
                        continue

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    if choices[0].get("finish_reason"):
                        finish = choices[0]["finish_reason"]

                    if delta.get("content"):
                        text_parts.append(delta["content"])
                        yield DeltaEvent(text=delta["content"])
                    if delta.get("reasoning_content"):
                        yield ReasoningDeltaEvent(text=delta["reasoning_content"])
                    for raw in delta.get("tool_calls") or []:
                        fn = raw.get("function") or {}
                        tool_calls.append(raw)
                        yield ToolCallDeltaEvent(
                            index=raw.get("index", 0), id=raw.get("id"),
                            name=fn.get("name"), arguments=fn.get("arguments"),
                            raw=raw)
            except httpx.TransportError as exc:
                # The connection died while the service was still
                # answering. Not ServiceNotRunning: it did answer. The
                # pre-response catch can never see this one, and it is
                # the ordinary failure on a long answer. Everything
                # already yielded stays yielded and the caller then
                # sees the error -- the same shape as the service's own
                # mid-stream failure chunk.
                raise RouterError(
                    _CUT_OFF.format(base_url=self._base_url)) from exc

            if failure is not None:
                raise RouterError(failure)

            message: dict = {"role": "assistant", "content": "".join(text_parts)}
            if tool_calls:
                message["tool_calls"] = _assemble(tool_calls)
            yield DoneEvent(result={
                "choices": [{"index": 0, "message": message,
                             "finish_reason": finish}]})
        finally:
            await response.aclose()

    def generate(self, messages: list[dict], tier: str, wait: bool = True,
                 vision: bool = False, session_id: Optional[str] = None,
                 **kwargs) -> dict:
        return self._run(self.agenerate(messages, tier, wait=wait, vision=vision,
                                        session_id=session_id, **kwargs))

    # -- housekeeping -----------------------------------------------------

    def _run(self, coro):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(coro)

    def reload(self) -> None:
        """Nothing to reload. The service picks up settings changes itself.

        Kept on the class so code written against the old in-process router
        keeps running unchanged.
        """
        return None

    async def aclose(self) -> None:
        """Release the connection pool from a caller that already has a loop.

        The correct way to release an instance that was only ever awaited.
        """
        await self._http.aclose()

    def close(self) -> None:
        """Release the connection pool. Safe to call more than once.

        Two shapes, because there are two ways to have used this object. If
        `generate()` made a loop of our own, close through it and then close
        the loop. If it did not -- the caller only ever awaited `agenerate()`
        -- there is still an open `httpx.AsyncClient` to release, and the old
        version returned without touching it, leaking the pool until garbage
        collection.
        """
        if self._loop is not None and not self._loop.is_closed():
            self._loop.run_until_complete(self._http.aclose())
            self._loop.close()
            self._loop = None
            return
        if self._http.is_closed:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._http.aclose())
        else:
            # Synchronous close cannot await inside someone else's running
            # loop. Saying so is better than the silent leak this replaces.
            raise RuntimeError(
                "close() cannot run inside a running event loop; "
                "use `await router.aclose()` (or `async with router`).")

    def __enter__(self) -> "FlexRouter":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    async def __aenter__(self) -> "FlexRouter":
        return self

    async def __aexit__(self, *_) -> None:
        await self.aclose()


def _assemble(deltas: list[dict]) -> list[dict]:
    """Stitch streamed tool-call fragments back into whole calls.

    Keyed by the provider's own index, and each fragment's dictionary is
    merged rather than read for four known fields, so a provider-specific key
    survives into the assembled call.
    """
    calls: dict[int, dict] = {}
    for raw in deltas:
        idx = raw.get("index", 0)
        call = calls.setdefault(idx, {"id": "", "type": "function",
                                      "function": {"name": "", "arguments": ""}})
        for key, value in raw.items():
            if key in ("index", "function"):
                continue
            if value:
                call[key] = value
        fn = raw.get("function") or {}
        if fn.get("name"):
            call["function"]["name"] = fn["name"]
        if fn.get("arguments"):
            call["function"]["arguments"] += fn["arguments"]
    return [calls[k] for k in sorted(calls)]
