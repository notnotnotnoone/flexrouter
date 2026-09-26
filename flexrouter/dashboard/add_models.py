""""Add models with AI" - the copy-paste way, same shape as ranking.py.

Builds a ready-made prompt naming one provider and its already-configured
models, and parses the answer pasted back into a set of proposed model
rows: a JSON array of objects, one per model. No network call happens
here, same as ranking.py: the owner sends the prompt themselves, wherever
they like, and pastes the answer back.

Nothing here writes anything. `flexrouter/dashboard/pages.py` is what turns
a parsed row into an override (existing chat models), a brand-new bucket
entry (new chat models), or a parked-models.json entry (anything that
isn't `chat`), after the owner has reviewed and checked which rows to
apply.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

KINDS = (
    "chat", "embedding", "speech_to_text", "text_to_speech", "image",
    "safeguard", "unknown",
)

# Rate/quota columns, beyond the identity/description ones (provider, model,
# kind, context, vision, free, score). rpm/tpm are the two the routing
# engine enforces via its in-memory SlidingWindow; rph/rpd/rps/tph/tpd/tps
# all land in a model's `quotas` dict and are enforced by the persistent
# QuotaTracker (flexrouter/quota.py), which counts requests for the r*
# keys and sums tokens-per-call for the t* keys against the same window.
_RATE_FIELDS = ("rpm", "tpm", "rph", "rpd", "rps", "tph", "tpd", "tps")

# rps/tps convert from a per-day or per-month allowance into a per-second
# one, which is legitimately fractional - live-verified against Mistral's
# published limits (e.g. 500 requests/day -> 2.08 rps once weighted per
# model). The other rate fields stay whole numbers. Rejecting the AI's
# correct float here as "not a number" was the bug, not the AI's answer.
_FRACTIONAL_RATE_FIELDS = frozenset({"rps", "tps"})

# Generation throughput - how many tokens/second the model actually produces
# once it's running. Separate from `tps` above, which is a provider-enforced
# *rate limit* (tokens/sec this account is allowed to send), not a speed.
# Feeds ModelConfig.tokens_per_second, used by "fastest" buckets to rank
# models by speed instead of score.
_SPEED_FIELD = "gen_tps"

_REQUIRED_KEYS = (
    "provider", "model", "kind", "context", *_RATE_FIELDS, "vision", "free", "score",
)


@dataclass
class ParsedRow:
    provider: str
    model: str
    kind: str
    context: Optional[int]
    rpm: Optional[int]
    tpm: Optional[int]
    rph: Optional[int]
    rpd: Optional[int]
    rps: Optional[int]
    tph: Optional[int]
    tpd: Optional[int]
    tps: Optional[int]
    gen_tps: Optional[float]
    vision: bool
    free: bool
    score: Optional[int]
    raw_line: str


@dataclass
class ParseIssue:
    line: str
    reason: str


@dataclass
class ParseResult:
    rows: list[ParsedRow]
    issues: list[ParseIssue]


def providers_with_keys(router) -> list[str]:
    """Providers the owner has actually put a key against - the only ones
    worth asking an AI to name models for, since a provider with no key
    cannot be reached anyway."""
    return sorted(
        name for name, pcfg in router._cfg.providers.items() if pcfg.keys
    )


def build_prompt(provider: str, existing_models: list[str], notes: str,
                 known_ids: list[str] | None = None) -> str:
    example = {
        "provider": provider, "model": "exact-model-id", "kind": "chat",
        "context": 128000, "rpm": 30, "tpm": 6000, "rph": None, "rpd": 14400,
        "rps": None, "tph": None, "tpd": None, "tps": None, "gen_tps": 220,
        "vision": False, "free": True, "score": 70,
    }
    lines = [
        f"You are helping configure {provider} models for a personal LLM "
        "router (flexrouter). I will tell you what is already configured, "
        "and any docs or model list I have pasted below; you answer with "
        "every model this provider offers that isn't already listed.",
        "",
    ]
    if existing_models:
        lines.append(
            f"Already configured for {provider}: " + ", ".join(sorted(existing_models)))
    else:
        lines.append(f"Nothing is configured for {provider} yet.")

    if notes.strip():
        lines += ["", "Docs / model list I pasted:", notes.strip()]

    if known_ids:
        # grill-decisions.md §6: ground the AI in the provider's real, live
        # ID list rather than whatever it remembers from training - the
        # review screen still checks this itself, but a model that matches
        # the list up front means fewer "not a real ID" rows to fix by hand.
        lines += [
            "",
            f"{provider}'s real model IDs right now (from its own /models "
            "endpoint) - match every row's \"model\" field to exactly one "
            "of these, or leave the model out if none of these fit:",
            ", ".join(sorted(known_ids)),
        ]

    lines += [
        "",
        "Reply with nothing but a single JSON array, one object per model - "
        "no commentary, no markdown table, no code fences. Each object must "
        "have exactly these keys:",
        json.dumps(list(_REQUIRED_KEYS)),
        "",
        "One example object, showing the shape (not real values for this "
        "model):",
        json.dumps(example, indent=2),
        "",
        "Where:",
        "- provider: exactly " + repr(provider),
        "- model: the provider's exact model id, as it expects it in an "
        "API call - never a display name or a guess",
        "- kind: one of " + ", ".join(repr(k) for k in KINDS) + " - no other value",
        "- context: the context window in tokens, an integer, or null if "
        "you don't know",
        "- rpm/rph/rpd/rps: requests per minute/hour/day/second this "
        "account gets, each an integer or null",
        "- tpm/tph/tpd/tps: tokens per minute/hour/day/second this account "
        "gets, each an integer or null",
        "- gen_tps: this model's own generation speed in tokens/second "
        "(how fast it actually produces output, not a rate limit), a "
        "number or null if you don't know it",
        "- vision: true if the model accepts images, otherwise false (a "
        "JSON boolean, not a string)",
        "- free: true if this model has a free tier or is free to use, "
        "otherwise false (a JSON boolean, not a string)",
        "- score: your own 0-100 quality/capability rating as an integer, "
        "or null if you don't want to guess",
        "",
        "Use null rather than guess at a number you aren't sure of - a "
        "wrong guess is worse than admitting you don't know. Leave a rate "
        "or quota null when the provider doesn't publish that particular "
        "window (most publish only a couple of these, not all eight).",
    ]
    return "\n".join(lines)


def _strip_code_fence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[: -3]
    return s.strip()


def _extract_json_array(text: str) -> str:
    """Pulls out the `[...]` array even when the AI added a stray sentence
    before or after it, since that's the most common way a pasted answer
    fails to be pure JSON."""
    s = _strip_code_fence(text)
    start = s.find("[")
    end = s.rfind("]")
    if start != -1 and end != -1 and end > start:
        return s[start:end + 1]
    return s


def _parse_int_or_none(raw, field: str) -> tuple[Optional[int], Optional[str]]:
    """Returns (value, error). `error` is None on success. `raw` is None
    for a JSON `null`, already distinguished from a missing key by the
    caller."""
    if raw is None:
        return None, None
    if isinstance(raw, bool):
        return None, f"{field} {raw!r} is not a number or null"
    if isinstance(raw, int):
        return raw, None
    if isinstance(raw, str) and raw.strip().lower() == "none":
        return None, None
    return None, f"{field} {raw!r} is not a number or null"


def _parse_float_or_none(raw, field: str) -> tuple[Optional[float], Optional[str]]:
    """Like `_parse_int_or_none`, but for a value that's legitimately
    fractional (generation speed), not just an integer count."""
    if raw is None:
        return None, None
    if isinstance(raw, bool):
        return None, f"{field} {raw!r} is not a number or null"
    if isinstance(raw, (int, float)):
        return float(raw), None
    if isinstance(raw, str) and raw.strip().lower() == "none":
        return None, None
    return None, f"{field} {raw!r} is not a number or null"


def _parse_bool(raw, field: str) -> tuple[Optional[bool], Optional[str]]:
    if isinstance(raw, bool):
        return raw, None
    if isinstance(raw, str) and raw.strip().lower() in ("yes", "no"):
        return raw.strip().lower() == "yes", None
    return None, f"{field} {raw!r} must be true or false"


def parse_answer(text: str) -> ParseResult:
    """Tolerant, but nothing is ever silently dropped: every object that
    doesn't parse into a `ParsedRow` becomes an entry in `issues` explaining
    why not.
    """
    rows: list[ParsedRow] = []
    issues: list[ParseIssue] = []
    seen: set[str] = set()

    if not text.strip():
        return ParseResult(rows=rows, issues=issues)

    candidate = _extract_json_array(text)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        issues.append(ParseIssue(line=text.strip()[:200], reason=f"not valid JSON: {exc}"))
        return ParseResult(rows=rows, issues=issues)

    if not isinstance(parsed, list):
        issues.append(ParseIssue(line=json.dumps(parsed)[:200],
                                  reason="expected a JSON array of objects"))
        return ParseResult(rows=rows, issues=issues)

    for item in parsed:
        line = json.dumps(item)
        if not isinstance(item, dict):
            issues.append(ParseIssue(line=line, reason="expected a JSON object per model"))
            continue

        missing = [k for k in _REQUIRED_KEYS if k not in item]
        if missing:
            issues.append(ParseIssue(line=line, reason=f"missing key(s): {', '.join(missing)}"))
            continue

        provider = str(item["provider"]).strip()
        model = str(item["model"]).strip()
        if not provider or not model:
            issues.append(ParseIssue(line=line, reason="provider and model are required"))
            continue

        kind_norm = str(item["kind"]).strip().lower()
        if kind_norm not in KINDS:
            issues.append(ParseIssue(
                line=line, reason=f"unknown kind {item['kind']!r} - must be one of {', '.join(KINDS)}"))
            continue

        context, err = _parse_int_or_none(item["context"], "context")
        if err:
            issues.append(ParseIssue(line=line, reason=err))
            continue

        rate_values: dict[str, Optional[float]] = {}
        rate_err = None
        for field in _RATE_FIELDS:
            parser = _parse_float_or_none if field in _FRACTIONAL_RATE_FIELDS else _parse_int_or_none
            value, err = parser(item[field], field)
            if err:
                rate_err = err
                break
            rate_values[field] = value
        if rate_err:
            issues.append(ParseIssue(line=line, reason=rate_err))
            continue

        gen_tps, err = _parse_float_or_none(item.get(_SPEED_FIELD), _SPEED_FIELD)
        if err:
            issues.append(ParseIssue(line=line, reason=err))
            continue
        if gen_tps is not None and gen_tps < 0:
            issues.append(ParseIssue(line=line, reason=f"{_SPEED_FIELD} cannot be negative"))
            continue

        vision, err = _parse_bool(item["vision"], "vision")
        if err:
            issues.append(ParseIssue(line=line, reason=err))
            continue

        free, err = _parse_bool(item["free"], "free")
        if err:
            issues.append(ParseIssue(line=line, reason=err))
            continue

        score, err = _parse_int_or_none(item["score"], "score")
        if err:
            issues.append(ParseIssue(line=line, reason=err))
            continue
        if score is not None and not (0 <= score <= 100):
            issues.append(ParseIssue(line=line, reason=f"score {score} is out of range 0-100"))
            continue

        ident = f"{provider}/{model}"
        if ident in seen:
            issues.append(ParseIssue(line=line, reason=f"duplicate of an earlier entry for {ident}"))
            continue
        seen.add(ident)

        rows.append(ParsedRow(
            provider=provider, model=model, kind=kind_norm, context=context,
            gen_tps=gen_tps, vision=vision, free=free, score=score, raw_line=line,
            **rate_values,
        ))

    return ParseResult(rows=rows, issues=issues)
