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

def test_empty_keys_non_local_warns():
    raw = _base()
    raw["providers"]["groq"]["api_keys"] = []
    r = validate_config(raw)
    assert any("groq" in w and "api_keys" in w for w in r["warnings"])

def test_collects_multiple_errors():
    raw = _base()
    raw["tiers"]["default"][0]["provider"] = "nope"
    raw["tiers"]["default"][0]["tpm"] = -5
    r = validate_config(raw)
    assert len(r["errors"]) >= 2
