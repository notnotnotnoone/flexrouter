import pytest

from flexrouter.dashboard import settings_write as sw
from flexrouter.overrides import load_overrides


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    return tmp_path


def test_set_settings_field_writes_an_override():
    sw.set_settings_field("port", 9999)
    assert load_overrides()["settings"]["port"] == 9999


def test_set_settings_field_rejects_an_unknown_field():
    with pytest.raises(ValueError):
        sw.set_settings_field("auth_token", "x")


def test_clear_settings_field_removes_it():
    sw.set_settings_field("port", 9999)
    sw.clear_settings_field("port")
    assert load_overrides().get("settings", {}) == {}


def test_set_provider_fields_writes_every_field():
    sw.set_provider_fields("groq", {"base_url": "http://x/v1", "header_parser": "openai_compatible"})
    ov = load_overrides()
    assert ov["providers"]["groq"]["base_url"] == "http://x/v1"
    assert ov["providers"]["groq"]["header_parser"] == "openai_compatible"


def test_set_provider_fields_rejects_a_credential_field():
    with pytest.raises(ValueError):
        sw.set_provider_fields("groq", {"api_key": "sk-x"})


def test_clear_provider_removes_every_field_at_once():
    sw.set_provider_fields("groq", {"base_url": "http://x/v1", "header_parser": "openai_compatible"})
    sw.clear_provider("groq")
    assert load_overrides().get("providers", {}) == {}


def test_set_model_fields_writes_every_field():
    sw.set_model_fields("groq", "llama-3.1-8b-instant", {"score": 90, "rpm": 30})
    ov = load_overrides()
    entry = ov["models"]["groq/llama-3.1-8b-instant"]
    assert entry["score"] == 90
    assert entry["rpm"] == 30


def test_set_model_fields_rejects_the_identity_fields():
    with pytest.raises(ValueError):
        sw.set_model_fields("groq", "llama-3.1-8b-instant", {"provider": "openai"})


def test_clear_model_removes_every_field_at_once():
    sw.set_model_fields("groq", "llama-3.1-8b-instant", {"score": 90, "rpm": 30})
    sw.clear_model("groq", "llama-3.1-8b-instant")
    assert load_overrides().get("models", {}) == {}
