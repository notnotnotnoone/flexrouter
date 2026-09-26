from __future__ import annotations
from enum import Enum


class Verdict(str, Enum):
    """What the failover policy says to do next."""

    NEXT = "next"
    NEXT_BIGGER_CONTEXT = "next_bigger_context"
    RETURN = "return"


def decide_failover(
    status_code: int | None,
    *,
    message_too_long: bool = False,
    empty_reply: bool = False,
) -> Verdict:
    """grill-decisions.md §2, as one pure, table-driven function.

    | The model says           | Router does                            |
    |---------------------------|-----------------------------------------|
    | 429 / 503 (busy)          | next model instantly                    |
    | 404 / 402 / 403 (broken)  | next model instantly                    |
    | message too long          | next model, with a bigger context window|
    | genuinely bad request     | return to the caller immediately        |
    | empty reply               | next model instantly                    |

    `message_too_long` wins over the status code: a provider can say
    "message too long" with a 400, and that specific reason takes the
    bigger-context path rather than the flat "return" a plain 400 gets.
    Anything not named in the table (an unlisted 5xx, or a status-less
    failure like a dropped connection) is treated as the model's problem,
    not the caller's, so it falls to NEXT.
    """
    if message_too_long:
        return Verdict.NEXT_BIGGER_CONTEXT
    if empty_reply:
        return Verdict.NEXT
    if status_code in (429, 503, 404, 402, 403):
        return Verdict.NEXT
    if status_code is not None and 400 <= status_code < 500:
        return Verdict.RETURN
    return Verdict.NEXT


def caller_budget_detail(
    finish_reason: str | None, caller_max_tokens: int | None,
) -> str | None:
    """grill-decisions.md §13: an empty reply with `finish_reason=length`
    and a caller-set `max_tokens` is the caller's budget too small for the
    model to finish reasoning, not a model failure - no penalty, and it
    still fails over to the next model in a bucket."""
    if finish_reason != "length" or caller_max_tokens is None:
        return None
    return f"the model used all {caller_max_tokens} tokens thinking, raise max_tokens"
