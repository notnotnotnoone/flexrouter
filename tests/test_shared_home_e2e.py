# tests/test_shared_home_e2e.py
import os

import pytest
import yaml
from click.testing import CliRunner

from flexrouter import cli, home
from flexrouter.config import load_config

SETTINGS = {
    "settings": {"port": 4891},
    "providers": {"groq": {"base_url": "https://api.groq.com/openai/v1"}},
    "buckets": {"fast": [{"provider": "groq", "model": "llama-3.3-70b-versatile",
                          "score": 90, "rpm": 30, "tpm": 6000}]},
}


@pytest.fixture
def shared_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "home"))
    home.ensure_home()
    home.config_path().write_text(yaml.dump(SETTINGS), encoding="utf-8")
    CliRunner().invoke(cli.cli, ["keys", "add", "groq", "--secret", "gsk-shared"])
    return tmp_path


def test_two_different_projects_see_the_same_settings_and_key(shared_home, monkeypatch):
    project_a = shared_home / "project-a"
    project_b = shared_home / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    # A stray settings file in one project must now be ignored entirely.
    (project_a / "flexrouter.yaml").write_text("settings: {port: 1}\n", encoding="utf-8")

    monkeypatch.chdir(project_a)
    from_a = load_config()
    monkeypatch.chdir(project_b)
    from_b = load_config()

    assert from_a.port == from_b.port == 4891
    assert from_a.providers["groq"].api_keys == ["gsk-shared"]
    assert from_b.providers["groq"].api_keys == ["gsk-shared"]


def test_the_settings_file_is_byte_identical_after_a_full_run(shared_home):
    before = home.config_path().read_bytes()
    load_config()
    CliRunner().invoke(cli.cli, ["doctor"])
    CliRunner().invoke(cli.cli, ["keys", "list"])
    assert home.config_path().read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_key_file_is_not_world_readable(shared_home):
    assert os.stat(home.keys_path()).st_mode & 0o777 == 0o600
