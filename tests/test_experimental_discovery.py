"""Auto-adding new models (each provider's /models endpoint, staged into
catalog_pending.json for the owner to accept) is opt-in: `auto_add_models`,
default off - renamed from `experimental_model_discovery`, still read as an
alias (grill-decisions.md §12, PLAN-V2.3.md Session 5). *Reading* the real
model list is a separate, always-on concern now - see
test_refresh_known_model_ids.py and test_model_list_checker.py.
"""
import json

import httpx
import pytest
import respx
import yaml
from fastapi.testclient import TestClient

from flexrouter import overrides as ov
from flexrouter.app import create_app
from flexrouter.config import load_config


# --- config.py / overrides.py ------------------------------------------------

def test_auto_add_models_defaults_to_false(config_file):
    cfg = load_config(config_file)
    assert cfg.auto_add_models is False


def test_auto_add_models_can_be_turned_on(tmp_path, minimal_config):
    minimal_config["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    minimal_config["settings"]["auto_add_models"] = True
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(minimal_config))
    import os
    os.environ["GROQ_API_KEY"] = "test-key"
    cfg = load_config(p)
    assert cfg.auto_add_models is True


def test_the_old_setting_name_still_turns_it_on(tmp_path, minimal_config):
    minimal_config["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    minimal_config["settings"]["experimental_model_discovery"] = True
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(minimal_config))
    import os
    os.environ["GROQ_API_KEY"] = "test-key"
    cfg = load_config(p)
    assert cfg.auto_add_models is True
    assert cfg.experimental_model_discovery is True  # old attribute name, still readable


def test_auto_add_models_is_an_allowed_override_field():
    from flexrouter.overrides import ALLOWED_FIELDS
    assert "auto_add_models" in ALLOWED_FIELDS["settings"]


# --- settings page -----------------------------------------------------------

@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def test_settings_page_shows_the_auto_add_toggle(client):
    body = client.get("/settings").text
    assert "Add new models automatically" in body
    assert "set-auto_add_models" in body


def test_the_toggle_is_off_by_default(client):
    body = client.get("/settings").text
    row = body[body.index('id="set-auto_add_models"'):]
    row = row[:row.index("</div></div>") + 12] if "</div></div>" in row else row[:2000]
    assert "checked" not in row.split("</form>")[0]


def test_turning_the_toggle_on_persists_as_a_real_bool(client):
    r = client.post("/settings/auto_add_models", data={"value": "true"},
                    follow_redirects=False)
    assert r.status_code == 303 and "ok=1" in r.headers["location"]
    assert ov.load_overrides()["settings"]["auto_add_models"] is True


def test_turning_the_toggle_off_persists_as_a_real_bool(client):
    client.post("/settings/auto_add_models", data={"value": "true"})
    r = client.post("/settings/auto_add_models", data={"value": "false"},
                    follow_redirects=False)
    assert r.status_code == 303 and "ok=1" in r.headers["location"]
    assert ov.load_overrides()["settings"]["auto_add_models"] is False


def test_check_for_new_models_button_hidden_when_auto_add_is_off(client):
    body = client.get("/settings").text
    assert "Check for new models now" not in body


def test_check_for_new_models_button_shown_when_auto_add_is_on(client):
    client.post("/settings/auto_add_models", data={"value": "true"})
    body = client.get("/settings").text
    assert "Check for new models now" in body


# --- Models page: pending tray ------------------------------------------------

def test_pending_tray_hidden_when_auto_add_is_off(client):
    from flexrouter import app as app_module
    from flexrouter.store import write_json
    router = app_module.get_router()
    write_json(router._cfg.state_dir + "/catalog_pending.json", {
        "groq": {"checked_at": "now", "appeared": [{"model": "brand-new-model"}],
                 "vanished": [], "changed": []},
    })
    body = client.get("/models_catalog").text
    assert "Pending catalogue changes" not in body
    assert "brand-new-model" not in body


def test_pending_tray_shown_when_auto_add_is_on(client):
    from flexrouter import app as app_module
    from flexrouter.store import write_json
    client.post("/settings/auto_add_models", data={"value": "true"})
    router = app_module.get_router()
    write_json(router._cfg.state_dir + "/catalog_pending.json", {
        "groq": {"checked_at": "now", "appeared": [{"model": "brand-new-model"}],
                 "vanished": [], "changed": []},
    })
    body = client.get("/models_catalog").text
    assert "Pending catalogue changes" in body
    assert "brand-new-model" in body


# --- palette -------------------------------------------------------------------

def test_check_for_new_models_absent_from_palette_when_off(client):
    body = client.get("/palette.json").text
    assert "Check for new models" not in body


def test_check_for_new_models_present_in_palette_when_on(client):
    client.post("/settings/auto_add_models", data={"value": "true"})
    body = client.get("/palette.json").text
    assert "Check for new models" in body


# --- provider discover route ----------------------------------------------------

@respx.mock
def test_discover_route_refuses_to_probe_when_auto_add_is_off(client):
    route = respx.get("https://api.groq.com/openai/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "should-not-be-fetched"}]}))
    body = client.get("/providers/groq/discover").text
    assert not route.called
    assert "auto_add_models" in body.lower() or "add new models automatically" in body.lower()


@respx.mock
def test_discover_route_probes_when_auto_add_is_on(client):
    client.post("/settings/auto_add_models", data={"value": "true"})
    respx.get("https://api.groq.com/openai/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "a-real-model"}]}))
    body = client.get("/providers/groq/discover").text
    assert "a-real-model" in body


# --- /settings/refresh-models ---------------------------------------------------

def test_refresh_models_route_refuses_when_auto_add_is_off(client, monkeypatch):
    called = []
    monkeypatch.setattr("flexrouter.dashboard.api.run_refresh",
                        lambda state_dir: called.append(state_dir))
    r = client.post("/settings/refresh-models", follow_redirects=False)
    assert not called
    assert "ok=0" in r.headers["location"] or "ok=1" in r.headers["location"]
    body = client.get(r.headers["location"]).text
    assert "add new models automatically" in body.lower()


def test_refresh_models_route_runs_when_auto_add_is_on(client, monkeypatch):
    client.post("/settings/auto_add_models", data={"value": "true"})
    called = []

    def fake_run_refresh(state_dir):
        called.append(state_dir)
        return {}

    monkeypatch.setattr("flexrouter.dashboard.api.run_refresh", fake_run_refresh)
    client.post("/settings/refresh-models", follow_redirects=False)
    assert called


def test_import_all_route_refuses_when_auto_add_is_off(client, monkeypatch):
    called = []
    monkeypatch.setattr("flexrouter.dashboard.api.run_refresh",
                        lambda state_dir: called.append(state_dir))
    r = client.post("/models_catalog/import-all", data={"bucket": "fast"}, follow_redirects=False)
    assert not called
    assert "ok=0" in r.headers["location"]


def test_models_page_hides_import_all_when_auto_add_is_off(client):
    assert "/models_catalog/import-all" not in client.get("/models_catalog").text
