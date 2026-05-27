import base64
import json
import webbrowser
import click
import yaml
from pathlib import Path

from flexrouter.config import discover_config, load_config
from flexrouter.exceptions import ConfigError


@click.group()
def cli():
    """flexrouter — universal LLM router."""


@cli.command()
def init():
    """Open browser setup wizard to generate flexrouter.yaml."""
    from flexrouter.dashboard.server import start_server
    port = 7352
    click.echo(f"Starting setup wizard at http://localhost:{port}/#setup")
    webbrowser.open(f"http://localhost:{port}/#setup")
    start_server(port=port, open_tab=False)


@cli.command()
def dashboard():
    """Start the live dashboard at http://localhost:<port>."""
    path = discover_config()
    port = 7352
    if path:
        try:
            cfg = load_config(path)
            port = cfg.dashboard_port
        except ConfigError:
            pass
    from flexrouter.dashboard.server import start_server
    click.echo(f"Dashboard running at http://localhost:{port}")
    webbrowser.open(f"http://localhost:{port}")
    start_server(port=port, open_tab=False)


@cli.command()
def status():
    """Print tier health to terminal."""
    path = discover_config()
    if not path:
        click.echo("No flexrouter.yaml found.", err=True)
        raise SystemExit(1)
    cfg = load_config(path)
    state = Path(cfg.state_dir) / "health.json"
    if not state.exists():
        click.echo("No health data yet. Run router.generate() first.")
        return
    health = json.loads(state.read_text())
    click.echo(f"Total cost: ${health['total_cost_usd']:.4f}")
    for provider, info in health.get("providers", {}).items():
        click.echo(f"  {provider}: ${info['daily_cost_usd']:.4f} today")


@cli.group()
def config():
    """Manage flexrouter configuration."""


@config.command("export")
def config_export():
    """Export config as a base64 token."""
    path = discover_config()
    if not path:
        click.echo("No flexrouter.yaml found.", err=True)
        raise SystemExit(1)
    token = base64.b64encode(path.read_bytes()).decode()
    click.echo(token)


@config.command("import")
@click.argument("token")
def config_import(token: str):
    """Import config from a base64 token."""
    data = base64.b64decode(token.encode())
    out = Path("flexrouter.yaml")
    out.write_bytes(data)
    click.echo(f"Config written to {out}")
