""""Add models with AI" - the copy-paste way, same shape as ranking.py.

Builds a ready-made prompt naming one provider and its already-configured
models, and parses the answer pasted back into a set of proposed model
rows - one per line, `provider | model | kind | context | rpm | tpm |
vision | free | score`. No network call happens here, same as ranking.py:
the owner sends the prompt themselves, wherever they like, and pastes the
answer back.

Nothing here writes anything. `flexrouter/dashboard/pages.py` is what turns
a parsed row into an override (existing chat models), a brand-new bucket
entry (new chat models), or a parked-models.json entry (anything that
isn't `chat`), after the owner has reviewed and checked which rows to
apply.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

KINDS = ("chat", "embedding", "speech_to_text", "text_to_speech", "image")

_COLUMNS = ("provider", "model", "kind", "context", "rpm", "tpm", "vision", "free", "score")
_HEADER_NORMALIZED = "".join(_COLUMNS)
_INT_RE = re.compile(r"-?\d+")


@dataclass
class ParsedRow:
    provider: str
    model: str
    kind: str
    context: Optional[int]
    rpm: Optional[int]
    tpm: Optional[int]
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


def build_prompt(provider: str, existing_models: list[str], notes: str) -> str:
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

    lines += [
        "",
        "Reply with exactly one line per model, in this form and nothing "
        "else - no headers, no commentary, no markdown table, no code "
        "fences:",
        "provider | model | kind | context | rpm | tpm | vision | free | score",
        "",
        "Where:",
        "- provider: exactly " + repr(provider),
        "- model: the provider's exact model id, as it expects it in an "
        "API call - never a display name or a guess",
        "- kind: one of chat, embedding, speech_to_text, text_to_speech, image",
        "- context: the context window in tokens, an integer, or the word "
        "none if you don't know",
        "- rpm: requests per minute this account gets, an integer, or none",
        "- tpm: tokens per minute this account gets, an integer, or none",
        "- vision: yes if the model accepts images, otherwise no",
        "- free: yes if this model has a free tier or is free to use, "
        "otherwise no",
        "- score: your own 0-100 quality/capability rating, or none if you "
        "don't want to guess",
        "",
        "Write none rather than guess at a number you aren't sure of - a "
        "wrong guess is worse than admitting you don't know.",
    ]
    return "\n".join(lines)


def _strip_table_pipes(line: str) -> str:
    """Drop a leading/trailing `|` (a markdown table row) and surrounding
    backticks (inline code formatting), without touching the pipes that
    separate real columns."""
    s = line.strip().strip("`").strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return s.strip()


def _is_noise_line(line: str) -> bool:
    """A header row, a markdown table separator (`---|---|---`), or a code
    fence marker (```) - all expected to show up in a pasted answer, and
    none of them a real data row worth reporting as unparseable."""
    s = line.strip()
    if not s:
        return True
    if s.startswith("```"):
        return True
    normalized = "".join(ch for ch in s.lower() if ch.isalnum())
    if normalized == _HEADER_NORMALIZED:
        return True
    if all(ch in "-:|" or ch.isspace() for ch in s):
        return True
    return False


def _parse_int_or_none(raw: str) -> tuple[Optional[int], bool]:
    """Returns (value, ok). `ok` is False when `raw` is neither "none" nor
    a plain integer."""
    if raw.lower() == "none":
        return None, True
    if not _INT_RE.fullmatch(raw):
        return None, False
    return int(raw), True


def _parse_yes_no(raw: str) -> tuple[Optional[bool], bool]:
    low = raw.lower()
    if low == "yes":
        return True, True
    if low == "no":
        return False, True
    return None, False


def parse_answer(text: str) -> ParseResult:
    """Tolerant, but nothing is ever silently dropped: every line that
    isn't blank/header/fence noise either becomes a `ParsedRow` or an entry
    in `issues` explaining why not.
    """
    rows: list[ParsedRow] = []
    issues: list[ParseIssue] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        if _is_noise_line(raw_line):
            continue
        line = raw_line.strip()

        stripped = _strip_table_pipes(line)
        parts = [p.strip().strip("`").strip() for p in stripped.split("|")]
        if len(parts) != len(_COLUMNS):
            issues.append(ParseIssue(
                line=line,
                reason=f"expected {len(_COLUMNS)} columns "
                       f"({' | '.join(_COLUMNS)}), got {len(parts)}"))
            continue

        provider, model, kind, context_raw, rpm_raw, tpm_raw, vision_raw, free_raw, score_raw = parts

        if not provider or not model:
            issues.append(ParseIssue(line=line, reason="provider and model are required"))
            continue

        kind_norm = kind.lower()
        if kind_norm not in KINDS:
            issues.append(ParseIssue(
                line=line,
                reason=f"unknown kind {kind!r} - must be one of {', '.join(KINDS)}"))
            continue

        context, ok = _parse_int_or_none(context_raw)
        if not ok:
            issues.append(ParseIssue(line=line, reason=f"context {context_raw!r} is not a number or none"))
            continue

        rpm, ok = _parse_int_or_none(rpm_raw)
        if not ok:
            issues.append(ParseIssue(line=line, reason=f"rpm {rpm_raw!r} is not a number or none"))
            continue

        tpm, ok = _parse_int_or_none(tpm_raw)
        if not ok:
            issues.append(ParseIssue(line=line, reason=f"tpm {tpm_raw!r} is not a number or none"))
            continue

        vision, ok = _parse_yes_no(vision_raw)
        if not ok:
            issues.append(ParseIssue(line=line, reason=f"vision {vision_raw!r} must be yes or no"))
            continue

        free, ok = _parse_yes_no(free_raw)
        if not ok:
            issues.append(ParseIssue(line=line, reason=f"free {free_raw!r} must be yes or no"))
            continue

        score, ok = _parse_int_or_none(score_raw)
        if not ok:
            issues.append(ParseIssue(line=line, reason=f"score {score_raw!r} is not a number or none"))
            continue
        if score is not None and not (0 <= score <= 100):
            issues.append(ParseIssue(line=line, reason=f"score {score} is out of range 0-100"))
            continue

        ident = f"{provider}/{model}"
        if ident in seen:
            issues.append(ParseIssue(line=line, reason=f"duplicate of an earlier line for {ident}"))
            continue
        seen.add(ident)

        rows.append(ParsedRow(
            provider=provider, model=model, kind=kind_norm, context=context,
            rpm=rpm, tpm=tpm, vision=vision, free=free, score=score, raw_line=line,
        ))

    return ParseResult(rows=rows, issues=issues)
