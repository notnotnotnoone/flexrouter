"""Pull the human sentence out of a provider's error body.

Providers disagree about error shape. Real bodies observed from this router's
own configured providers:

    cerebras  {"message": "Model zai-glm-4.7 is archived and unavailable...",
               "type": "model_archived_error", "code": "model_archived"}
    googleai  [{"error": {"code": 404, "message": "This model is no longer
               available. Please update your code to use ..."}}]
    openai    {"error": {"message": "Incorrect API key provided", ...}}

Before this, failures were recorded as `resp.text[:200]` — a truncated raw
blob, so the dashboard and the quarantine reasons showed
`{"error":{"message":"This model is no lon` instead of the sentence that
tells you what to do. The message is the whole point of keeping the error.
"""
from __future__ import annotations

import json
from typing import Any

MAX_LENGTH = 300

# Keys that carry a human-readable sentence, in order of preference.
_MESSAGE_KEYS = ("message", "error_message", "detail", "description", "error")


def _dig(node: Any, depth: int = 0) -> str | None:
    """Find the first human-readable message in a decoded JSON structure."""
    if depth > 6:
        return None

    if isinstance(node, str):
        text = node.strip()
        return text or None

    if isinstance(node, list):
        for item in node:
            found = _dig(item, depth + 1)
            if found:
                return found
        return None

    if isinstance(node, dict):
        # Prefer a message on this level before descending, so the outer
        # "message" wins over some nested field that merely contains a string.
        for key in _MESSAGE_KEYS:
            if key in node:
                found = _dig(node[key], depth + 1)
                if found:
                    return found
        for value in node.values():
            found = _dig(value, depth + 1)
            if found:
                return found

    return None


def extract_error_message(body: str | bytes | None) -> str | None:
    """Best-effort human message from a provider error body.

    Returns None when there is nothing worth showing, so callers can fall back
    to the status code alone rather than printing an empty string.
    """
    if body is None:
        return None
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return None

    body = body.strip()
    if not body:
        return None

    try:
        decoded = json.loads(body)
    except (ValueError, TypeError):
        # Not JSON — plain text bodies (nginx pages, gateway errors) are still
        # worth showing, just collapsed and clipped.
        return _clip(" ".join(body.split()))

    found = _dig(decoded)
    return _clip(" ".join(found.split())) if found else None


def _clip(text: str) -> str:
    if len(text) <= MAX_LENGTH:
        return text
    return text[:MAX_LENGTH - 1].rstrip() + "…"


def describe_http_error(status_code: int, provider: str, model: str | None,
                        body: str | bytes | None) -> str:
    """One readable line for a failed provider response."""
    where = f"{provider}/{model}" if model else provider
    message = extract_error_message(body)
    if message:
        return f"{status_code} from {where}: {message}"
    return f"{status_code} from {where}"
