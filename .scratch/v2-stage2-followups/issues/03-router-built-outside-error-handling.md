# Issue 03: a broken settings file escapes the error envelope

Status: ready-for-agent

## What

In `flexrouter/app.py`, `get_router()` is called outside the `try` blocks in both
`chat_completions` and `_stream_chat`. `get_router()` builds the router, which parses
the settings file. If that parse fails, the exception escapes both error helpers and is
handled by the web framework's own default handler instead.

The body is generic today only because `create_app` never turns on debug mode. In a
debug or auto-reload deployment the framework would render the traceback — and a
settings parse error carrying raw file text is the exact shape of a leak this project
has already shipped once.

## Why it is not urgent

This project is never run with debug mode on, and the surface binds to the local
machine only.

## Done when

- `get_router()` is inside the handled region, so a settings failure comes back in the
  OpenAI error envelope, scrubbed, like everything else.
- A test drives an unparseable settings file through a chat request and asserts the
  response is the error envelope rather than a framework default.
