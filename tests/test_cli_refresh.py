from click.testing import CliRunner
import flexrouter.cli as cli
from flexrouter.refresh import RefreshResult


def test_refresh_prints_summary(monkeypatch, tmp_path):
    cfg = tmp_path / "flexrouter.yaml"
    cfg.write_text("providers: {}\ntiers: {}\nsettings:\n  state_dir: .flexrouter\n")
    monkeypatch.setattr(cli, "discover_config", lambda: cfg)
    fake = RefreshResult(timestamp="2026-06-19T12:00:00+00:00", added=["groq/a"], removed=[],
                         changed=[], backup_path="/b.yaml", provider_errors=[{"provider": "x", "error": "boom"}])
    monkeypatch.setattr(cli, "refresh_config", lambda *a, **k: fake, raising=False)
    result = CliRunner().invoke(cli.cli, ["refresh"])
    assert result.exit_code == 0
    assert "+1" in result.output
    assert "boom" in result.output
