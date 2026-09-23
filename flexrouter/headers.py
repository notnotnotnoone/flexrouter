"""Pluggable per-provider rate-limit header parsing.

Different providers expose rate-limit information via slightly different
response headers (or none at all). This module normalizes that into a
single `ParsedHeaders` shape via a registry of parser functions keyed by
provider name, so the client doesn't need to special-case providers.
"""
from __future__ import annotations
import re
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class ParsedHeaders:
    limit_requests: int | None = None
    limit_tokens: int | None = None
    remaining_requests: int | None = None
    remaining_tokens: int | None = None
    reset_requests_at: float | None = None  # epoch seconds
    reset_tokens_at: float | None = None  # epoch seconds


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
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?(?:(\d+)ms)?", text)
    if not m or not any(m.groups()):
        return None
    ms = 0.0
    if m.group(1):
        ms += int(m.group(1)) * 3_600_000
    if m.group(2):
        ms += int(m.group(2)) * 60_000
    if m.group(3):
        ms += float(m.group(3)) * 1000
    if m.group(4):
        ms += int(m.group(4))
    return int(ms) if ms else None


def _parse_int(headers, name: str) -> int | None:
    val = headers.get(name)
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _parse_reset_at(headers, name: str) -> float | None:
    ms = _parse_duration_ms(headers.get(name))
    if ms is None:
        return None
    return time.time() + ms / 1000


def _parse_openai_compatible(headers) -> ParsedHeaders:
    norm = {str(k).lower(): v for k, v in dict(headers).items()}
    return ParsedHeaders(
        limit_requests=_parse_int(norm, "x-ratelimit-limit-requests"),
        limit_tokens=_parse_int(norm, "x-ratelimit-limit-tokens"),
        remaining_requests=_parse_int(norm, "x-ratelimit-remaining-requests"),
        remaining_tokens=_parse_int(norm, "x-ratelimit-remaining-tokens"),
        reset_requests_at=_parse_reset_at(norm, "x-ratelimit-reset-requests"),
        reset_tokens_at=_parse_reset_at(norm, "x-ratelimit-reset-tokens"),
    )


def _parse_google(headers) -> ParsedHeaders:
    norm = {str(k).lower(): v for k, v in dict(headers).items()}
    for candidate in ("x-goog-ratelimit-remaining-requests", "x-goog-ratelimit-request-remaining"):
        if candidate in norm and "x-ratelimit-remaining-requests" not in norm:
            norm["x-ratelimit-remaining-requests"] = norm[candidate]
            break
    return _parse_openai_compatible(norm)


def _parse_groq(headers) -> ParsedHeaders:
    # Groq's x-ratelimit-limit-requests is requests per DAY (console.groq.com/
    # docs/rate-limits), so it is not an rpm. Remaining/reset still hold: at
    # zero remaining the model is out until that reset, whatever the window.
    parsed = _parse_openai_compatible(headers)
    parsed.limit_requests = None
    return parsed


def _parse_mistral(headers) -> ParsedHeaders:
    norm = {str(k).lower(): v for k, v in dict(headers).items()}
    return ParsedHeaders(
        limit_requests=_parse_int(norm, "x-ratelimit-limit-req-minute"),
        limit_tokens=_parse_int(norm, "x-ratelimit-limit-tokens-minute"),
        remaining_requests=_parse_int(norm, "x-ratelimit-remaining-req-minute"),
        remaining_tokens=_parse_int(norm, "x-ratelimit-remaining-tokens-minute"),
    )


PARSERS: dict[str, Callable[[dict], ParsedHeaders]] = {
    "openai_compatible": _parse_openai_compatible,
    "groq": _parse_groq,
    "mistral": _parse_mistral,
    "cerebras": _parse_openai_compatible,
    "openrouter": _parse_openai_compatible,
    "siliconflow": _parse_openai_compatible,
    "google": _parse_google,
}


def parse_headers(parser_name: str, headers) -> ParsedHeaders:
    parser = PARSERS.get(parser_name, _parse_openai_compatible)
    return parser(headers)
