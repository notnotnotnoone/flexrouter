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
    # A bare string here is read as an env var name when a key is being
    # resolved, but it is also where a mistyped key lands, and nothing tells
    # the two apart — so it is masked rather than shown.
    assert safe["providers"]["d"]["api_keys"] == "…_ENV"
    # the caller's own structure is untouched
    assert raw["providers"]["a"]["api_keys"] == ["sk-aaaa1111"]


def test_redact_config_masks_auth_token():
    from flexrouter.config import redact_config

    raw = {"settings": {"auth_token": "flx-AAAABBBBCCCCDDDD1234"}}
    safe = redact_config(raw)
    assert safe["settings"]["auth_token"] == "…1234"
    assert "flx-AAAABBBBCCCCDDDD1234" not in str(safe)
    # the caller's own structure is untouched
    assert raw["settings"]["auth_token"] == "flx-AAAABBBBCCCCDDDD1234"


def test_redact_settings_text_masks_auth_token():
    from flexrouter.config import redact_settings_text

    secret = "flx-AAAABBBBCCCCDDDD1234"
    text = (
        "settings:\n"
        f"  auth_token: {secret}\n"
        "providers:\n"
        "  groq:\n"
        "    base_url: https://api.groq.com/openai/v1\n"
    )
    out = redact_settings_text(text)
    assert secret not in out
    assert "…1234" in out


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


# --- Shapes a secret can take that a plain text replacement does not catch ---
#
# `redact_settings_text` masks by replacing each secret's *parsed* value in
# the file's own text, which keeps the owner's comments. That only works when
# the parsed value appears verbatim in the source. YAML has several ways to
# write a string whose parsed value does not: a folded block gains a space, a
# double-quoted escape is decoded, a doubled quote collapses. Each of those
# used to put a live key, in full, into a token whose whole purpose is to be
# handed to somebody else. Every one of them must now either come out masked
# or refuse to come out at all.

BACKSLASH = chr(92)


def _settings_with(body: str) -> str:
    return (
        "providers:\n"
        "  groq:\n"
        "    base_url: https://api.groq.com/openai/v1\n"
        f"{body}"
        "\nbuckets:\n"
        "  fast:\n"
        "    - provider: groq\n"
        "      model: llama-3.1-8b-instant\n"
        "      score: 85\n"
        "      rpm: 30\n"
        "      tpm: 6000\n"
    )


def _export(tmp_path, monkeypatch, text):
    """Run the real `config export` against a home holding `text`.

    Returns (exit_code, everything the command emitted, decoded token).
    """
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "home"))
    home.ensure_home()
    home.config_path().write_text(text, encoding="utf-8")
    result = CliRunner().invoke(cli.cli, ["config", "export"])
    emitted = result.output + (result.stderr or "")
    decoded = ""
    if result.exit_code == 0:
        decoded = base64.b64decode(result.output.strip().encode()).decode(
            "utf-8", "replace")
    return result.exit_code, emitted, decoded


def _assert_export_hides(tmp_path, monkeypatch, text, secret):
    """Either the secret comes out masked, or nothing comes out at all.

    Checked against what the *recipient* ends up with: the decoded token read
    back as YAML. Checking the token's raw text alone would pass a folded or
    escaped key straight through, since its characters are all there but
    spelled differently from the value they parse to.
    """
    from flexrouter.config import _every_string

    code, emitted, decoded = _export(tmp_path, monkeypatch, text)
    assert secret not in emitted
    assert secret not in decoded
    if code == 0:
        assert "…" in decoded
        for value in _every_string(yaml.safe_load(decoded)):
            assert secret not in value
    return code, emitted, decoded


def test_export_does_not_leak_a_key_written_as_a_folded_block(
        tmp_path, monkeypatch):
    """A folded block joins its lines with a space, so the parsed value is
    not in the source text and the replacement finds nothing."""
    _assert_export_hides(
        tmp_path, monkeypatch,
        _settings_with("    api_keys:\n      - key: >-\n"
                       "          gsk-LIVE-SECRET-\n          TAIL9999\n"),
        "gsk-LIVE-SECRET- TAIL9999")


def test_export_does_not_leak_a_key_written_with_an_escape(tmp_path, monkeypatch):
    """A double-quoted escape is decoded on parse."""
    _assert_export_hides(
        tmp_path, monkeypatch,
        _settings_with('    api_key: "gsk-LIVE' + BACKSLASH
                       + 'u002DSECRET-7777"\n'),
        "gsk-LIVE-SECRET-7777")


def test_export_does_not_leak_a_key_written_with_a_doubled_quote(
        tmp_path, monkeypatch):
    """Inside single quotes, '' parses down to one quote."""
    _assert_export_hides(
        tmp_path, monkeypatch,
        _settings_with("    api_key: 'gsk-LIVE''SECRET-6666'\n"),
        "gsk-LIVE'SECRET-6666")


def test_export_does_not_leak_a_key_typed_into_the_env_var_slot(
        tmp_path, monkeypatch):
    """`api_keys: <bare string>` is read as an environment variable name when
    a key is being resolved, but it is also exactly where somebody who types
    their key into the wrong place ends up, and the two cannot be told apart.
    Showing a real key can never be taken back; hiding a variable name costs
    nothing, so this position is treated as a secret."""
    code, _, decoded = _assert_export_hides(
        tmp_path, monkeypatch,
        _settings_with("    api_keys: gsk-BARE-5555\n"),
        "gsk-BARE-5555")
    assert code == 0
    assert "…5555" in decoded


def test_export_does_not_leak_a_key_written_in_flow_style(tmp_path, monkeypatch):
    code, _, decoded = _assert_export_hides(
        tmp_path, monkeypatch,
        _settings_with("    api_keys: [{key: gsk-FLOW-1111}]\n"),
        "gsk-FLOW-1111")
    assert code == 0
    assert "…1111" in decoded


def test_export_does_not_leak_a_key_reached_through_an_alias(tmp_path, monkeypatch):
    """The key is written once and pointed at from a second provider."""
    text = (
        "providers:\n"
        "  groq:\n"
        "    base_url: https://api.groq.com/openai/v1\n"
        "    api_key: &shared gsk-ANCHOR-2222\n"
        "  other:\n"
        "    base_url: https://other.test/v1\n"
        "    api_key: *shared\n"
        "\nbuckets:\n  fast:\n    - provider: groq\n"
        "      model: m\n      score: 85\n      rpm: 30\n      tpm: 6000\n"
    )
    _assert_export_hides(tmp_path, monkeypatch, text, "gsk-ANCHOR-2222")


def test_export_does_not_leak_a_key_under_a_provider_block_that_is_an_alias(
        tmp_path, monkeypatch):
    """The whole provider block is reused, so the same key appears twice in
    the parsed structure while being written once."""
    text = (
        "providers:\n"
        "  groq: &prov\n"
        "    base_url: https://api.groq.com/openai/v1\n"
        "    api_keys:\n      - key: gsk-BLOCKALIAS-3333\n"
        "  twin: *prov\n"
        "\nbuckets:\n  fast:\n    - provider: groq\n"
        "      model: m\n      score: 85\n      rpm: 30\n      tpm: 6000\n"
    )
    _assert_export_hides(tmp_path, monkeypatch, text, "gsk-BLOCKALIAS-3333")


def test_export_also_hides_a_key_repeated_inside_a_comment(tmp_path, monkeypatch):
    """A comment is shared along with everything else in the file."""
    text = _settings_with(
        "    # old key was gsk-COMMENT-4444, replaced 2026-01-01\n"
        "    api_keys:\n      - key: gsk-COMMENT-4444\n")
    code, _, decoded = _assert_export_hides(
        tmp_path, monkeypatch, text, "gsk-COMMENT-4444")
    assert code == 0
    assert "old key was …4444" in decoded


def test_export_explains_itself_in_plain_words_instead_of_a_traceback(
        tmp_path, monkeypatch):
    """Refusing is right; refusing with a page of Python is not."""
    code, emitted, _ = _export(
        tmp_path, monkeypatch,
        _settings_with("    api_keys:\n      - key: >-\n"
                       "          gsk-LIVE-SECRET-\n          TAIL9999\n"))
    assert code != 0
    assert "Traceback" not in emitted
    assert "ConfigError" not in emitted
    assert "Nothing was shared." in emitted
    assert "flexrouter keys add" in emitted


def test_export_explains_itself_when_the_file_cannot_be_read_at_all(
        tmp_path, monkeypatch):
    code, emitted, _ = _export(
        tmp_path, monkeypatch, f"providers: [unclosed\n  key: {SECRET}\n")
    assert code != 0
    assert SECRET not in emitted
    assert "Traceback" not in emitted
    assert "Nothing was shared." in emitted
