import pytest

from flexrouter import redact
from flexrouter.redact import scrub


@pytest.fixture(autouse=True)
def _heuristic_redaction_on():
    """This whole file tests Rule A/Rule B, the heuristic "this looks like
    a credential" scrubbing - which is opt-in (redact_errors, default off)
    since it has no way to tell a provider/model identifier apart from an
    actual secret and used to mangle names it hadn't already been told
    about. Exact-known-secret replacement (scrub_body's `_secrets` loop) is
    unaffected by this flag and is not what these tests are about."""
    redact.set_enabled(True)
    yield
    redact.set_enabled(False)


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
    #
    # Round 4 put ':' back in the run class (round 3 had dropped it, which
    # let "ab12:cd34:ef56" survive whole) and "upstream:" stopped being
    # exempt as a result. Round 5's edge trim gives the word back without
    # giving up the class: a run's leading and trailing ": = . , ;" are
    # trimmed off before the exemption is checked, so "upstream:" is judged
    # as "upstream".
    out = scrub("token gsk_BBB222 rejected by upstream: retrying")
    assert "gsk_BBB222" not in out
    assert "rejected" in out
    assert "upstream" in out
    assert "retrying" in out


# Round 4: three narrow holes left by round 3's window scan.

def test_a_short_colon_bearing_credential_is_cut_down():
    # Finding 1: round 3 dropped ':' from the run class inside a cue
    # window, so a short colon-separated credential was split into
    # sub-6-character pieces and survived whole. The window scan uses the
    # full ruled token class again.
    out = scrub("token ab12:cd34:ef56")
    assert "ab12:cd34:ef56" not in out


def test_a_run_straddling_the_window_edge_is_scrubbed_whole():
    # Finding 2: the 48-character window cut a run in half, so only the
    # part inside the window was scrubbed and the rest leaked. A run that
    # starts inside the window is extended to its natural end first.
    out = scrub("token " + "ww " * 12 + "sk-Ab12Cd7777x rejected")
    assert "sk-Ab12Cd7777x" not in out
    assert "Cd7777x" not in out
    assert "7777x" not in out


def test_rule_a_no_longer_destroys_a_cue_word_before_rule_b_sees_it():
    # Finding 3: Rule A used to run first and could swallow a cue word into
    # its own replacement, destroying the word boundary Rule B needs, so
    # Rule B never fired and the credential beside it survived. Rule B now
    # runs first, while the cue word is still intact.
    #
    # The review quoted "prefix=aaaaaaaaaaaakey sk-Ab12Cd" for this, but
    # that string has no word-bounded cue word to destroy in the first
    # place ("key" there is the tail of "aaaaaaaaaaaakey", so \bkey\b never
    # matches it, before or after Rule A). See the round 4 report. This is
    # the same mechanism with a cue word that is genuinely word-bounded and
    # genuinely swallowed by Rule A's 16-character run.
    out = scrub("key=abcdefghijklmnop sk-Ab12Cd")
    assert "sk-Ab12Cd" not in out


def test_a_value_scrubbed_by_rule_b_is_not_re_matched_by_rule_a():
    # Rule A runs second now, so it sees Rule B's output. The replacement
    # marker "…" is not a token character, so a value Rule B already cut to
    # a four-character tail cannot be re-matched by Rule A into anything
    # that exposes more of it.
    out = scrub("key sk-AAAABBBBCCCCDDDD9999")
    assert "sk-AAAABBBBCCCCDDDD9999" not in out
    assert out == "key …9999"


# Round 5: a run's leading and trailing ": = . , ;" are trimmed off before
# the all-lowercase exemption is judged and before the scrub, in both
# rules. The trimmed characters stay in the output; only what is left is
# treated as the run.

def test_a_word_abutting_a_colon_near_a_cue_stays_readable():
    out = scrub("Incorrect API key provided: sk-abc123XYZ")
    assert "sk-abc123XYZ" not in out
    assert "provided" in out


def test_the_edge_trim_does_not_undo_the_colon_bearing_credential_fix():
    # The trim only touches a run's edges, so internal colons - the whole
    # reason ':' is in the token class - are untouched and this credential
    # is still one run that gets scrubbed whole.
    out = scrub("token ab12:cd34:ef56")
    assert "ab12:cd34:ef56" not in out
    assert "ab12" not in out
    assert "cd34" not in out


def test_a_credential_with_a_trailing_delimiter_is_still_scrubbed():
    # Trimming the trailing colon leaves "abc123", which has digits in it,
    # so the exemption does not cover it and it is still scrubbed. Nothing
    # that was caught becomes uncaught.
    out = scrub("token abc123:")
    assert "abc123" not in out


# Round 6: the edge trim's own hole. Trimming a leading '=' or ':' off a run
# threw away the very character that marked what followed as a value, and the
# all-lowercase exemption then covered it. Confirmed live before the fix:
# scrub('key=abcdef') and friends returned their input unchanged. The
# exemption now stops at a run sitting directly behind an '=' or a ':'.

def test_a_lowercase_value_bolted_to_an_equals_sign_is_scrubbed():
    assert "abcdef" not in scrub("key=abcdef")


def test_a_lowercase_value_bolted_to_an_equals_sign_with_a_tail_is_scrubbed():
    assert "letmein" not in scrub("token=letmein;")


def test_a_lowercase_secret_bolted_to_an_equals_sign_is_scrubbed():
    assert "passwd" not in scrub("secret=passwd;")


def test_a_lowercase_value_bolted_to_a_colon_is_scrubbed():
    assert "letmein" not in scrub("Authorization:letmein")


def test_the_delimiter_itself_stays_where_it_was():
    # The trim is unchanged: the '=' is still in the output, only what
    # followed it is cut down.
    out = scrub("key=abcdef")
    assert out.startswith("key=")


def test_the_carve_out_is_one_sided_and_prose_stays_readable():
    # The whole point of judging only the *leading* side: in prose the
    # lowercase word comes before its colon, never after one, so every word
    # the exemption exists to protect keeps it.
    out = scrub("Incorrect API key provided: sk-abc123XYZ was rejected "
                "by upstream: please check your billing settings")
    assert "sk-abc123XYZ" not in out
    for word in ("provided", "rejected", "upstream", "please", "billing"):
        assert word in out


import pytest


@pytest.fixture
def known_ids():
    from flexrouter import redact
    redact.set_known_identifiers(["nvidia", "nvidia/adept/fuyu-8b", "adept/fuyu-8b",
                                  "mistral/labs-leanstral-1-5", "labs-leanstral-1-5"])
    yield
    redact.set_known_identifiers([])


def test_configured_model_ids_and_request_fields_stay_readable(known_ids):
    # Rule A cut these to "…u-8b" and "…alty", which made the Error brain
    # and every quarantine reason unreadable.
    from flexrouter.redact import scrub
    assert scrub("404 from nvidia/adept/fuyu-8b: Not found") == "404 from nvidia/adept/fuyu-8b: Not found"
    assert scrub("403 from mistral/labs-leanstral-1-5: Model labs-leanstral-1-5 is a Labs model.") \
        == "403 from mistral/labs-leanstral-1-5: Model labs-leanstral-1-5 is a Labs model."
    assert scrub('Unknown name "frequency_penalty": Cannot find field.') \
        == 'Unknown name "frequency_penalty": Cannot find field.'


def test_a_secret_beside_a_known_id_is_still_scrubbed(known_ids):
    from flexrouter.redact import scrub
    out = scrub("api key sk-proj-abcdefghijklmnopqrstuvwxyz123456 rejected for nvidia/adept/fuyu-8b")
    assert "abcdefghijklmnop" not in out
    assert out.endswith("rejected for nvidia/adept/fuyu-8b")


def test_a_secret_that_merely_contains_a_known_id_is_still_scrubbed(known_ids):
    from flexrouter.redact import scrub
    out = scrub("token: xQ9nvidia/adept/fuyu-8bZZ81kd")
    assert "nvidia/adept/fuyu-8b" not in out


def test_a_provider_body_keeps_its_error_codes_but_never_a_stored_key():
    from flexrouter import redact
    body = ('{"error": {"code": 400, "status": "INVALID_ARGUMENT", "type": "labs_not_enabled", '
            '"@type": "type.googleapis.com/google.rpc.BadRequest", '
            '"message": "Incorrect API key provided: gsk_LiveSecretValue0123456789abcdef"}}')
    redact.set_known_secrets(["gsk_LiveSecretValue0123456789abcdef"])
    try:
        out = redact.scrub_body(body)
    finally:
        redact.set_known_secrets([])
    assert "gsk_LiveSecretValue" not in out and "…cdef" in out
    for code in ("INVALID_ARGUMENT", "labs_not_enabled", "type.googleapis.com/google.rpc.BadRequest"):
        assert code in out


def test_a_body_still_loses_a_value_right_after_a_cue_word():
    # Belt and braces for a credential that is not one of ours.
    from flexrouter import redact
    assert "Zx81Qq99Lm" not in redact.scrub_body('{"detail": "token Zx81Qq99Lm rejected"}')
