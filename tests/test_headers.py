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


def test_groq_request_limit_is_per_day_so_it_is_not_read_as_rpm():
    # Groq docs: x-ratelimit-limit-requests is requests per DAY. Seen live on
    # allam-2-7b: limit 7000 with a 12.34s reset (86400 / 7000). Stored as
    # rpm, it let the router fire far past the real per-minute cap.
    parsed = parse_headers("groq", {
        "x-ratelimit-limit-requests": "7000",
        "x-ratelimit-remaining-requests": "6999",
        "x-ratelimit-reset-requests": "12.342s",
        "x-ratelimit-limit-tokens": "6000",
        "x-ratelimit-remaining-tokens": "5987",
        "x-ratelimit-reset-tokens": "130ms",
    })
    assert parsed.limit_requests is None
    assert parsed.limit_tokens == 6000
    assert parsed.remaining_requests == 6999  # hitting 0 still means "wait for the reset"
    assert parsed.reset_requests_at is not None


def test_mistral_headers_are_read_under_their_own_names():
    # Seen live on ministral-3b-latest; the openai_compatible names are absent.
    parsed = parse_headers("mistral", {
        "x-ratelimit-limit-req-minute": "750",
        "x-ratelimit-remaining-req-minute": "749",
        "x-ratelimit-limit-tokens-minute": "1300000",
        "x-ratelimit-remaining-tokens-minute": "1299991",
    })
    assert (parsed.limit_requests, parsed.limit_tokens) == (750, 1300000)
    assert (parsed.remaining_requests, parsed.remaining_tokens) == (749, 1299991)


def test_the_groq_and_mistral_presets_use_their_own_parsers():
    from flexrouter import presets
    shipped = presets.shipped()
    assert shipped["groq"].header_parser == "groq"
    assert shipped["mistral"].header_parser == "mistral"


def test_a_provider_saved_before_its_parser_existed_uses_the_preset_parser(tmp_path, monkeypatch):
    # Providers added from a preset stored a snapshot of its header_parser,
    # so fixing the preset never reached them.
    from flexrouter import config as config_mod
    raw = {
        "providers": {
            "groq": {"base_url": "https://api.groq.com/openai/v1", "header_parser": "openai_compatible"},
            "custom": {"base_url": "https://my.proxy/v1", "header_parser": "openai_compatible"},
            "mistral": {"base_url": "https://some.other.host/v1", "header_parser": "openai_compatible"},
        },
        "buckets": {"b": []},
        "settings": {"state_dir": str(tmp_path)},
    }
    path = tmp_path / "config.yaml"
    import yaml
    path.write_text(yaml.dump(raw))
    monkeypatch.setattr(config_mod, "load_keys", lambda *a, **k: {})
    cfg = config_mod.load_config(path)
    assert cfg.providers["groq"].header_parser == "groq"
    assert cfg.providers["custom"].header_parser == "openai_compatible"
    assert cfg.providers["mistral"].header_parser == "openai_compatible"  # not Mistral's endpoint
