"""A key typed into the settings file must never come back out in full.

Typing a key straight into config.yaml is deprecated but still supported, so
every surface that hands the settings to a human or over HTTP is reachable
with a live secret in hand. The stage's second binding promise is that no
secret is ever printed in full or returned by any interface.
"""
import base64

import pytest
import yaml
from click.testing import CliRunner
from fastapi.testclient import TestClient

from flexrouter import cli, home
from flexrouter.app import create_app

SECRET = "gsk-DO-NOT-LEAK-THIS-abcd1234"

SETTINGS_WITH_AN_INLINE_KEY = f"""\
# The owner's own comment.
settings:
  port: 4891

providers:
  groq:
    base_url: https://api.groq.com/openai/v1
    api_keys:
      - key: {SECRET}
  openai:
    base_url: https://api.openai.com/v1
    api_key: {SECRET}-two
  byenv:
    base_url: https://example.test/v1
    api_keys:
      - env: SOME_ENV_NAME

buckets:
  fast:
    - provider: groq
      model: llama-3.1-8b-instant
      score: 85
      rpm: 30
      tpm: 6000
"""


@pytest.fixture
def home_with_an_inline_key(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "home"))
    home.ensure_home()
    home.config_path().write_text(SETTINGS_WITH_AN_INLINE_KEY, encoding="utf-8")
    return tmp_path


def test_get_config_endpoint_never_returns_a_key_in_full(home_with_an_inline_key):
    client = TestClient(create_app())
    resp = client.get("/api/config")
    assert resp.status_code == 200
    body = resp.text
    assert SECRET not in body
    assert f"{SECRET}-two" not in body
    payload = resp.json()
    assert payload["providers"]["groq"]["api_keys"] == [{"key": "…1234"}]
    assert payload["providers"]["openai"]["api_key"] == "…-two"


def test_get_config_endpoint_leaves_environment_variable_names_alone(
        home_with_an_inline_key):
    client = TestClient(create_app())
    payload = client.get("/api/config").json()
    assert payload["providers"]["byenv"]["api_keys"] == [{"env": "SOME_ENV_NAME"}]


def test_get_config_endpoint_still_returns_the_rest_of_the_settings(
        home_with_an_inline_key):
    client = TestClient(create_app())
    payload = client.get("/api/config").json()
    assert payload["settings"]["port"] == 4891
    assert payload["buckets"]["fast"][0]["model"] == "llama-3.1-8b-instant"


def test_config_export_never_prints_a_key_in_full(home_with_an_inline_key):
    result = CliRunner().invoke(cli.cli, ["config", "export"])
    assert result.exit_code == 0
    assert SECRET not in result.output
    decoded = base64.b64decode(result.output.strip().encode()).decode("utf-8")
    assert SECRET not in decoded
    assert f"{SECRET}-two" not in decoded
    assert "…1234" in decoded


def test_config_export_keeps_the_owners_comments_and_the_rest(
        home_with_an_inline_key):
    result = CliRunner().invoke(cli.cli, ["config", "export"])
    decoded = base64.b64decode(result.output.strip().encode()).decode("utf-8")
    assert "# The owner's own comment." in decoded
    assert "llama-3.1-8b-instant" in decoded
    assert "SOME_ENV_NAME" in decoded


def test_config_import_round_trips_the_masked_export(home_with_an_inline_key):
    token = CliRunner().invoke(cli.cli, ["config", "export"]).output.strip()
    result = CliRunner().invoke(cli.cli, ["config", "import", token])
    assert result.exit_code == 0
    assert SECRET not in result.output
    assert "llama-3.1-8b-instant" in result.output
    assert "flexrouter keys add" in result.output


def test_config_validation_endpoint_never_returns_a_key_in_full(
        home_with_an_inline_key):
    client = TestClient(create_app())
    resp = client.get("/api/config/validate")
    assert resp.status_code == 200
    assert SECRET not in resp.text


def test_doctor_never_prints_a_key_in_full(home_with_an_inline_key):
    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert SECRET not in result.output
    assert SECRET not in (result.stderr or "")


def test_export_refuses_rather_than_leak_when_the_file_cannot_be_parsed(
        tmp_path, monkeypatch):
    """If we cannot parse it we cannot tell which parts are keys, so we must
    not hand the bytes over regardless."""
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "home"))
    home.ensure_home()
    home.config_path().write_text(
        f"providers: [unclosed\n  key: {SECRET}\n", encoding="utf-8")
    result = CliRunner().invoke(cli.cli, ["config", "export"])
    assert result.exit_code != 0
    assert SECRET not in result.output


def test_redact_config_masks_every_shape_a_key_can_take():
    from flexrouter.config import redact_config

    raw = {"providers": {
        "a": {"api_keys": ["sk-aaaa1111"]},
        "b": {"api_keys": [{"key": "sk-bbbb2222"}]},
        "c": {"api_key": "sk-cccc3333"},
        "d": {"api_keys": "SOME_ENV"},
    }}
    safe = redact_config(raw)
    assert safe["providers"]["a"]["api_keys"] == ["…1111"]
    assert safe["providers"]["b"]["api_keys"] == [{"key": "…2222"}]
    assert safe["providers"]["c"]["api_key"] == "…3333"
    assert safe["providers"]["d"]["api_keys"] == "SOME_ENV"
    # the caller's own structure is untouched
    assert raw["providers"]["a"]["api_keys"] == ["sk-aaaa1111"]


def test_redact_config_survives_an_override_layered_on_top(home_with_an_inline_key):
    """Overrides are merged before redaction, so a merged-in provider entry
    has to come through masked too."""
    from flexrouter.dashboard.api import get_config
    from flexrouter.overrides import set_override

    set_override("providers", "groq", "base_url", "https://elsewhere.test/v1")
    safe = get_config()
    assert safe["providers"]["groq"]["base_url"] == "https://elsewhere.test/v1"
    assert safe["providers"]["groq"]["api_keys"] == [{"key": "…1234"}]
    assert SECRET not in yaml.dump(safe)
