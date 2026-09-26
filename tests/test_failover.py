import pytest

from flexrouter.failover import Verdict, decide_failover


@pytest.mark.parametrize("status_code, kwargs, expected", [
    (429, {}, Verdict.NEXT),
    (503, {}, Verdict.NEXT),
    (404, {}, Verdict.NEXT),
    (402, {}, Verdict.NEXT),
    (403, {}, Verdict.NEXT),
    (None, {"message_too_long": True}, Verdict.NEXT_BIGGER_CONTEXT),
    (400, {"message_too_long": True}, Verdict.NEXT_BIGGER_CONTEXT),
    (400, {}, Verdict.RETURN),
    (422, {}, Verdict.RETURN),
    (None, {"empty_reply": True}, Verdict.NEXT),
    (None, {}, Verdict.NEXT),
    (500, {}, Verdict.NEXT),
])
def test_the_policy_table(status_code, kwargs, expected):
    assert decide_failover(status_code, **kwargs) == expected


def test_message_too_long_wins_over_a_bad_request_status():
    # A provider can answer 400 for "message too long" - that specific
    # reason takes the bigger-context path, not the flat "return" a plain
    # 400 gets.
    assert decide_failover(400, message_too_long=True) == Verdict.NEXT_BIGGER_CONTEXT
