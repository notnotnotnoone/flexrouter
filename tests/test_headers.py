from flexrouter.headers import parse_headers, ParsedHeaders, PARSERS


def test_openai_compatible_parses_all_fields():
    headers = {
        "x-ratelimit-limit-requests": "30",
        "x-ratelimit-limit-tokens": "6000",
        "x-ratelimit-remaining-requests": "29",
        "x-ratelimit-remaining-tokens": "5900",
        "x-ratelimit-reset-requests": "1m30s",
        "x-ratelimit-reset-tokens": "500ms",
    }
    result = parse_headers("openai_compatible", headers)
    assert result.limit_requests == 30
    assert result.limit_tokens == 6000
    assert result.remaining_requests == 29
    assert result.remaining_tokens == 5900
    assert result.reset_requests_at is not None
    assert result.reset_tokens_at is not None


def test_openai_compatible_missing_headers_returns_none_fields():
    result = parse_headers("openai_compatible", {})
    assert result == ParsedHeaders()


def test_unknown_parser_name_falls_back_to_openai_compatible():
    headers = {"x-ratelimit-remaining-requests": "5"}
    result = parse_headers("does-not-exist", headers)
    assert result.remaining_requests == 5


def test_cerebras_and_openrouter_are_registered_aliases():
    assert "cerebras" in PARSERS
    assert "openrouter" in PARSERS
    headers = {"x-ratelimit-remaining-requests": "7"}
    assert parse_headers("cerebras", headers).remaining_requests == 7
    assert parse_headers("openrouter", headers).remaining_requests == 7


def test_google_falls_back_to_goog_prefixed_headers():
    headers = {"x-goog-ratelimit-remaining-requests": "3"}
    result = parse_headers("google", headers)
    assert result.remaining_requests == 3


def test_duration_parser_handles_hours_minutes_seconds_ms_and_bare_seconds():
    from flexrouter.headers import _parse_duration_ms
    assert _parse_duration_ms("2h") == 2 * 3_600_000
    assert _parse_duration_ms("1m30s") == 90_000
    assert _parse_duration_ms("500ms") == 500
    assert _parse_duration_ms("14") == 14_000
    assert _parse_duration_ms(None) is None
    assert _parse_duration_ms("garbage") is None
