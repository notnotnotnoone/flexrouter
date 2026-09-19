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
    # a pure-letter run this long is caught the same as a mixed one now -
    # "uncharacteristically" is 20 characters, well past even the round-2
    # 16-character floor. That is the accepted trade stated in the module
    # docstring, not a bug: a false positive here costs readability, a
    # false negative on a same-shaped credential costs a key. A short
    # ordinary word (under the 16-character floor) must still survive.
    out = scrub("uncharacteristically legitimate")
    assert "uncharacteristically" not in out
    assert "legitimate" in out


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
    # enough (19 chars) that round 1's 20-char floor still missed it -
    # caught here via the cue word that names it in a real provider error.
    token = "hf_QWERTYuiopASDFGH"
    out = scrub(f"invalid token: {token}")
    assert token not in out


def test_a_bare_short_credential_survives_no_longer():
    # Round 2, gap 1: this exact bare call - no surrounding cue word at all
    # - was the literal review reproduction that round 1's 20-char floor
    # still let through in full (19 characters, one short). The floor
    # dropped to 16 specifically to close this without leaning on a cue
    # word being present. Kept alongside the cue-context test above, per
    # the controller ruling: both are worth having.
    token = "hf_QWERTYuiopASDFGH"
    out = scrub(token)
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
    # bare ellipsis instead. (Note: the cue and value are separated by
    # "api_key: " rather than "api_key=" so the whole cue+value span isn't
    # itself one contiguous 16+ character run that Rule A would catch as a
    # single unit before Rule B ever gets a look at just the value part.)
    out = scrub("api_key: abc12345")
    assert "abc12345" not in out
    assert "…" in out
    # No four-character tail survives from this particular short token.
    assert "2345" not in out


def test_api_key_equals_short_token_is_also_cut_down():
    # Round 3: restored alongside the ": " variant above. Here "api_key="
    # plus the 8-character value forms one contiguous 16-character run of
    # Rule A's own token class, so Rule A legitimately claims the whole
    # thing before Rule B ever runs - a tail is fine here (Rule A's floor
    # is being hit honestly), the only requirement is the credential itself
    # doesn't survive.
    out = scrub("api_key=abc12345")
    assert "abc12345" not in out


def test_an_ordinary_sentence_with_no_cue_and_no_long_run_survives():
    text = "the provider is temporarily overloaded, please retry shortly"
    assert scrub(text) == text


def test_two_cue_introduced_credentials_in_one_message_are_both_cut_down():
    # Round 2, gap 2: "only the last is scrubbed" was a hole, not a
    # cosmetic issue - a provider error naming two rejected credentials
    # would leak the first whole. Literal review reproduction.
    first = "sk-AAAABBBBCCCC1111"
    second = "gsk_DDDDEEEEFFFF2222"
    out = scrub(f"key {first} and token {second} both rejected")
    assert first not in out
    assert second not in out


def test_two_short_cue_introduced_credentials_are_both_cut_down():
    # Same shape as above, but both credentials are short enough (under the
    # 16-character Rule A floor) that this can only pass if Rule B itself -
    # not Rule A's length floor - catches both. Each cue word gets its own
    # bounded search so the first cue's match can't run past the second
    # cue and swallow it as filler.
    first = "sk-AAA111"
    second = "gsk_BBB222"
    out = scrub(f"key {first} and token {second} both rejected")
    assert first not in out
    assert second not in out


def test_a_cue_introduced_credential_and_a_separate_bare_run_are_both_cut_down():
    # A cue-caught short credential (Rule B) and an unrelated long bare run
    # (Rule A) in the same message - both mechanisms have to fire
    # independently without one interfering with the other.
    cued = "sk-AAA111"
    bare = "zzzzzzzzzzzzzzzzzzzzzzzz9999"
    out = scrub(f"key {cued} rejected; also saw {bare} in the log")
    assert cued not in out
    assert bare not in out


# Round 3: `_pick_value` (the "which candidate is the real one" heuristic
# added in round 2) was itself the hole - every one of the five messages
# below leaked its credential in full under that heuristic, each for a
# different reason (a later colon outranking the real value, punctuation or
# a newline between the cue word and the value breaking the old adjacency
# requirement). Rule B no longer picks a candidate at all: it scrubs every
# qualifying run in a 48-character window after each cue word.

def test_a_later_colon_no_longer_lets_the_real_value_leak():
    out = scrub("token gsk_BBB222 rejected by upstream: retrying")
    assert "gsk_BBB222" not in out


def test_a_credential_followed_by_prose_no_longer_leaks():
    out = scrub(
        "The API key sk-live-a1 was rejected: please check your billing settings"
    )
    assert "sk-live-a1" not in out


def test_a_semicolon_between_cue_and_value_no_longer_hides_the_value():
    out = scrub("Authorization failed for key sk-Ab12Cd; reason: expired")
    assert "sk-Ab12Cd" not in out


def test_a_newline_between_cue_and_value_no_longer_hides_the_value():
    out = scrub("key\nsk-AAA111 rejected")
    assert "sk-AAA111" not in out


def test_parentheses_around_the_value_no_longer_hide_it():
    out = scrub("key (sk-AAA111) rejected")
    assert "sk-AAA111" not in out


def test_ordinary_lowercase_words_near_a_cue_word_stay_readable():
    # This is exactly what the lowercase exemption exists for: the
    # credential in this message must go, but the plain lowercase prose
    # around it - the only thing standing between this rule and an error
    # message that's just a wall of ellipses - must not.
    out = scrub("token gsk_BBB222 rejected by upstream: retrying")
    assert "gsk_BBB222" not in out
    assert "rejected" in out
    assert "upstream" in out
    assert "retrying" in out
