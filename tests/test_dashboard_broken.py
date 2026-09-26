"""What's broken: two columns, one fix per card, and the menu badge."""
import pytest
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter.app import create_app


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def _down():
    app_module.get_router()._engine._penalties.quarantine_provider("groq", "key rejected")


def test_all_clear_when_nothing_is_wrong(client):
    body = client.get("/broken").text
    assert "All clear" in body


def test_a_down_provider_is_a_card_with_a_fix(client):
    _down()
    body = client.get("/broken").text
    assert 'class="card' in body
    assert "key rejected" in body
    assert 'href="/providers/groq"' in body


def test_both_columns_are_labelled(client):
    _down()
    body = client.get("/broken").text
    assert "Needs you" in body and "Handling it" in body


def test_the_menu_badge_counts_what_needs_you(client):
    _down()
    body = client.get("/models_catalog").text      # any page carries the menu
    assert 'class="nav-badge"' in body


def test_no_badge_when_nothing_needs_you(client):
    assert 'class="nav-badge"' not in client.get("/models_catalog").text


def test_broken_polls_itself(client):
    assert "data-live" in client.get("/broken").text
