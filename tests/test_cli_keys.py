import pytest
import yaml
from click.testing import CliRunner

from flexrouter import cli, home
from flexrouter.keys import load_keys


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    home.ensure_home()
    return tmp_path


def test_keys_add_stores_the_key():
    result = CliRunner().invoke(cli.cli, ["keys", "add", "groq", "--secret", "gsk-abcd"])
    assert result.exit_code == 0
    assert load_keys()["groq"][0].secret == "gsk-abcd"


def test_keys_add_never_echoes_the_secret():
    result = CliRunner().invoke(cli.cli, ["keys", "add", "groq", "--secret", "gsk-abcd"])
    assert "gsk-abcd" not in result.output
    assert "…abcd" in result.output


def test_keys_list_shows_masked_values_only():
    CliRunner().invoke(cli.cli, ["keys", "add", "groq", "--secret", "gsk-abcd"])
    result = CliRunner().invoke(cli.cli, ["keys", "list"])
    assert "gsk-abcd" not in result.output
    assert "…abcd" in result.output
    assert "groq-1" in result.output


def test_keys_list_says_so_when_empty():
    result = CliRunner().invoke(cli.cli, ["keys", "list"])
    assert result.exit_code == 0
    assert "no keys" in result.output.lower()


def test_keys_rm_removes_it():
    CliRunner().invoke(cli.cli, ["keys", "add", "groq", "--secret", "gsk-abcd"])
    result = CliRunner().invoke(cli.cli, ["keys", "rm", "groq", "groq-1"])
    assert result.exit_code == 0
    assert load_keys().get("groq") == []


def test_keys_rm_is_clear_when_there_is_no_such_key():
    result = CliRunner().invoke(cli.cli, ["keys", "rm", "groq", "groq-9"])
    assert result.exit_code == 1
    assert "no key" in result.output.lower()


def test_keys_import_lifts_keys_out_of_an_old_settings_file(tmp_path):
    old = tmp_path / "old-flexrouter.yaml"
    old.write_text(yaml.dump({
        "providers": {
            "groq": {"base_url": "https://api.groq.com/openai/v1",
                     "api_keys": ["gsk-one", {"key": "gsk-two"}]},
            "ollama": {"base_url": "http://localhost:11434/v1", "api_keys": []},
        }
    }), encoding="utf-8")

    result = CliRunner().invoke(cli.cli, ["keys", "import", str(old)])
    assert result.exit_code == 0
    assert [r.secret for r in load_keys()["groq"]] == ["gsk-one", "gsk-two"]
    assert "gsk-one" not in result.output


def test_keys_import_skips_env_var_references(tmp_path):
    old = tmp_path / "old-flexrouter.yaml"
    old.write_text(yaml.dump({
        "providers": {"groq": {"base_url": "https://x/v1",
                               "api_keys": [{"env": "GROQ_API_KEY"}]}}
    }), encoding="utf-8")
    CliRunner().invoke(cli.cli, ["keys", "import", str(old)])
    assert load_keys() == {}


def test_keys_import_does_not_modify_the_old_file(tmp_path):
    old = tmp_path / "old-flexrouter.yaml"
    old.write_text("providers:\n  groq:\n    base_url: https://x/v1\n"
                   "    api_keys: [gsk-one]  # my note\n", encoding="utf-8")
    before = old.read_bytes()
    CliRunner().invoke(cli.cli, ["keys", "import", str(old)])
    assert old.read_bytes() == before
