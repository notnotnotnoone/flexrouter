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
    verdict: str | None = None,
) -> Verdict:
    """grill-decisions.md §2 and §4, as one pure, table-driven function.

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

    A bare 400 has no status-code answer of its own (unlike 404/402/403,
    which are unambiguous) - §4 overturns ADR 0013's "bare 400 =
    bad_request at 1.0" and hands it to the error brain's verdict instead.
    Only a confident `bad_request` is genuinely the caller's fault; every
    other verdict (including `unknown`, when JEV is unsure or down) is
    treated as the model's problem and fails over. An unlisted 4xx (422,
    413, ...) keeps the old flat "return" - out of this session's scope.
    """
    if message_too_long:
        return Verdict.NEXT_BIGGER_CONTEXT
    if empty_reply:
        return Verdict.NEXT
    if status_code in (429, 503, 404, 402, 403):
        return Verdict.NEXT
    if status_code == 400:
        # No verdict at all (a caller that never asked) keeps the old flat
        # "return", same as an explicit bad_request - only a verdict that
        # actually names something else sends it to the next model.
        return Verdict.RETURN if verdict in (None, "bad_request") else Verdict.NEXT
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
