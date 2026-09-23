"""The generated app password (ADR 0015)."""
import pytest
from fastapi.testclient import TestClient

from flexrouter import app_password
from flexrouter.app import create_app


def test_nothing_generated_means_none(tmp_path):
    assert app_password.current(tmp_path / "keys.json") is None


def test_generate_stores_and_returns_the_same_secret(tmp_path):
    path = tmp_path / "keys.json"
    secret = app_password.generate(path)
    assert secret.startswith("fr-") and len(secret) > 30
    assert app_password.current(path) == secret


def test_generating_again_replaces_the_old_one(tmp_path):
    path = tmp_path / "keys.json"
    first = app_password.generate(path)
    second = app_password.generate(path)
    assert first != second and app_password.current(path) == second


def test_generated_wins_over_the_settings_file(tmp_path):
    path = tmp_path / "keys.json"
    assert app_password.effective("from-settings", path) == "from-settings"
    secret = app_password.generate(path)
    assert app_password.effective("from-settings", path) == secret


def test_clear_falls_back_to_the_settings_file(tmp_path):
    path = tmp_path / "keys.json"
    app_password.generate(path)
    app_password.clear(path)
    assert app_password.effective("from-settings", path) == "from-settings"


# ── the server actually enforces it ────────────────────────────────────

@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def test_v1_requires_the_generated_password(client):
    assert client.get("/v1/models").status_code == 200       # nothing set yet
    secret = app_password.generate()
    assert client.get("/v1/models").status_code == 401
    ok = client.get("/v1/models", headers={"Authorization": f"Bearer {secret}"})
    assert ok.status_code == 200


def test_the_dashboard_shows_a_new_password_once(client):
    r = client.post("/settings/app-password", follow_redirects=False)
    assert r.status_code == 200
    secret = app_password.current()
    assert secret in r.text                          # shown now...
    assert secret not in client.get("/settings").text  # ...and never again


def test_the_dashboard_never_accepts_a_typed_password(client):
    client.post("/settings/app-password", data={"secret": "chosen-by-attacker"})
    assert app_password.current() != "chosen-by-attacker"
