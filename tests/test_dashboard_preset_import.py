"""Adding a provider from a preset, and importing what it lists.

`probe.probe_key` has done the discovery half since Stage 4 and was
reachable only from the JSON API. These tests are about the page.
"""
import os

import pytest
import respx
import httpx
from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter import keys as keystore, home


@pytest.fixture
def minimal_config(minimal_config):
    # Automatic discovery is experimental and off by default; every test in
    # this module is specifically about the "paste a key, see its models"
    # flow, so it turns discovery on. The off case (no auto-listing at all)
    # is tests/test_experimental_discovery.py's job and
    # test_a_preset_is_added_without_listing_models_when_discovery_is_off
    # below.
    minimal_config["settings"]["experimental_model_discovery"] = True
    return minimal_config


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


MODELS_BODY = {"data": [{"id": "llama-3.3-70b"}, {"id": "qwen-3-32b"}]}


@respx.mock
def test_adding_a_preset_creates_the_provider_and_stores_the_key(client):
    respx.get("https://api.cerebras.ai/v1/models").mock(
        return_value=httpx.Response(200, json=MODELS_BODY))
    r = client.post("/providers/add",
                    data={"name": "cerebras", "secret": "sk-c", "label": "acct 1"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "/providers/cerebras/discover" in r.headers["location"]
    assert keystore.load_keys(home.keys_path())["cerebras"][0].label == "acct 1"


@respx.mock
def test_discovery_lists_the_models_it_actually_found(client):
    respx.get("https://api.cerebras.ai/v1/models").mock(
        return_value=httpx.Response(200, json=MODELS_BODY))
    client.post("/providers/add", data={"name": "cerebras", "secret": "sk-c"},
                follow_redirects=False)
    body = client.get("/providers/cerebras/discover").text
    assert "llama-3.3-70b" in body
    assert "qwen-3-32b" in body
    assert "<form" in body          # each one is importable


@respx.mock
def test_a_rejected_key_explains_itself_instead_of_showing_nothing(client):
    respx.get("https://api.cerebras.ai/v1/models").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}}))
    client.post("/providers/add", data={"name": "cerebras", "secret": "sk-bad"},
                follow_redirects=False)
    body = client.get("/providers/cerebras/discover").text
    # probe.py's own wording - the distinction discovery exists to make.
    assert "rejected" in body.lower()
    assert "no models" not in body.lower()


@respx.mock
def test_a_model_id_from_the_provider_cannot_inject_markup(client):
    respx.get("https://api.cerebras.ai/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "<img src=x onerror=alert(1)>"}]}))
    client.post("/providers/add", data={"name": "cerebras", "secret": "sk-c"},
                follow_redirects=False)
    body = client.get("/providers/cerebras/discover").text
    assert "<img src=x onerror=alert(1)>" not in body
    assert "&lt;img" in body


@respx.mock
def test_a_preset_is_added_without_listing_models_when_discovery_is_off(tmp_path, minimal_config):
    """Adding a provider key via a preset must not auto-list its models
    when experimental_model_discovery is off (the module fixture above turns
    it on; this test explicitly turns it back off to check the default)."""
    import yaml

    minimal_config["settings"]["experimental_model_discovery"] = False
    minimal_config["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(minimal_config))
    os.environ["GROQ_API_KEY"] = "test-key"
    with TestClient(create_app(str(p))) as c:
        route = respx.get("https://api.cerebras.ai/v1/models").mock(
            return_value=httpx.Response(200, json=MODELS_BODY))
        r = c.post("/providers/add", data={"name": "cerebras", "secret": "sk-c"},
                   follow_redirects=False)
        assert not route.called
        assert "/discover" not in r.headers["location"]
        assert "/providers/cerebras" in r.headers["location"]


def test_adding_an_unknown_preset_is_refused(client):
    r = client.post("/providers/add", data={"name": "nope", "secret": "sk-x"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "ok=0" in r.headers["location"]


def test_a_preset_with_no_models_path_skips_discovery(client, monkeypatch):
    from flexrouter import presets
    monkeypatch.setattr(presets, "get", lambda n, path=None: presets.Preset(
        name="bare", label="Bare", base_url="https://bare.test/v1",
        models_path=None))
    r = client.post("/providers/add", data={"name": "bare", "secret": "sk-x"},
                    follow_redirects=False)
    # Straight to the provider page: there is nothing to discover.
    assert "/providers/bare" in r.headers["location"]
    assert "/discover" not in r.headers["location"]
