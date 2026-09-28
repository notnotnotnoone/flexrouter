"""'Explain errors with AI': a copy-paste prompt for discussing problems with
a chatbot (grill-decisions.md §8).

Same family as 'Add models with AI' and 'Rank models with AI', except there
is no paste-back: it's a conversation, and fixes are made with the normal
buttons. So the prompt ends with the buttons the page actually has, and the
chatbot can answer in terms of clicks.

Every problem carries what a chatbot needs to be useful: the plain status,
how often and when, the provider's full reply (only keys flexrouter holds
are masked), the model's settings, its last few requests, and the
provider's real model IDs that look like it. Nothing here writes.
"""
from __future__ import annotations

import time

from flexrouter import catalogue
from flexrouter.dashboard import facts
from flexrouter.redact import mask_known_secrets

PREAMBLE = (
    "I use flexrouter, a tool on my computer that sends my AI requests to free\n"
    "models (Google, Groq, Mistral…). If one model fails, it tries the next one.\n"
    "Please help me understand these errors and tell me which button to click\n"
    "to fix each one."
)

_STATUS_WORD = {"needs_you": "Needs you", "struggling": "Struggling", "busy": "Busy",
                "off": "Off", "ready": "Ready"}

# What the page lets the owner do, in the words its buttons use.
BUTTONS = ("[Use another ID]", "[Remove]", "[Retry]", "[Try now]", "[Turn off]",
           "[Turn on]", "[Replace key]", "[Move to bucket]")

RECENT = 3
WINDOW_SECONDS = 24 * 3600


def _parse_at(at: str) -> float | None:
    from flexrouter.dashboard.overview import parse_utc
    dt = parse_utc(at or "")
    return dt.timestamp() if dt else None


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s ago"
    if seconds < 90 * 60:
        return f"{seconds // 60}m ago"
    if seconds < 36 * 3600:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _history(traces: list[dict], provider: str, model: str, now: float):
    """Failures of this model in the last day, when the last one was, and
    its last few requests (newest first), all read from traces."""
    failures, last_fail, recent = 0, None, []
    for entry in reversed(traces):
        at = _parse_at(entry.get("at", ""))
        mine = [a for a in entry.get("attempts") or []
                if a.get("provider") == provider and a.get("model") == model]
        answered = entry.get("answered_by") or {}
        won = answered.get("provider") == provider and answered.get("model") == model
        if not mine and not won:
            continue
        if at is not None and now - at <= WINDOW_SECONDS:
            failures += len(mine)
        if mine and last_fail is None and at is not None:
            last_fail = at
        if len(recent) < RECENT:
            when = _ago(now - at) if at is not None else "?"
            if mine:
                a = mine[-1]
                recent.append(f"{when}: failed, HTTP {a.get('status') or 'none'}"
                              f" ({(a.get('verdict') or 'unclear').replace('_', ' ')})")
            else:
                recent.append(f"{when}: answered")
    return failures, last_fail, recent


def _settings(router, provider: str, model: str) -> str:
    buckets, mc = [], None
    for bucket, models in router._cfg.tiers.items():
        for m in models:
            if m.provider == provider and m.model == model:
                buckets.append(bucket)
                mc = mc or m
    if mc is None:
        return "not in any bucket"
    limits = [f"{mc.rpm} requests/min" if mc.rpm else "requests/min unknown"]
    for k, v in (mc.quotas or {}).items():
        limits.append(f"{v} {k}")
    where = ", ".join(f'"{b}"' for b in buckets)
    return f"bucket {where}, " + ", ".join(limits)


def _problem(router, item, n: int, total: int, traces: list[dict], now: float) -> list[str]:
    provider, model = item.provider, item.model
    who = f"{provider}/{model}" if model else (
        f"{provider} key {item.detail}" if item.kind.startswith("key") else provider)
    status = _STATUS_WORD.get(item.status, item.status or "Problem")
    out = [f"━━ Problem {n} of {total} ━━  {who}   {status}",
           f"What happened:  {item.reason or 'no reason recorded'}"]
    if model:
        count, last, recent = _history(traces, provider, model, now)
        if count or last:
            last_word = f", last one {_ago(now - last)}" if last else ""
            out.append(f"How often:      {count} failed attempt{'s' if count != 1 else ''} "
                       f"in the last 24h{last_word}")
    elif item.since:
        out.append(f"Since:          {item.since}")
    if item.provider_text:
        out.append(f"{provider}'s exact reply:")
        out += ["    " + line for line in mask_known_secrets(item.provider_text).splitlines()]
    if model:
        out.append(f"My settings for it:  {_settings(router, provider, model)}")
        if recent:
            out.append("Its last few requests:")
            out += ["    " + r for r in recent]
        similar = [s for s in catalogue.did_you_mean(provider, model, router._cfg.state_dir,
                                                     limit=5) if s != model]
        if similar:
            out.append(f"Models {provider} actually has that look similar:")
            out.append("    " + ", ".join(similar))
    return out


def row_key(item) -> str:
    return f"{item.provider}/{item.model}" if item.model else item.provider


def problems(router, now: float | None = None) -> list:
    """Everything red or unsure: the Needs-you pile, then Struggling ones.
    Busy is left out; it clears by itself and isn't worth a conversation."""
    data = facts.broken(router, now)
    return data["needs_you"] + [i for i in data["handling_itself"]
                                if i.status == "struggling"]


def build_prompt(router, only: str = "", now: float | None = None,
                 traces: list[dict] | None = None) -> str:
    """The whole prompt. `only` narrows it to one row, "provider/model" (or
    a provider name for a provider-wide problem). Empty when nothing's wrong."""
    now = now if now is not None else time.time()
    items = problems(router, now)
    if only:
        items = [i for i in items if row_key(i) == only]
    if not items:
        return ""
    if traces is None:
        traces = facts._read_traces(router, 5000)
    lines = [PREAMBLE, ""]
    for n, item in enumerate(items, 1):
        lines += _problem(router, item, n, len(items), traces, now)
        lines.append("")
    lines.append("Buttons I can click in flexrouter:")
    lines.append("    " + "  ".join(BUTTONS))
    return "\n".join(lines)
