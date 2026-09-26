"""The Ctrl+K command bar's index."""
import pytest
from fastapi.testclient import TestClient

from flexrouter.app import create_app


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def test_the_index_lists_pages_providers_models_settings_and_actions(client):
    items = client.get("/palette.json").json()
    kinds = {i["kind"] for i in items}
    assert {"page", "provider", "model", "bucket", "setting", "action"} <= kinds
    assert any(i["label"] == "groq" and i["href"] == "/providers/groq" for i in items)
    assert any(i["href"] == "/settings#set-port" for i in items)


def test_every_page_carries_the_command_bar_shell(client):
    body = client.get("/models_catalog").text
    assert 'id="palette"' in body
