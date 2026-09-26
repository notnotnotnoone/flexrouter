from flexrouter.failover import caller_budget_detail


def test_no_message_when_finish_reason_is_not_length():
    assert caller_budget_detail(finish_reason="stop", caller_max_tokens=5) is None


def test_no_message_when_the_caller_never_set_max_tokens():
    assert caller_budget_detail(finish_reason="length", caller_max_tokens=None) is None


def test_names_the_callers_own_max_tokens_value():
    detail = caller_budget_detail(finish_reason="length", caller_max_tokens=5)
    assert detail == "the model used all 5 tokens thinking, raise max_tokens"
