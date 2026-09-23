"""The Providers page leads with what you can add, not with a blank form."""
import json

import pytest
from fastapi.testclient import TestClient

from flexrouter.app import create_app


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def test_the_page_offers_every_preset(client):
    body = client.get("/providers").text
    for label in ("Groq", "Cerebras", "OpenRouter", "Mistral",
                  "NVIDIA NIM", "Google AI Studio", "DeepSeek"):
        assert label in body


def test_a_configured_preset_is_marked_as_already_set_up(client):
    # groq is in the test config, so the grid must not offer to add it again
    # as though it were missing.
    body = client.get("/providers").text
    assert 'class="preset-card is-configured"' in body


def test_an_unconfigured_preset_links_to_its_add_page(client):
    assert 'href="/providers/add/cerebras"' in client.get("/providers").text


def test_a_preset_shows_where_to_get_a_key(client):
    body = client.get("/providers/add/cerebras").text
    assert "https://cloud.cerebras.ai" in body


def test_a_broken_owner_preset_is_reported_not_fatal(client, monkeypatch):
    from flexrouter import home
    home.presets_path().write_text('{"oops": 5}', encoding="utf-8")
    r = client.get("/providers")
    assert r.status_code == 200
    assert "oops" in r.text
    assert "Groq" in r.text          # the shipped set still renders


def test_a_custom_provider_is_still_possible(client):
    body = client.get("/providers").text
    assert "Custom provider" in body
    assert 'action="/providers"' in body


def test_a_preset_label_is_escaped(client):
    from flexrouter import home
    home.presets_path().write_text(
        json.dumps({"evil": {"label": "<script>x</script>",
                             "base_url": "https://e.test/v1"}}),
        encoding="utf-8")
    body = client.get("/providers").text
    assert "<script>x</script>" not in body
    assert "&lt;script&gt;" in body
