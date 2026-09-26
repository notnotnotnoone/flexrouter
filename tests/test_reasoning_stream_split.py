from flexrouter.reasoning import ReasoningStreamSplitter


def test_plain_text_passes_through_immediately():
    s = ReasoningStreamSplitter()
    content, reasoning = s.feed("hello there")
    assert content == "hello there"
    assert reasoning == ""


def test_a_tag_that_arrives_whole_in_one_chunk():
    s = ReasoningStreamSplitter()
    content, reasoning = s.feed("<think>thinking</think>answer")
    assert content == "answer"
    assert reasoning == "thinking"


def test_the_opening_tag_split_across_chunks_is_still_recognised():
    s = ReasoningStreamSplitter()
    c1, r1 = s.feed("<thi")
    c2, r2 = s.feed("nk>thinking")
    assert (c1, r1) == ("", "")
    assert (c2, r2) == ("", "thinking")


def test_the_closing_tag_split_across_chunks_is_still_recognised():
    s = ReasoningStreamSplitter()
    s.feed("<think>thinking")
    c1, r1 = s.feed("</th")
    c2, r2 = s.feed("ink>answer")
    assert (c1, r1) == ("", "")
    assert (c2, r2) == ("answer", "")


def test_flush_at_end_of_stream_returns_unclosed_reasoning():
    s = ReasoningStreamSplitter()
    _, r1 = s.feed("<think>never finished thinking")
    content, r2 = s.flush()
    assert content == ""
    assert r1 + r2 == "never finished thinking"


def test_flush_returns_leftover_plain_text_that_never_became_a_tag():
    s = ReasoningStreamSplitter()
    s.feed("weird ending <th")
    content, reasoning = s.flush()
    assert content == "<th"
    assert reasoning == ""


def test_reasoning_split_across_many_small_chunks():
    s = ReasoningStreamSplitter()
    out_content, out_reasoning = "", ""
    for ch in "<think>step by step</think>4":
        c, r = s.feed(ch)
        out_content += c
        out_reasoning += r
    assert out_content == "4"
    assert out_reasoning == "step by step"
