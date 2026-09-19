import pytest
import yaml
from click.testing import CliRunner

from flexrouter import cli, home

SETTINGS = {
    "settings": {"port": 4891},
    "providers": {
        "openrouter": {"base_url": "https://openrouter.ai/api/v1"},
        "groq": {"base_url": "https://api.groq.com/openai/v1",
                 "api_key_env": "GROQ_API_KEY"},
        "cerebras": {"base_url": "https://api.cerebras.ai/v1"},
    },
    "buckets": {"smart": [{"provider": "openrouter", "model": "deepseek-chat",
                           "score": 90, "rpm": 20, "tpm": 10000}]},
}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    home.ensure_home()
    home.config_path().write_text(yaml.dump(SETTINGS), encoding="utf-8")
    return tmp_path


def test_doctor_prints_the_home(_home):
    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert result.exit_code == 0
    assert str(_home) in result.output


def test_doctor_says_where_each_key_came_from(monkeypatch):
    from flexrouter.keys import add_key
    add_key("openrouter", "sk-or-abcd")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-env")

    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert "openrouter" in result.output
    assert "…abcd" in result.output
    assert "GROQ_API_KEY" in result.output
    assert "no key" in result.output.lower()  # cerebras


def test_doctor_never_prints_a_secret():
    from flexrouter.keys import add_key
    add_key("openrouter", "sk-or-abcd")
    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert "sk-or-abcd" not in result.output


def test_doctor_counts_buckets_and_models():
    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert "1 bucket" in result.output
    assert "1 model" in result.output


def test_doctor_reports_unreadable_settings(_home):
    home.config_path().write_text("settings: [this: is: not: valid\n", encoding="utf-8")
    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert result.exit_code == 1
    assert "could not read" in result.output.lower()


def test_doctor_lists_overrides():
    from flexrouter.overrides import set_override
    set_override("models", "openrouter/deepseek-chat", "score", 42)
    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert "openrouter/deepseek-chat" in result.output


def test_doctor_never_leaks_a_secret_from_a_malformed_settings_file(_home):
    # Same shape of failure as CRITICAL #1: a syntax error next to an inline
    # api_key makes PyYAML embed the plaintext secret in its own error text.
    # doctor's combined stdout+stderr output must never contain it.
    home.config_path().write_text(
        "providers:\n"
        "  openrouter:\n"
        "    api_key: sk-or-v1: SUPERSECRET1234\n"
        "    base_url: https://openrouter.ai/api/v1\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert result.exit_code == 1
    assert "SUPERSECRET1234" not in result.output
    assert "sk-or-v1" not in result.output
    assert "could not read" in result.output.lower()


def test_doctor_reports_inline_key_and_the_deprecation_warning(_home):
    settings = {
        "settings": SETTINGS["settings"],
        "providers": {
            **SETTINGS["providers"],
            "cerebras": {"base_url": "https://api.cerebras.ai/v1",
                         "api_key": "sk-cerebras-inline-secret"},
        },
        "buckets": SETTINGS["buckets"],
    }
    home.config_path().write_text(yaml.dump(settings), encoding="utf-8")

    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert result.exit_code == 0
    assert "sk-cerebras-inline-secret" not in result.output
    assert "typed into" in result.output
    assert "flexrouter keys add cerebras" in result.output
    # The deprecation-warning passthrough at the bottom of doctor.
    assert "move it with: flexrouter keys add cerebras" in result.output.lower()


def test_doctor_reports_a_brand_new_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "brand-new-home"))
    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert result.exit_code == 0
    assert "brand-new" in result.output.lower()
    assert "flexrouter keys add" in result.output


def test_doctor_reports_disabled_keys_honestly(_home):
    from flexrouter.keys import add_key, load_keys, save_keys

    add_key("cerebras", "sk-cerebras-disabled-secret")
    vault = load_keys()
    vault["cerebras"][0].enabled = False
    save_keys(vault)

    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert result.exit_code == 0
    assert "sk-cerebras-disabled-secret" not in result.output
    assert "cerebras" in result.output
    assert "disabled" in result.output.lower()
    assert "1" in result.output


def test_doctor_distinguishes_missing_field_from_unreadable(_home):
    bad = {
        "settings": SETTINGS["settings"],
        "providers": {"openrouter": {}},  # missing base_url
        "buckets": SETTINGS["buckets"],
    }
    home.config_path().write_text(yaml.dump(bad), encoding="utf-8")

    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert result.exit_code == 1
    assert "could not read" not in result.output.lower()
    assert "missing" in result.output.lower()
    assert "base_url" in result.output
