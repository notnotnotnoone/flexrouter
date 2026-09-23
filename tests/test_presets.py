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
    assert groq.header_parser == "groq"
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


# --- Owner presets layering (Task 2) ---

import json


def _write(tmp_path, body):
    p = tmp_path / "presets.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


def test_the_owner_can_add_a_preset_of_their_own(tmp_path):
    path = _write(tmp_path, {"fireworks": {
        "label": "Fireworks",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "models_path": "/models",
    }})
    found = presets.all(path)
    assert "fireworks" in found
    assert found["fireworks"].base_url == "https://api.fireworks.ai/inference/v1"
    # and the shipped ten are still there
    assert "groq" in found


def test_an_owner_preset_overrides_a_shipped_one_field_by_field(tmp_path):
    path = _write(tmp_path, {"groq": {"seed_rpm": 1000}})
    groq = presets.all(path)["groq"]
    assert groq.seed_rpm == 1000
    # Untouched fields survive: this is a patch, not a replacement. A whole
    # -entry replace would silently blank base_url for anyone who only
    # wanted to bump a rate.
    assert groq.base_url == "https://api.groq.com/openai/v1"
    assert groq.label == "Groq"


def test_a_broken_entry_costs_one_preset_not_the_page(tmp_path):
    path = _write(tmp_path, {
        "good": {"base_url": "https://example.test/v1"},
        "bad": {"seed_rpm": "not a number"},
    })
    found = presets.all(path)
    assert "good" in found
    assert "bad" not in found
    assert "groq" in found          # shipped set unharmed
    assert any("bad" in p for p in presets.problems(path))


def test_a_corrupt_file_is_survivable(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert "groq" in presets.all(path)
    assert presets.problems(path)


def test_a_missing_file_is_not_a_problem(tmp_path):
    path = tmp_path / "nope.json"
    assert "groq" in presets.all(path)
    assert presets.problems(path) == []


def test_get_returns_none_for_an_unknown_name(tmp_path):
    assert presets.get("groq") is not None
    assert presets.get("no-such-provider") is None


# --- Catalogue ↔ Registry (Task 3) ---


def test_the_catalogue_is_now_a_view_of_the_registry():
    """`refresh.py` still reads catalogue.PROVIDERS. It must not notice."""
    from flexrouter import catalogue
    names = {p.name for p in catalogue.PROVIDERS}
    assert names == set(presets.shipped())
    groq = next(p for p in catalogue.PROVIDERS if p.name == "groq")
    assert groq.base_url == "https://api.groq.com/openai/v1"
    # The seed values reach the old attribute names refresh.py reads.
    assert groq.default_rpm == 30
    assert callable(groq.free_filter)


def test_the_openrouter_filter_still_drops_paid_models():
    from flexrouter import catalogue
    orouter = next(p for p in catalogue.PROVIDERS if p.name == "openrouter")
    assert orouter.free_filter({"id": "x", "pricing": {"prompt": "0"}}) is True
    assert orouter.free_filter({"id": "x", "pricing": {"prompt": "0.5"}}) is False
