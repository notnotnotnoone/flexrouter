import csv
import io
import json

import pytest
import yaml
from click.testing import CliRunner

from flexrouter import cli, home
from flexrouter.keys import add_key, load_keys
from flexrouter.tui import facts, render

SETTINGS = {
    "settings": {"port": 4891},
    "providers": {
        "groq": {"base_url": "https://api.groq.com/openai/v1"},
        "cerebras": {"base_url": "https://api.cerebras.ai/v1"},
    },
    "buckets": {"smart": [{"provider": "groq", "model": "llama-3.1-8b-instant",
                           "score": 90, "rpm": 20, "tpm": 10000}]},
}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    home.ensure_home()
    home.config_path().write_text(yaml.dump(SETTINGS), encoding="utf-8")
    return tmp_path


def _render_text(renderable) -> str:
    """A rich renderable, flattened to plain text (as a pipe would see it)."""
    buf = io.StringIO()
    from rich.console import Console
    Console(file=buf, width=200).print(renderable)
    return buf.getvalue()


# --- facts: overview -------------------------------------------------------

def test_overview_counts_buckets_models_and_providers():
    ov = facts.overview()
    assert ov.loaded
    assert ov.bucket_count == 1
    assert ov.model_count == 1
    assert {p.name for p in ov.providers} == {"groq", "cerebras"}


def test_overview_reads_spend_from_health_json():
    (home.state_dir() / "health.json").write_text(json.dumps({
        "total_cost_usd": 0.25,
        "providers": {"groq": {"daily_cost_usd": 0.1}},
    }), encoding="utf-8")
    ov = facts.overview()
    assert ov.health_present
    assert ov.total_cost_usd == pytest.approx(0.25)
    groq = next(p for p in ov.providers if p.name == "groq")
    assert groq.daily_cost_usd == pytest.approx(0.1)


def test_overview_reports_an_unreadable_settings_file():
    home.config_path().write_text("settings: [this: is: not: valid\n", encoding="utf-8")
    ov = facts.overview()
    assert not ov.loaded
    assert ov.config_error


def test_overview_is_fine_with_no_health_data():
    ov = facts.overview()
    assert ov.loaded
    assert not ov.health_present
    assert ov.total_cost_usd == 0.0


# --- facts: providers and keys --------------------------------------------

def test_providers_and_keys_lists_with_and_without_a_key():
    add_key("groq", "gsk-abcd")
    rows = {r.name: r for r in facts.providers_and_keys()}
    assert rows["groq"].has_any_key
    assert [r.id for r in rows["groq"].saved] == ["groq-1"]
    # cerebras is declared in settings but has no key at all.
    assert rows["cerebras"].in_settings
    assert not rows["cerebras"].has_any_key


def test_providers_and_keys_includes_a_provider_only_in_keys_json():
    add_key("openrouter", "sk-or-xyz")
    rows = {r.name: r for r in facts.providers_and_keys()}
    assert "openrouter" in rows
    assert rows["openrouter"].in_settings is False
    assert rows["openrouter"].has_any_key


# --- facts: requests -------------------------------------------------------

def _write_audit(*rows: list[str]) -> None:
    path = home.state_dir() / "audit.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "tier", "provider", "model", "prompt_tokens",
                         "completion_tokens", "cost_usd", "latency_ms", "status",
                         "request_id"])
        writer.writerows(rows)


def test_recent_requests_reads_the_audit_log_newest_first():
    _write_audit(
        ["2026-05-27T12:00:00+00:00", "low", "groq", "llama-3.1-8b-instant",
         "10", "5", "0.0001", "280", "ok", "r1"],
        ["2026-05-27T12:01:00+00:00", "low", "cerebras", "gpt-oss-120b",
         "20", "8", "0.0002", "310", "fail", "r2"],
    )
    rows = facts.recent_requests()
    assert [r.provider for r in rows] == ["cerebras", "groq"]
    assert rows[0].prompt_tokens == 20
    assert rows[0].status == "fail"


def test_recent_requests_skips_a_torn_row():
    _write_audit(
        ["2026-05-27T12:00:00", "low", "groq", "m", "10", "5", "0.0", "280", "ok", "r1"],
        ["2026-05-27T12:01:00", "low", "groq", "m", "abc", "5", "0.0", "280", "ok", "r2"],
    )
    rows = facts.recent_requests()
    assert len(rows) == 1
    assert rows[0].provider == "groq"


def test_recent_requests_is_empty_when_nothing_was_logged():
    assert facts.recent_requests() == []


# --- facts: doctor ---------------------------------------------------------

def test_doctor_report_lists_paths_and_key_sources():
    add_key("groq", "gsk-abcd")
    report = facts.doctor_report()
    assert report.error is None
    assert report.home_dir == str(home.home_dir())
    assert report.bucket_count == 1
    assert report.provider_count == 2
    sources = {s.name: s for s in report.key_sources}
    assert sources["groq"].masked == "…abcd"
    assert "no key" in sources["cerebras"].where.lower()


def test_doctor_report_flags_an_unreadable_settings_file():
    home.config_path().write_text("settings: [bad: yaml\n", encoding="utf-8")
    report = facts.doctor_report()
    assert report.error
    assert report.error_is_missing_field is False


# --- render: secrets never reach the screen --------------------------------

def test_keys_table_never_print_the_secret():
    add_key("groq", "gsk-super-secret")
    text = _render_text(render.keys_table(load_keys()))
    assert "gsk-super-secret" not in text
    assert "…cret" in text
    assert "groq-1" in text


# --- CLI: keys add --list --------------------------------------------------

def test_keys_add_list_shows_providers_with_and_without_keys():
    add_key("groq", "gsk-abcd")
    result = CliRunner().invoke(cli.cli, ["keys", "add", "--list"])
    assert result.exit_code == 0
    assert "groq" in result.output
    assert "cerebras" in result.output
    assert "…abcd" in result.output
    assert "no key" in result.output.lower()


def test_keys_add_list_adds_nothing():
    result = CliRunner().invoke(cli.cli, ["keys", "add", "--list"])
    assert result.exit_code == 0
    assert load_keys() == {}


def test_keys_add_without_a_provider_asks_for_one():
    result = CliRunner().invoke(cli.cli, ["keys", "add"])
    assert result.exit_code != 0
    assert "provider" in result.output.lower()
    assert load_keys() == {}


# --- CLI: piped output stays a sane width --------------------------------
# The non-terminal console is deliberately very wide so nothing wraps; a panel
# that expanded to that width would draw a border thousands of columns long.

def test_status_stays_a_sane_width_when_piped():
    result = CliRunner().invoke(cli.cli, ["status"])
    assert result.exit_code == 0
    assert max(len(line) for line in result.output.splitlines()) < 200


def test_doctor_stays_a_sane_width_when_piped():
    add_key("groq", "gsk-abcd")
    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert result.exit_code == 0
    assert max(len(line) for line in result.output.splitlines()) < 200


# --- TUI: it mounts, and a key can be added from the form ------------------

async def test_tui_mounts_all_four_views():
    pytest.importorskip("textual")
    from textual.widgets import DataTable

    from flexrouter.tui.app import FlexRouterApp

    app = FlexRouterApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        for table_id in ("#overview-table", "#keys-table", "#requests-table"):
            assert app.query_one(table_id, DataTable)
        assert app.query_one("#doctor-body")


async def test_tui_adds_a_key_through_the_form():
    pytest.importorskip("textual")
    from textual.widgets import Button, Input

    from flexrouter.tui.app import FlexRouterApp

    app = FlexRouterApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_add_key()
        await pilot.pause()
        app.screen.query_one("#provider-input", Input).value = "groq"
        app.screen.query_one("#secret-input", Input).value = "gsk-typed"
        app.screen.query_one("#save", Button).press()
        await pilot.pause()
    assert load_keys()["groq"][0].secret == "gsk-typed"
