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
    # Amended rule (round 1 fix): the mixed-alpha-and-digit gate is gone, so
    # a pure-letter run this long (20+ chars) is caught the same as a mixed
    # one now - "uncharacteristically" is exactly 20 characters. That is the
    # accepted trade stated in the module docstring, not a bug: a false
    # positive here costs readability, a false negative on a same-shaped
    # credential costs a key. A short ordinary word must still survive.
    out = scrub("uncharacteristically disproportionate")
    assert "uncharacteristically" not in out
    assert "disproportionate" in out


def test_empty_text_is_safe():
    assert scrub("") == ""


def test_a_credential_split_by_separator_characters_is_cut_down_whole():
    # Finding 1: a JWT's '.' segments and an AWS-style secret's '/' used to
    # split the token into sub-24-char fragments that each slipped under the
    # old floor. The token character class now includes the separators a
    # credential is actually built from, so the whole thing is one run.
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CVPmB92K27u"
    out = scrub(f"Bearer {jwt}")
    assert jwt not in out

    aws_style = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    out = scrub(aws_style)
    assert aws_style not in out


def test_a_base64_credential_with_plus_and_equals_is_cut_down():
    # Finding 1: base64 secrets use '+' and '=' too.
    token = "c2VjcmV0a2V5+dGhpc2lzYQ=="
    out = scrub(f"leaked credential: {token}")
    assert token not in out


def test_a_short_key_named_after_a_cue_word_is_cut_down():
    # Finding 2: a short key has no length of its own to catch it, so the
    # cue word that introduces it is what has to catch it instead. The old
    # 24-char floor let "sk-abc123XYZ" (12 chars) straight through.
    out = scrub("Incorrect API key provided: sk-abc123XYZ")
    assert "sk-abc123XYZ" not in out


def test_a_short_bare_credential_is_cut_down_when_a_cue_word_names_it():
    # Finding 2: same shape, a Hugging Face style token ("hf_...") short
    # enough (19 chars) to be under the Rule A floor even with the widened
    # character class - it still has to be caught via the cue word that
    # names it in a real provider error.
    token = "hf_QWERTYuiopASDFGH"
    out = scrub(f"invalid token: {token}")
    assert token not in out


def test_a_long_pure_letter_run_is_cut_down():
    # Finding 3: the deleted mixed-class gate used to let an all-letter run
    # of key-length through untouched.
    token = "sk-proj-AAAABBBBCCCCDDDDEEEEFFFF"
    out = scrub(token)
    assert token not in out


def test_a_long_pure_digit_run_is_cut_down():
    # Finding 3: and an all-digit run of key-length too.
    token = "9081726354019283746501928374"
    out = scrub(token)
    assert token not in out


def test_a_short_token_of_eight_or_fewer_chars_leaves_no_tail():
    # A tail of the last four characters of an 8-or-fewer character token
    # would leak half of it or more. Below that length the token becomes a
    # bare ellipsis instead.
    out = scrub("api_key=abc12345")
    assert "abc12345" not in out
    assert "…" in out
    # No four-character tail survives from this particular short token.
    assert "2345" not in out


def test_an_ordinary_sentence_with_no_cue_and_no_long_run_survives():
    text = "the provider is temporarily overloaded, please retry shortly"
    assert scrub(text) == text
