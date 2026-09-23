# tests/test_config_validate.py
from flexrouter.config import validate_config

def _base():
    return {
        "providers": {"groq": {"base_url": "https://api.groq.com/openai/v1", "api_keys": ["k"]}},
        "tiers": {"default": [{"provider": "groq", "model": "llama-3.1-8b-instant",
                               "score": 50, "rpm": 30, "tpm": 6000, "context_window": 131072}]},
    }

def test_clean_config_has_no_errors():
    r = validate_config(_base())
    assert r["errors"] == []

def test_unknown_provider_reference_is_error():
    raw = _base()
    raw["tiers"]["default"][0]["provider"] = "nope"
    r = validate_config(raw)
    assert any("nope" in e and "default[0]" in e for e in r["errors"])

def test_non_positive_rpm_is_error():
    raw = _base()
    raw["tiers"]["default"][0]["rpm"] = 0
    r = validate_config(raw)
    assert any("rpm" in e and "default[0]" in e for e in r["errors"])

def test_low_context_window_warns_modality():
    raw = _base()
    raw["tiers"]["default"][0]["context_window"] = 448
    r = validate_config(raw)
    assert any("context_window" in w for w in r["warnings"])

def test_non_chat_name_warns_modality():
    raw = _base()
    raw["tiers"]["default"][0]["model"] = "whisper-large-v3"
    r = validate_config(raw)
    assert any("whisper-large-v3" in w for w in r["warnings"])

def test_no_credential_anywhere_for_a_non_local_provider_warns():
    raw = _base()
    raw["providers"]["groq"]["api_keys"] = []
    r = validate_config(raw)
    assert any("groq" in w and "no key found" in w for w in r["warnings"])


def test_empty_keys_in_the_settings_file_is_not_a_warning_on_its_own(tmp_path, monkeypatch):
    """Credentials live in keys.json now, so an empty `api_keys:` in the
    settings file is the correct state, not a problem to report."""
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    from flexrouter.keys import add_key
    add_key("groq", "gsk-saved")
    raw = _base()
    raw["providers"]["groq"]["api_keys"] = []
    r = validate_config(raw)
    assert not any("groq" in w and "no key" in w for w in r["warnings"])


def test_an_environment_variable_also_counts_as_a_credential(monkeypatch):
    monkeypatch.setenv("GROQ_KEY_FOR_TEST", "gsk-from-env")
    raw = _base()
    raw["providers"]["groq"]["api_keys"] = []
    raw["providers"]["groq"]["api_key_env"] = "GROQ_KEY_FOR_TEST"
    r = validate_config(raw)
    assert not any("groq" in w and "no key" in w for w in r["warnings"])


def test_the_buckets_spelling_is_understood():
    """Against the tool's own starter settings file this used to report
    'tiers: no tiers defined' as a hard error."""
    raw = _base()
    raw["buckets"] = raw.pop("tiers")
    r = validate_config(raw)
    assert r["errors"] == []


def test_the_buckets_spelling_reports_problems_under_that_name():
    raw = _base()
    raw["buckets"] = raw.pop("tiers")
    raw["buckets"]["default"][0]["provider"] = "nope"
    r = validate_config(raw)
    assert any("buckets.default[0]" in e for e in r["errors"])


def test_the_starter_settings_file_validates_without_a_hard_error(tmp_path, monkeypatch):
    import yaml

    from flexrouter import home

    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    home.ensure_home()
    raw = yaml.safe_load(home.config_path().read_text(encoding="utf-8"))
    r = validate_config(raw)
    # It has no providers yet, which is a real thing to report; it must not
    # additionally claim the buckets it does define are missing.
    assert not any("no buckets defined" in e or "no tiers defined" in e
                   for e in r["errors"])

def test_is_probably_chat_model():
    from flexrouter.config import is_probably_chat_model
    assert is_probably_chat_model("llama-3.1-8b-instant") is True
    assert is_probably_chat_model("whisper-large-v3") is False
    assert is_probably_chat_model("models/gemini-2.5-flash-image") is False
    assert is_probably_chat_model("google/lyria-3-pro-preview") is False
    assert is_probably_chat_model("codestral-embed-2505") is False
    assert is_probably_chat_model("mistral-ocr-latest") is False
    assert is_probably_chat_model("mistral-moderation-2603") is False
    assert is_probably_chat_model("google/deplot") is False
    assert is_probably_chat_model("codestral-latest") is True

def test_collects_multiple_errors():
    raw = _base()
    raw["tiers"]["default"][0]["provider"] = "nope"
    raw["tiers"]["default"][0]["tpm"] = -5
    r = validate_config(raw)
    assert len(r["errors"]) >= 2
