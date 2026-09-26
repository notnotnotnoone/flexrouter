from flexrouter.reasoning import split_reasoning


def test_no_tags_passes_content_through_unchanged():
    content, reasoning = split_reasoning("just a normal reply")
    assert content == "just a normal reply"
    assert reasoning == ""


def test_a_closed_think_tag_is_pulled_out_of_content():
    content, reasoning = split_reasoning("<think>let me consider this</think>the answer is 4")
    assert content == "the answer is 4"
    assert reasoning == "let me consider this"


def test_thought_tag_variant_is_also_split():
    content, reasoning = split_reasoning("<thought>hmm</thought>ok")
    assert content == "ok"
    assert reasoning == "hmm"


def test_an_unclosed_tag_from_truncation_is_still_reasoning():
    content, reasoning = split_reasoning("<think>still thinking about this")
    assert content == ""
    assert reasoning == "still thinking about this"


def test_reasoning_only_leaves_empty_content():
    content, reasoning = split_reasoning("<think>just thinking</think>")
    assert content == ""
    assert reasoning == "just thinking"
