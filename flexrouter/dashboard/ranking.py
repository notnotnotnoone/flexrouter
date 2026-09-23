"""Stage 8 (PLAN-V2.md) - ranking models with an AI, the copy-paste way.

Builds a ready-made prompt from the current model list plus whatever
benchmark material the owner supplies, and parses the answer pasted back
into a set of proposed scores. No network call happens here - sending the
prompt to a model directly is the other of the two ways the plan describes
running this ("the service sends it itself"), and is not built yet, so the
only path wired up is: build prompt, owner runs it wherever they like,
paste the answer back.

Nothing here writes anything. `flexrouter/dashboard/pages.py` is what
turns a parsed proposal into an actual override, after the owner has
picked which rows to apply.
"""
from __future__ import annotations


def build_prompt(rows, notes: str) -> str:
    """`rows` is `facts.models(router)` output - every model configured in
    any bucket, once each."""
    lines = [
        "You are ranking LLM models on a 0-100 overall quality/capability "
        "scale for a personal router that automatically picks the best "
        "available model for each request.",
        "Higher is better. Keep scores comparable across providers, since "
        "they compete against each other directly.",
        "",
        "Current models and their existing scores:",
    ]
    for r in rows:
        caps = [
            name for name, fact in (
                ("vision", r.vision), ("tools", r.tools), ("reasoning", r.reasoning),
            ) if fact is not None and fact.status == "yes"
        ]
        cap_note = f", capabilities: {', '.join(caps)}" if caps else ""
        context = r.learned_context or r.context_window
        lines.append(
            f"- {r.provider} | {r.model} | current score {r.score} | "
            f"context {context}{cap_note}"
        )

    if notes.strip():
        lines += ["", "Benchmark material / notes supplied by the owner:", notes.strip()]

    lines += [
        "",
        "Reply with exactly one line per model above, in this form and "
        "nothing else - no headers, no commentary, no extra lines:",
        "provider | model | proposed_score",
        "Use the exact same provider and model text shown above for each "
        "line, so the answer can be matched back up automatically.",
    ]
    return "\n".join(lines)


def build_rate_limit_prompt(idents: list[str], docs_text: str) -> str:
    """Same shape as `build_prompt`, for the rate-limits page: a ready-made
    prompt built from the current model list plus whatever docs text the
    owner pasted in, meant to be copied into whichever model they actually
    use - not sent anywhere by this service."""
    lines = [
        "Read the rate-limit documentation pasted below and report the "
        "requests-per-minute (rpm) and tokens-per-minute (tpm) limit for "
        "each of these models:",
        "",
    ]
    lines += [f"- {ident}" for ident in idents]
    lines += [
        "",
        "Documentation:",
        docs_text.strip(),
        "",
        "Reply with exactly one line per model you found a number for, in "
        "this form and nothing else - no headers, no commentary, no extra "
        "lines:",
        "provider | model | rpm | tpm",
        "Use the exact same provider and model text shown above for each "
        "line, so the answer can be matched back up automatically. Write "
        "none for whichever of rpm/tpm the docs don't give for that model, "
        "and leave a model out entirely if the docs don't mention it at "
        "all - don't guess.",
    ]
    return "\n".join(lines)


def parse_rate_limit_answer(text: str, known_idents: set) -> dict:
    """Parse a pasted-back answer into `{"provider/model": (rpm, tpm)}`.

    Same tolerance rules as `parse_answer`: a line that doesn't fit, names a
    model this router doesn't have, or gives `none` for both numbers is
    dropped rather than raising.
    """
    proposed = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 4:
            continue
        provider, model, raw_rpm, raw_tpm = parts
        if not provider or not model:
            continue
        ident = f"{provider}/{model}"
        if ident not in known_idents:
            continue
        rpm = int(raw_rpm) if raw_rpm.isdigit() else None
        tpm = int(raw_tpm) if raw_tpm.isdigit() else None
        if rpm is None and tpm is None:
            continue
        proposed[ident] = (rpm, tpm)
    return proposed


def parse_answer(text: str, known_idents: set) -> dict:
    """Parse a pasted-back answer into `{"provider/model": score}`.

    Tolerant by design: a line that isn't `provider | model | score`, names
    a model this router doesn't actually have configured, or gives a
    non-integer score is silently dropped rather than raising - the owner
    is pasting free-form model output, not a machine-generated file, and a
    stray blank line or a model the AI mentioned in passing must not blow
    up the whole paste.
    """
    proposed = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3:
            continue
        provider, model, raw_score = parts
        if not provider or not model:
            continue
        cleaned = raw_score.lstrip("-")
        if not cleaned.isdigit():
            continue
        ident = f"{provider}/{model}"
        if ident not in known_idents:
            continue
        proposed[ident] = int(raw_score)
    return proposed
