from flexrouter.redact import scrub


def test_an_openai_style_key_is_cut_down_to_its_tail():
    out = scrub("Incorrect API key provided: sk-proj-AAAABBBBCCCCDDDDEEEE1234")
    assert "sk-proj-AAAABBBBCCCCDDDDEEEE1234" not in out
    assert "…1234" in out


def test_a_bare_long_token_is_cut_down_too():
    out = scrub("bad credential gsk0123456789abcdefghijklmnopqrstuvwxyzAB")
    assert "gsk0123456789abcdefghijklmnopqrstuvwxyzAB" not in out
    assert "…zyAB" in out or "…yzAB" in out


def test_a_bearer_header_echoed_back_is_cut_down():
    out = scrub("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456")
    assert "abcdefghijklmnopqrstuvwxyz123456" not in out


def test_ordinary_words_survive_untouched():
    text = "rate limit exceeded, retry in 20s"
    assert scrub(text) == text


def test_a_long_plain_word_is_not_mistaken_for_a_key():
    assert scrub("uncharacteristically disproportionate") == \
        "uncharacteristically disproportionate"


def test_empty_text_is_safe():
    assert scrub("") == ""
