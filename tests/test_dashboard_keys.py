"""Editing keys from the dashboard.

The storage for all of this has existed since Stage 6; only the routes
were missing, so the page could show a key and never change one.
"""
import pytest
from fastapi.testclient import TestClient

from flexrouter import app as app_module
from flexrouter.app import create_app
from flexrouter import keys as keystore


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


@pytest.fixture
def keys_path():
    from flexrouter import home
    return home.keys_path()


def test_a_key_can_be_added_to_a_provider_that_already_exists(client, keys_path):
    r = client.post("/providers/groq/keys",
                    data={"secret": "sk-new-one", "label": "second account"},
                    follow_redirects=False)
    assert r.status_code == 303
    records = keystore.load_keys(keys_path)["groq"]
    assert any(k.label == "second account" for k in records)


def test_the_page_never_renders_the_secret(client):
    client.post("/providers/groq/keys",
                data={"secret": "sk-super-secret-value", "label": "x"},
                follow_redirects=False)
    body = client.get("/providers/groq").text
    assert "sk-super-secret-value" not in body
    assert "…alue" in body          # mask() output, and only that


def test_adding_a_blank_key_is_refused_with_a_reason(client):
    r = client.post("/providers/groq/keys", data={"secret": "   "},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "ok=0" in r.headers["location"]
    assert "paste" in r.headers["location"].lower()


def test_a_key_can_be_removed(client, keys_path):
    client.post("/providers/groq/keys", data={"secret": "sk-doomed"},
                follow_redirects=False)
    made = keystore.load_keys(keys_path)["groq"][-1]
    r = client.post(f"/providers/groq/keys/{made.id}/remove", follow_redirects=False)
    assert r.status_code == 303
    assert all(k.id != made.id for k in keystore.load_keys(keys_path)["groq"])


def test_a_key_can_be_disabled_from_the_page(client, keys_path):
    client.post("/providers/groq/keys", data={"secret": "sk-x"},
                follow_redirects=False)
    made = keystore.load_keys(keys_path)["groq"][-1]
    client.post(f"/providers/groq/keys/{made.id}/edit",
                data={"enabled": "", "label": made.label, "weight": "1",
                      "allow_models": "*"},
                follow_redirects=False)
    after = [k for k in keystore.load_keys(keys_path)["groq"] if k.id == made.id][0]
    assert after.enabled is False


def test_globs_are_split_on_commas_and_whitespace(client, keys_path):
    client.post("/providers/groq/keys", data={"secret": "sk-x"},
                follow_redirects=False)
    made = keystore.load_keys(keys_path)["groq"][-1]
    client.post(f"/providers/groq/keys/{made.id}/edit",
                data={"enabled": "on", "label": "l", "weight": "2",
                      "allow_models": "llama-* , gemma-*"},
                follow_redirects=False)
    after = [k for k in keystore.load_keys(keys_path)["groq"] if k.id == made.id][0]
    assert after.allow_models == ["llama-*", "gemma-*"]
    assert after.weight == 2


def test_editing_a_key_that_vanished_says_so_instead_of_500ing(client):
    r = client.post("/providers/groq/keys/groq-404/edit",
                    data={"label": "x", "weight": "1", "allow_models": "*"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "ok=0" in r.headers["location"]


def test_a_key_label_cannot_inject_markup(client, keys_path):
    client.post("/providers/groq/keys",
                data={"secret": "sk-x", "label": "<script>alert(1)</script>"},
                follow_redirects=False)
    body = client.get("/providers/groq").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body
