"""Undo on dangerous buttons (US-87): run at once, offer Undo, no dialog."""
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from flexrouter import keys as keystore
from flexrouter import overrides as ov
from flexrouter.app import create_app
from flexrouter.dashboard import undo


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def _undo_token(response) -> str:
    return parse_qs(urlparse(response.headers["location"]).query)["undo"][0]


def test_restore_puts_back_the_exact_bytes(tmp_path):
    f = tmp_path / "a.json"
    f.write_text('{"x": 1}')
    token = undo.snapshot([f])
    f.write_text("{}")
    assert undo.restore(token) is True
    assert f.read_text() == '{"x": 1}'


def test_restore_removes_a_file_that_did_not_exist(tmp_path):
    f = tmp_path / "new.json"
    token = undo.snapshot([f])
    f.write_text("{}")
    undo.restore(token)
    assert not f.exists()


def test_a_token_works_once_and_expires(tmp_path, monkeypatch):
    f = tmp_path / "a.json"
    f.write_text("1")
    token = undo.snapshot([f])
    assert undo.restore(token) is True
    assert undo.restore(token) is False
    old = undo.snapshot([f])
    monkeypatch.setattr(undo.time, "time", lambda: 10 ** 12)
    assert undo.restore(old) is False


def test_reset_everything_i_changed_can_be_undone(client):
    client.post("/settings/window_seconds", data={"value": "999"})
    r = client.post("/settings/reset-all", follow_redirects=False)
    assert ov.load_overrides() == {}
    assert client.post(f"/undo/{_undo_token(r)}").json()["ok"] is True
    assert ov.load_overrides()["settings"]["window_seconds"] == 999


def test_reset_everything_no_longer_asks_first(client):
    assert "data-confirm" not in client.get("/settings").text


def test_removing_a_key_can_be_undone(client):
    keystore.add_key("groq", "gsk_undo_me_1234567890", "spare")
    kid = next(k.id for k in keystore.load_keys()["groq"] if k.label == "spare")
    r = client.post(f"/providers/groq/keys/{kid}/remove", follow_redirects=False)
    assert all(k.id != kid for k in keystore.load_keys().get("groq", []))
    client.post(f"/undo/{_undo_token(r)}")
    assert any(k.id == kid for k in keystore.load_keys()["groq"])


def test_disabling_a_model_can_be_undone(client):
    r = client.post("/models_catalog/groq/llama-3.1-8b-instant", data={"action": "disable"},
                    follow_redirects=False)
    assert "undo=" in r.headers["location"]
    client.post(f"/undo/{_undo_token(r)}")
    fields = ov.load_overrides().get("models", {}).get("groq/llama-3.1-8b-instant", {})
    assert fields.get("enabled") is not False


def test_a_plain_save_offers_no_undo(client):
    r = client.post("/settings/window_seconds", data={"value": "999"}, follow_redirects=False)
    assert "undo=" not in r.headers["location"]


def test_an_old_undo_says_so(client):
    r = client.post("/undo/not-a-token")
    assert r.status_code == 410
    assert r.json() == {"ok": False, "message": "Too late to undo that"}
