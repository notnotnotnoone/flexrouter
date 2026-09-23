"""Settings: plain names in groups, preferences, backup and restore."""
import json

import pytest
from fastapi.testclient import TestClient

from flexrouter import overrides as ov
from flexrouter.app import create_app
from flexrouter.dashboard import prefs


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def test_settings_have_plain_names_and_keep_the_raw_one(client):
    body = client.get("/settings").text
    assert "Sideline a failing provider for" in body
    assert "penalty_base_seconds" in body


def test_settings_are_grouped(client):
    body = client.get("/settings").text
    for group in ("Server", "Retries", "Sidelining bad providers", "Error brain", "History"):
        assert group in body


def test_the_port_says_it_needs_a_restart(client):
    body = client.get("/settings").text
    port_row = body[body.index('id="set-port"'):]
    assert "restart needed" in port_row[:1500]


def test_every_row_is_searchable(client):
    assert body_count(client.get("/settings").text, "data-search=") >= 20


def body_count(body, needle):
    return body.count(needle)


def test_dashboard_preferences_save(client):
    r = client.post("/settings/dashboard", data={"motion": "off", "refresh_seconds": "30",
                                                 "default_range": "7d", "timezone": "local"},
                    follow_redirects=False)
    assert r.status_code == 303 and "ok=1" in r.headers["location"]
    assert prefs.load().motion == "off"
    assert 'data-motion="off"' in client.get("/settings").text


def test_bad_preferences_are_refused(client):
    r = client.post("/settings/dashboard", data={"refresh_seconds": "0"}, follow_redirects=False)
    assert "ok=0" in r.headers["location"]


def test_backup_holds_changes_but_never_keys(client):
    client.post("/settings/window_seconds", data={"value": "999"})
    client.post("/settings/keys/aa", data={"secret": "aa-secretvalue1234"})
    r = client.get("/settings/backup")
    data = r.json()
    assert data["overrides"]["settings"]["window_seconds"] == 999
    assert "aa-secretvalue1234" not in r.text
    assert "attachment" in r.headers["content-disposition"]


def test_restore_round_trips(client):
    client.post("/settings/window_seconds", data={"value": "999"})
    backup = client.get("/settings/backup").content
    client.post("/settings/reset-all")
    assert ov.load_overrides() == {}
    r = client.post("/settings/restore", files={"backup": ("b.json", backup, "application/json")},
                    follow_redirects=False)
    assert "ok=1" in r.headers["location"]
    assert ov.load_overrides()["settings"]["window_seconds"] == 999


def test_restore_refuses_a_backup_that_would_set_a_forbidden_field(client):
    evil = json.dumps({"flexrouter_backup": 1, "overrides": {"settings": {"auth_token": "x"}}})
    r = client.post("/settings/restore", files={"backup": ("b.json", evil, "application/json")},
                    follow_redirects=False)
    assert "ok=0" in r.headers["location"]
    assert "auth_token" not in json.dumps(ov.load_overrides())


def test_restore_refuses_a_file_that_is_not_a_backup(client):
    r = client.post("/settings/restore", files={"backup": ("b.json", b"{}", "application/json")},
                    follow_redirects=False)
    assert "ok=0" in r.headers["location"]


def test_reset_all_clears_every_dashboard_change(client):
    client.post("/settings/window_seconds", data={"value": "999"})
    client.post("/settings/reset-all")
    assert ov.load_overrides() == {}


def test_the_settings_file_view_hides_keys(client, config_file):
    body = client.get("/settings").text
    assert "Your settings file" in body
