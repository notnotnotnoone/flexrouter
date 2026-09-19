import yaml

from flexrouter import home


def test_flexrouter_home_env_var_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "custom"))
    assert home.home_dir() == tmp_path / "custom"


def test_falls_back_to_localappdata_on_windows(tmp_path, monkeypatch):
    monkeypatch.delenv("FLEXROUTER_HOME", raising=False)
    monkeypatch.setattr(home.os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert home.home_dir() == tmp_path / "Local" / "flexrouter"


def test_falls_back_to_config_dir_elsewhere(tmp_path, monkeypatch):
    monkeypatch.delenv("FLEXROUTER_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(home.os, "name", "posix")
    monkeypatch.setattr(home.Path, "home", staticmethod(lambda: tmp_path))
    assert home.home_dir() == tmp_path / ".config" / "flexrouter"


def test_paths_all_sit_under_the_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    assert home.config_path() == tmp_path / "config.yaml"
    assert home.keys_path() == tmp_path / "keys.json"
    assert home.overrides_path() == tmp_path / "overrides.json"
    assert home.state_dir() == tmp_path / "state"


def test_ensure_home_creates_dirs_and_a_starter_config(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "h"))
    home.ensure_home()
    assert (tmp_path / "h" / "state").is_dir()
    raw = yaml.safe_load((tmp_path / "h" / "config.yaml").read_text(encoding="utf-8"))
    assert raw["settings"]["port"] == 4891
    assert raw["providers"] == {}
    assert set(raw["buckets"]) == {"smart", "fast", "long"}
    assert all(v == [] for v in raw["buckets"].values())


def test_ensure_home_never_overwrites_an_existing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "h"))
    home.ensure_home()
    cfg = tmp_path / "h" / "config.yaml"
    cfg.write_text("# my own notes\nsettings: {port: 9999}\n", encoding="utf-8")
    home.ensure_home()
    assert "# my own notes" in cfg.read_text(encoding="utf-8")


def test_starter_config_keeps_its_comments(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "h"))
    home.ensure_home()
    assert "#" in (tmp_path / "h" / "config.yaml").read_text(encoding="utf-8")
