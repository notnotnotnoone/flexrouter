import pytest
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter.app import create_app
from flexrouter.dashboard.render import AREAS


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def test_overview_is_served_at_the_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_every_area_has_a_page(client):
    for slug, _, _ in AREAS:
        path = "/" if slug == "overview" else f"/{slug}"
        assert client.get(path).status_code == 200, slug


def test_every_page_carries_the_whole_menu(client):
    body = client.get("/").text
    for _, label, _ in AREAS:
        assert label.replace("&", "&amp;").replace("'", "&#x27;") in body


def test_the_stylesheet_is_served(client):
    r = client.get("/wire.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]


def test_overview_shows_the_real_counts(client):
    body = client.get("/").text
    assert "Providers" in body
    assert "Models" in body


def test_unbuilt_areas_say_so_rather_than_pretending(client):
    body = client.get("/models").text
    assert "not built yet" in body.lower()


def test_an_unknown_path_is_a_404_not_the_dashboard(client):
    r = client.get("/no-such-page")
    assert r.status_code == 404


def test_the_openai_surface_still_works(client):
    assert client.get("/v1/models").status_code == 200


def test_the_private_api_still_works(client):
    assert client.get("/api/providers").status_code == 200


def test_a_healthy_provider_shows_state_ok(client):
    # The colored state marker is the first thing the owner's eye goes to;
    # the config_file fixture's one provider has a live, unquarantined key,
    # so it must render as "ok".
    body = client.get("/").text
    assert "state-ok" in body


def test_a_quarantined_provider_shows_state_bad_and_why(client):
    # Same router the app is already using - not a second LocalRouter - so
    # the quarantine is visible to the request the test client makes.
    router = app_module.get_router()
    router._engine._penalties.quarantine_provider("groq", "key rejected")
    body = client.get("/").text
    assert "state-bad" in body
    assert "key rejected" in body


def test_a_provider_name_is_escaped_not_injected(client, config_file):
    # Guards the whole rendering approach: a provider named with markup must
    # not reach the browser as markup.
    import yaml
    raw = yaml.safe_load(config_file.read_text())
    raw["providers"]["<script>bad</script>"] = {
        "base_url": "https://example.invalid/v1",
        "api_keys": [{"env": "GROQ_API_KEY"}],
    }
    config_file.write_text(yaml.dump(raw))
    with TestClient(create_app(str(config_file))) as c:
        body = c.get("/").text
    assert "<script>bad</script>" not in body
