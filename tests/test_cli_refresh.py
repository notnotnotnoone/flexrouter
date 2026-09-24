from click.testing import CliRunner
import flexrouter.cli as cli
from flexrouter.refresh import RefreshResult


def test_refresh_prints_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "home"))
    cfg = tmp_path / "flexrouter.yaml"
    cfg.write_text("providers: {}\ntiers: {}\nsettings:\n"
                   "  state_dir: .flexrouter\n"
                   "  experimental_model_discovery: true\n")
    monkeypatch.setattr(cli.home, "config_path", lambda: cfg)
    fake = RefreshResult(timestamp="2026-06-19T12:00:00+00:00", added=["groq/a"], removed=[],
                         changed=[], provider_errors=[{"provider": "x", "error": "boom"}],
                         pending_path=str(tmp_path / "st" / "catalog_pending.json"))
    monkeypatch.setattr(cli, "refresh_config", lambda *a, **k: fake, raising=False)
    result = CliRunner().invoke(cli.cli, ["refresh"])
    assert result.exit_code == 0
    assert "1 new model" in result.output
    assert "boom" in result.output
    assert "Nothing has been changed" in result.output
    assert str(tmp_path / "st" / "catalog_pending.json") in result.output


def test_refresh_is_a_no_op_when_discovery_is_off(monkeypatch, tmp_path):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "home"))
    cfg = tmp_path / "flexrouter.yaml"
    cfg.write_text("providers: {}\ntiers: {}\nsettings:\n  state_dir: .flexrouter\n")
    monkeypatch.setattr(cli.home, "config_path", lambda: cfg)

    called = []
    monkeypatch.setattr(cli, "refresh_config", lambda *a, **k: called.append(1), raising=False)

    result = CliRunner().invoke(cli.cli, ["refresh"])
    assert result.exit_code == 0
    assert not called
    assert "Model discovery is off" in result.output
    assert "experimental_model_discovery" in result.output
