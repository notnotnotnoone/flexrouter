"""The provider preset registry.

Presets used to be a Python list with a callable in it, which meant the
dashboard could not show them and the owner could not add one. They are
data now. These tests hold the shape of that data.
"""
import pytest

from flexrouter import presets


def test_every_requested_provider_ships():
    names = set(presets.shipped())
    # The seven the owner asked for, plus the three already configured in
    # the wild, which must not regress.
    assert {"groq", "cerebras", "openrouter", "mistral", "nvidia",
            "googleai", "deepseek"} <= names
    assert {"ollama", "siliconflow", "sambanova"} <= names


def test_a_preset_carries_everything_the_add_form_needs():
    groq = presets.shipped()["groq"]
    assert groq.base_url == "https://api.groq.com/openai/v1"
    assert groq.signup_url.startswith("https://")
    assert groq.models_path == "/models"
    assert groq.header_parser == "openai_compatible"
    assert groq.seed_rpm > 0 and groq.seed_tpm > 0


def test_ollama_lists_models_at_its_own_path():
    # The one provider whose model list is not OpenAI-shaped.
    assert presets.shipped()["ollama"].models_path == "/api/tags"


def test_a_preset_without_discovery_says_so_with_none():
    # `None` is the honest value for "cannot discover", and the UI keys off
    # it to hide a button that could not work. An empty string would read
    # as a path and produce a request to the base URL.
    for p in presets.shipped().values():
        assert p.models_path is None or p.models_path.startswith("/")


def test_seed_limits_are_named_seeds_not_defaults():
    # Renamed deliberately: these fill in a newly imported model's fields
    # once and are never read as a limit. `default_rpm` invited exactly the
    # confusion this rename exists to end.
    groq = presets.shipped()["groq"]
    assert not hasattr(groq, "default_rpm")
    assert not hasattr(groq, "default_tpm")
