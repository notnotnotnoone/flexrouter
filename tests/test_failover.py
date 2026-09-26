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


@pytest.mark.parametrize("verdict, expected", [
    ("bad_request", Verdict.RETURN),
    ("too_fast", Verdict.NEXT),
    ("bad_key", Verdict.NEXT),
    ("needs_payment", Verdict.NEXT),
    ("model_gone", Verdict.NEXT),
    ("their_end_temporary", Verdict.NEXT),
    ("unknown", Verdict.NEXT),
])
def test_a_bare_400_is_decided_by_the_error_brains_verdict(verdict, expected):
    """grill-decisions.md §4: overturns ADR 0013's "bare 400 = bad_request
    at 1.0" - only a confident bad_request is genuinely the caller's fault."""
    assert decide_failover(400, verdict=verdict) == expected


def test_a_bare_400_with_no_verdict_at_all_still_returns():
    """A caller that never asked the error brain (verdict=None) keeps the
    old flat behavior - this never actually happens in the router, where
    _classify() always returns a concrete verdict, even under NullDecider."""
    assert decide_failover(400) == Verdict.RETURN
