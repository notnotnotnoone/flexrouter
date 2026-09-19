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

    Defaults to the config's port (accepting a legacy dashboard_port in the
    config file as an alias) so existing configs keep working; the separate
    7353 API port is gone — there is one server now.
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


from flexrouter import keys as keyvault


@cli.group()
def keys():
    """Add, list, and remove your API keys."""


@keys.command("list")
def keys_list():
    """Show your saved keys (masked — the full value is never printed)."""
    vault = keyvault.load_keys()
    if not any(vault.values()):
        click.echo("No keys saved yet. Add one with: flexrouter keys add <provider>")
        return
    for provider, records in sorted(vault.items()):
        click.echo(provider)
        for r in records:
            state = "" if r.enabled else "  (off)"
            label = f"  {r.label}" if r.label else ""
            click.echo(f"  {r.id:<16} {keyvault.mask(r.secret)}{label}{state}")


@keys.command("add")
@click.argument("provider")
@click.option("--secret", prompt=True, hide_input=True,
              help="The key itself. Leave it off and you'll be asked without it showing.")
@click.option("--label", default="", help="A name to recognise it by.")
def keys_add(provider: str, secret: str, label: str):
    """Save a key for PROVIDER."""
    record = keyvault.add_key(provider, secret.strip(), label=label)
    click.echo(f"Saved {record.id} for {provider}: {keyvault.mask(record.secret)}")


@keys.command("rm")
@click.argument("provider")
@click.argument("key_id")
def keys_rm(provider: str, key_id: str):
    """Remove a saved key."""
    if not keyvault.remove_key(provider, key_id):
        click.echo(f"No key {key_id!r} for {provider}.", err=True)
        raise SystemExit(1)
    click.echo(f"Removed {key_id} from {provider}.")


def _scan_old_settings(path: Path) -> tuple[list[tuple[str, str]], bool]:
    """Parse an old settings file and lift its plain secrets in. Env
    references are left alone — they already resolve. The old file is
    never modified. Returns (added, saw_env_reference) so callers can
    tell "nothing importable here" apart from "only env references here"."""
    import yaml

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    added: list[tuple[str, str]] = []
    saw_env = False
    for provider, praw in (raw.get("providers") or {}).items():
        if not isinstance(praw, dict):
            continue
        secrets: list[str] = []
        if praw.get("api_key"):
            secrets.append(str(praw["api_key"]))
        entries = praw.get("api_keys", [])
        if isinstance(entries, str):
            entries = []
        for e in entries or []:
            if isinstance(e, str) and e:
                secrets.append(e)
            elif isinstance(e, dict) and e.get("key"):
                secrets.append(str(e["key"]))
            elif isinstance(e, dict) and e.get("env"):
                saw_env = True
        for s in secrets:
            added.append((provider, keyvault.add_key(provider, s, label="imported").id))
    return added, saw_env


def _import_keys_from(path: Path) -> list[tuple[str, str]]:
    """Lift plain secrets out of an old settings file. Env references are
    left alone — they already resolve. The old file is never modified."""
    added, _ = _scan_old_settings(path)
    return added


@keys.command("import")
@click.argument("old_file", type=click.Path(exists=True, dir_okay=False))
def keys_import(old_file: str):
    """Copy the keys out of an old flexrouter.yaml into the shared home."""
    added, saw_env = _scan_old_settings(Path(old_file))
    if not added:
        if saw_env:
            click.echo("Found no keys to copy — that file only references "
                       "environment variables, which already work as they are.")
        else:
            click.echo("Found no keys to copy — nothing importable was found "
                       "in that file.")
        return
    for provider, key_id in added:
        click.echo(f"Copied {provider} -> {key_id}")
    click.echo(f"\nCopied {len(added)} key(s). Your old file was not changed; "
               f"delete it when you're happy.")


@cli.command()
def doctor():
    """Show where flexrouter keeps things and which key it will use."""
    import os
    import warnings

    from flexrouter import overrides as ov

    home.ensure_home()
    click.echo(f"flexrouter home: {home.home_dir()}")
    click.echo(f"  settings   {home.config_path().name}")
    click.echo(f"  keys       {home.keys_path().name}")
    click.echo(f"  changes    {home.overrides_path().name}")
    click.echo(f"  records    {home.state_dir().name}{os.sep}")
    click.echo("")

    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            cfg = load_config()
    except Exception as e:
        click.echo(f"Could not read your settings: {e}", err=True)
        raise SystemExit(1)

    models = sum(len(v) for v in cfg.tiers.values())
    click.echo(f"{len(cfg.tiers)} bucket(s), {models} model(s), "
               f"{len(cfg.providers)} provider(s). Serving on port {cfg.port}.")
    click.echo("")

    click.echo("Which key each provider will use:")
    for name, provider in sorted(cfg.providers.items()):
        if not provider.keys:
            click.echo(f"  {name:<14} no key found")
            continue
        first = provider.keys[0]
        if first.source == "env":
            where = f"{first.label} (environment)"
        elif first.source == "inline":
            where = (f"typed into {home.config_path().name} — move it with: "
                     f"flexrouter keys add {name}")
        else:
            where = f"saved key {first.id}  {keyvault.mask(first.secret)}"
        extra = f"  (+{len(provider.keys) - 1} more)" if len(provider.keys) > 1 else ""
        click.echo(f"  {name:<14} {where}{extra}")

    changes = ov.load_overrides()
    click.echo("")
    if not changes:
        click.echo("No dashboard changes on top of your settings file.")
    else:
        click.echo("Changes layered on top of your settings file:")
        for section in ov.SECTIONS:
            for key, value in (changes.get(section) or {}).items():
                click.echo(f"  {key}: {value}")

    for w in caught:
        click.echo(f"\n! {w.message}", err=True)
