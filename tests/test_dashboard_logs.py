"""The Logs page: only there with --log, and tailing the activity log file."""
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from flexrouter import log_setup
from flexrouter.app import create_app

LINE_1 = "2026-09-23 10:00:00 INFO flexrouter.app: started"  # no millis, as written
LINE_2 = "2026-09-23 10:00:01,200 WARNING flexrouter._router: provider slow"
LINE_3 = '2026-09-23 10:00:02,300 INFO uvicorn.access: "GET /logs HTTP/1.1" 200'


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


@pytest.fixture(autouse=True)
def _logging_off_afterwards():
    yield
    log_setup.disable()


def _enable_with_lines(tmp_path, lines=(LINE_1, LINE_2, LINE_3)):
    path = log_setup.enable(tmp_path / "state")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_the_page_is_absent_without_the_flag(client):
    assert client.get("/logs").status_code == 404
    assert "--log" in client.get("/logs").text
    assert 'href="/logs"' not in client.get("/").text


def test_the_page_tails_the_file_newest_first(client, tmp_path):
    _enable_with_lines(tmp_path)
    body = client.get("/logs").text
    assert body.count("log-row") >= 3
    assert body.index("provider slow") < body.index("started")
    assert 'href="/logs"' in client.get("/").text


def test_levels_are_marked_and_odd_lines_survive(client, tmp_path):
    _enable_with_lines(tmp_path, lines=(LINE_2, "Traceback (most recent call last):"))
    body = client.get("/logs").text
    assert 'data-level="WARNING"' in body
    assert "Traceback (most recent call last):" in body


def test_the_fragment_is_just_the_live_block(client, tmp_path):
    _enable_with_lines(tmp_path)
    body = client.get("/logs?fragment=1").text
    assert body.startswith('<div id="log-rows"')
    assert "<!doctype" not in body


def test_an_empty_log_says_so(client, tmp_path):
    log_setup.enable(tmp_path / "state")
    assert "Nothing logged yet" in client.get("/logs").text


def test_the_uvicorn_config_shares_one_file_handler(tmp_path):
    log_setup.enable(tmp_path / "state")
    config = log_setup.uvicorn_log_config()
    handler = config["handlers"]["flexrouter_file"]
    assert handler["maxBytes"] == log_setup.MAX_BYTES
    assert handler["backupCount"] == log_setup.BACKUPS
    assert str(tmp_path / "state" / "flexrouter.log") == handler["filename"]
    assert "flexrouter_file" in config["loggers"]["uvicorn"]["handlers"]
    assert "flexrouter_file" in config["loggers"]["uvicorn.access"]["handlers"]
    assert config["loggers"]["flexrouter"]["handlers"] == ["flexrouter_file"]


def test_tail_reads_the_last_lines(tmp_path):
    log_setup.enable(tmp_path / "state")
    path = log_setup.log_path()
    path.write_text("".join(f"line {i}\n" for i in range(500)), encoding="utf-8")
    tail = log_setup.tail(3)
    assert tail == ["line 497", "line 498", "line 499"]


@pytest.mark.parametrize("command", ["dashboard", "serve"])
def test_the_cli_flag_wires_logging_into_the_server(command, config_file, monkeypatch):
    import uvicorn
    import webbrowser

    captured = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(kw))
    monkeypatch.setattr(webbrowser, "open", lambda url: None)
    from flexrouter.cli import cli

    result = CliRunner().invoke(cli, [command, "--config", str(config_file), "--log"])
    assert result.exit_code == 0, result.output
    assert log_setup.enabled()
    assert log_setup.log_path().name == "flexrouter.log"
    assert "log_config" in captured
    assert "Logging to" in result.output


def test_without_the_flag_nothing_is_wired(config_file, monkeypatch):
    import uvicorn

    captured = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(kw))
    from flexrouter.cli import cli

    result = CliRunner().invoke(cli, ["serve", "--config", str(config_file)])
    assert result.exit_code == 0, result.output
    assert not log_setup.enabled()
    assert "log_config" not in captured
