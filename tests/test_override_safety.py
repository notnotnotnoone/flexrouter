"""Overrides arrive from outside and outlive the process that wrote them.

`POST /api/config` is unauthenticated and local, `overrides.json` is layered
over the settings on every load, and nothing may rewrite the settings file to
undo a bad one. So a change that `load_config` cannot make sense of would wedge
settings loading permanently, with no way out but hand-editing JSON.
"""
import pytest
import yaml
from click.testing import CliRunner
from fastapi.testclient import TestClient

from flexrouter import cli, home
from flexrouter.app import create_app
from flexrouter.config import load_config
from flexrouter.dashboard import api
from flexrouter.exceptions import ConfigError
from flexrouter.overrides import load_overrides, save_overrides

SETTINGS = {
    "settings": {"port": 4891},
    "providers": {"groq": {"base_url": "https://api.groq.com/openai/v1"}},
    "buckets": {"fast": [{"provider": "groq", "model": "llama-3.1-8b-instant",
                          "score": 85, "rpm": 30, "tpm": 6000}]},
}


@pytest.fixture
def a_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "home"))
    home.ensure_home()
    home.config_path().write_text(yaml.dump(SETTINGS), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# What may be overridden
# ---------------------------------------------------------------------------


def test_a_setting_that_is_not_a_real_setting_is_refused(a_home):
    with pytest.raises(ValueError):
        api.post_config({"settings": {"nonsense_field": 1}})
    assert load_overrides() == {}


def test_a_model_override_cannot_rewrite_which_provider_a_model_is_on(a_home):
    """Rewriting `provider` turns an override for one model into a different
    model, possibly on a provider that does not exist."""
    with pytest.raises(ValueError):
        api.post_config({"models": {"groq/llama-3.1-8b-instant":
                                    {"provider": "does-not-exist"}}})
    assert load_overrides() == {}


def test_a_model_override_cannot_rewrite_the_model_id(a_home):
    with pytest.raises(ValueError):
        api.post_config({"models": {"groq/llama-3.1-8b-instant":
                                    {"model": "something-else"}}})
    assert load_overrides() == {}


def test_a_provider_override_cannot_point_at_an_environment_variable(a_home):
    with pytest.raises(ValueError):
        api.post_config({"providers": {"groq": {"api_key_env": "ANY_VAR"}}})
    assert load_overrides() == {}


def test_an_unknown_section_is_refused(a_home):
    with pytest.raises(ValueError):
        api.post_config({"nonsense": {"a": 1}})
    assert load_overrides() == {}


def test_nothing_is_written_when_any_part_of_the_change_is_refused(a_home):
    with pytest.raises(ValueError):
        api.post_config({"settings": {"port": 7000, "nonsense_field": 1}})
    assert load_overrides() == {}


def test_the_changes_that_are_allowed_still_work(a_home):
    api.post_config({
        "settings": {"port": 7000},
        "providers": {"groq": {"base_url": "https://elsewhere.test/v1"}},
        "models": {"groq/llama-3.1-8b-instant": {"enabled": False}},
    })
    saved = load_overrides()
    assert saved["settings"]["port"] == 7000
    assert saved["providers"]["groq"]["base_url"] == "https://elsewhere.test/v1"
    assert saved["models"]["groq/llama-3.1-8b-instant"]["enabled"] is False


def test_the_endpoint_answers_a_refused_change_with_400_not_a_traceback(a_home):
    client = TestClient(create_app())
    resp = client.post("/api/config", json={"settings": {"nonsense_field": 1}})
    assert resp.status_code == 400
    assert "nonsense_field" in resp.json()["error"]


# ---------------------------------------------------------------------------
# A bad value is reported as a settings problem, not a crash
# ---------------------------------------------------------------------------


def test_a_setting_that_is_not_a_number_raises_configerror(a_home):
    save_overrides({"settings": {"port": "not-a-port"}})
    with pytest.raises(ConfigError) as excinfo:
        load_config()
    assert "port" in str(excinfo.value)
    assert "not-a-port" in str(excinfo.value)


def test_doctor_explains_a_bad_setting_instead_of_tracebacking(a_home):
    save_overrides({"settings": {"window_seconds": "soon"}})
    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "window_seconds" in (result.output + (result.stderr or ""))


# ---------------------------------------------------------------------------
# Getting out of it again
# ---------------------------------------------------------------------------


def test_config_reset_undoes_one_wedging_change(a_home):
    save_overrides({"settings": {"port": "not-a-port", "window_seconds": 30}})
    result = CliRunner().invoke(cli.cli, ["config", "reset", "settings", "port"])
    assert result.exit_code == 0
    assert load_config().port == 4891
    assert load_overrides()["settings"]["window_seconds"] == 30


def test_config_reset_with_no_arguments_undoes_everything(a_home):
    save_overrides({"settings": {"port": "not-a-port"},
                    "models": {"groq/llama-3.1-8b-instant": {"enabled": False}}})
    result = CliRunner().invoke(cli.cli, ["config", "reset"])
    assert result.exit_code == 0
    assert load_overrides() == {}
    assert load_config().port == 4891


def test_config_reset_undoes_a_whole_section(a_home):
    save_overrides({"settings": {"port": 7000},
                    "models": {"groq/llama-3.1-8b-instant": {"enabled": False}}})
    CliRunner().invoke(cli.cli, ["config", "reset", "models"])
    assert "models" not in load_overrides()
    assert load_overrides()["settings"]["port"] == 7000


def test_config_reset_never_touches_the_settings_file(a_home):
    save_overrides({"settings": {"port": 7000}})
    before = home.config_path().read_bytes()
    CliRunner().invoke(cli.cli, ["config", "reset"])
    assert home.config_path().read_bytes() == before


def test_config_reset_says_so_when_there_is_nothing_to_undo(a_home):
    result = CliRunner().invoke(cli.cli, ["config", "reset", "settings", "port"])
    assert result.exit_code == 1
