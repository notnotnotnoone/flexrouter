import base64
import json
import os
import webbrowser
from pathlib import Path

import click

from flexrouter import home
from flexrouter.config import load_config
from flexrouter.exceptions import ConfigError
from flexrouter.refresh import refresh_config


@click.group()
def cli():
    """flexrouter — universal LLM router."""


@cli.command()
def init():
    """Interactive terminal wizard to generate flexrouter.yaml."""
    from flexrouter.onboard import run_onboard
    run_onboard()


def _resolve_port(config_path, override):
    """Single port for everything: API, dashboard, and dashboard data.

    Defaults to the config's dashboard_port so existing configs keep working;
    the separate 7353 API port is gone — there is one server now.
    """
    if override is not None:
        return override
    path = Path(config_path) if config_path else home.config_path()
    if path:
        try:
            return load_config(path).port
        except ConfigError:
            pass
    return home.DEFAULT_PORT


def _run_daemon(port, config_path, open_browser):
    import uvicorn
    from flexrouter.app import create_app

    click.echo(f"Settings: {home.config_path()}")

    url = f"http://localhost:{port}"
    click.echo(f"flexrouter running at {url}")
    click.echo(f"  dashboard   {url}")
    click.echo(f"  OpenAI API  {url}/v1")
    click.echo(f"  API docs    {url}/docs")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(config_path), host="127.0.0.1", port=port, log_level="info")


_config_option = click.option(
    "--config", "-c", "config_path", default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Settings file to use. Defaults to the flexrouter home (see: flexrouter doctor).",
)


@cli.command()
@click.option("--port", default=None, type=int,
              help="Port for everything (default: the config's dashboard_port, else 7352)")
@_config_option
def serve(port, config_path):
    """Start the flexrouter server: API and dashboard on one port."""
    _run_daemon(_resolve_port(config_path, port), config_path, open_browser=False)


@cli.command()
@click.option("--port", default=None, type=int, help="Port to serve on")
@_config_option
def dashboard(port, config_path):
    """Start the server and open the dashboard in a browser."""
    _run_daemon(_resolve_port(config_path, port), config_path, open_browser=True)


@cli.command()
def status():
    """Print tier health to terminal."""
    path = home.config_path()
    cfg = load_config(path)
    state = Path(cfg.state_dir) / "health.json"
    if not state.exists():
        click.echo("No health data yet. Run router.generate() first.")
        return
    health = json.loads(state.read_text())
    click.echo(f"Total cost: ${health['total_cost_usd']:.4f}")
    for provider, info in health.get("providers", {}).items():
        click.echo(f"  {provider}: ${info['daily_cost_usd']:.4f} today")


@cli.command()
def refresh():
    """Re-discover models + rate limits and rewrite flexrouter.yaml (with backup)."""
    path = home.config_path()
    cfg = load_config(path)
    aa_key = os.environ.get("AA_API_KEY")
    result = refresh_config(str(path), cfg.state_dir, aa_key=aa_key)
    click.echo(f"Refreshed: +{len(result.added)} added, "
               f"-{len(result.removed)} removed, {len(result.changed)} changed")
    click.echo(f"Backup: {result.backup_path}")
    for err in result.provider_errors:
        click.echo(f"  ! {err['provider']}: {err['error']}", err=True)


@cli.group()
def config():
    """Manage flexrouter configuration."""


@config.command("export")
def config_export():
    """Export config as a base64 token."""
    path = home.config_path()
    token = base64.b64encode(path.read_bytes()).decode()
    click.echo(token)


@config.command("import")
@click.argument("token")
def config_import(token: str):
    """Show settings from a token (it will not overwrite yours)."""
    data = base64.b64decode(token.encode()).decode("utf-8", "replace")
    click.echo("flexrouter never overwrites your settings file. Here it is — "
               f"paste what you want into {home.config_path()}:\n")
    click.echo(data)
