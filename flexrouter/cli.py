import base64
import webbrowser
from pathlib import Path

import click

from flexrouter import home
from flexrouter import service_keys
from flexrouter.config import load_config, redact_settings_text
from flexrouter.exceptions import ConfigError
from flexrouter.refresh import refresh_config
from flexrouter.tui import facts as tui_facts
from flexrouter.tui import render as tui_render


@click.group()
def cli():
    """flexrouter — universal LLM router."""


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

    tui_render.console().print(tui_render.banner(port, str(home.config_path())))

    url = f"http://localhost:{port}"
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
              help="Port for everything (default: the settings file's port, else 4891)")
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
@_config_option
def tui(config_path):
    """Open the live TUI: overview, keys, requests, and paths."""
    # Imported here, not at module load: textual is only needed when someone
    # actually opens the TUI, and the ordinary commands should stay light.
    from flexrouter.tui.app import FlexRouterApp

    FlexRouterApp(config_path).run()


@cli.command()
def status():
    """Show the router's totals and today's spend."""
    tui_render.console().print(tui_render.status_panel(tui_facts.overview()))


@cli.command()
def refresh():
    """Check your providers for model and limit changes. Nothing is changed."""
    path = home.config_path()
    cfg = load_config(path)
    aa_key = service_keys.resolve("aa")
    result = refresh_config(str(path), cfg.state_dir, aa_key=aa_key)
    out = tui_render.console()
    out.print(
        f"Checked your providers: found [bold]{len(result.added)}[/bold] new model(s), "
        f"[bold]{len(result.removed)}[/bold] that are gone, and "
        f"[bold]{len(result.changed)}[/bold] with different limits."
    )
    out.print("Nothing has been changed. Your settings file is exactly as you left it.")
    out.print(f"What was found is saved here: {tui_render.esc(result.pending_path)}")
    for err in result.provider_errors:
        click.echo(f"  ! {err['provider']}: {err['error']}", err=True)


@cli.group()
def config():
    """Manage flexrouter configuration."""


@config.command("export")
def config_export():
    """Print a shareable copy of your settings. Any key in it is hidden."""
    path = home.config_path()
    try:
        text = redact_settings_text(path.read_text(encoding="utf-8"))
    except ConfigError:
        # Every other command explains itself in one plain sentence when it
        # gives up. A refused export must do the same, not print a page of
        # Python at someone who does not write it.
        click.echo(
            "Nothing was shared. A copy is only safe to pass on once every "
            "key inside it has been hidden, and this time that could not be "
            "done — either your settings file has a mistake in it, or a key "
            "in it is written in a form that cannot be hidden reliably.",
            err=True)
        click.echo("", err=True)
        click.echo("Two things to try:", err=True)
        click.echo("  1. Check the file for mistakes: flexrouter doctor",
                   err=True)
        click.echo(
            f"  2. Take any key out of {path} and add it back with: "
            f"flexrouter keys add <provider>. Your keys go on working "
            f"exactly as before — they just move somewhere that is never "
            f"shared.", err=True)
        raise SystemExit(1)
    click.echo(base64.b64encode(text.encode("utf-8")).decode())


@config.command("reset")
@click.argument("section", required=False,
                type=click.Choice(["settings", "providers", "models"]))
@click.argument("name", required=False)
def config_reset(section: str | None, name: str | None):
    """Undo dashboard changes. With no arguments, undo all of them.

    Your own settings file is never involved — this only clears what the
    dashboard saved on top of it. Use it when a change made flexrouter
    unable to read your settings.
    """
    from flexrouter import overrides as ov

    if section is None:
        changes = ov.load_overrides()
        if not changes:
            click.echo("There were no dashboard changes to undo.")
            return
        ov.save_overrides({})
        click.echo("Undid every dashboard change. Your settings file is "
                   "untouched, as always.")
        return

    if name is None:
        changes = ov.load_overrides()
        if not changes.get(section):
            click.echo(f"There were no {section} changes to undo.")
            return
        changes.pop(section)
        ov.save_overrides(changes)
        click.echo(f"Undid every {section} change.")
        return

    if not ov.clear_override(section, name):
        click.echo(f"There was no {section} change for {name!r} to undo.",
                   err=True)
        raise SystemExit(1)
    click.echo(f"Undid the {section} change for {name}.")


@config.command("import")
@click.argument("token")
def config_import(token: str):
    """Show settings from a token (it will not overwrite yours)."""
    data = base64.b64decode(token.encode()).decode("utf-8", "replace")
    click.echo("flexrouter never overwrites your settings file. Here it is — "
               f"paste what you want into {home.config_path()}:\n")
    click.echo(data)
    click.echo("\nA key is never included in an export. Anywhere you see "
               "… followed by four characters, that is a key that was "
               "hidden on the way out — add your own with: "
               "flexrouter keys add <provider>")


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
    tui_render.console().print(tui_render.keys_table(vault))


@keys.command("add")
@click.argument("provider", required=False)
@click.option("--secret", default=None,
              help="The key itself. Leave it off and you'll be asked without it showing.")
@click.option("--label", default="", help="A name to recognise it by.")
@click.option("--list", "-l", "show_providers", is_flag=True,
              help="Show every provider, with and without a key saved. Adds nothing.")
def keys_add(provider: str | None, secret: str | None, label: str,
             show_providers: bool):
    """Save a key for PROVIDER.

    With --list, shows every provider you have added a key for and every one
    you have not, and saves nothing.
    """
    if show_providers:
        tui_render.console().print(
            tui_render.providers_table(tui_facts.providers_and_keys()))
        return
    if not provider:
        raise click.UsageError(
            "Name the provider to save a key for: flexrouter keys add <provider>. "
            "See them all with: flexrouter keys add --list")
    # Prompted here rather than by the option's own prompt=, so that --list
    # never stops to ask for a secret it is not going to save.
    if secret is None:
        secret = click.prompt("Secret", hide_input=True)
    record = keyvault.add_key(provider, secret.strip(), label=label)
    tui_render.console().print(
        f"Saved [bold]{tui_render.esc(record.id)}[/bold] for "
        f"{tui_render.esc(provider)}: {tui_render.esc(keyvault.mask(record.secret))}")


@keys.command("rm")
@click.argument("provider")
@click.argument("key_id")
def keys_rm(provider: str, key_id: str):
    """Remove a saved key."""
    if not keyvault.remove_key(provider, key_id):
        click.echo(f"No key {key_id!r} for {provider}.", err=True)
        raise SystemExit(1)
    tui_render.console().print(
        f"Removed [bold]{tui_render.esc(key_id)}[/bold] from {tui_render.esc(provider)}.")


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
    out = tui_render.console()
    if not added:
        if saw_env:
            out.print("Found no keys to copy — that file only references "
                      "environment variables, which already work as they are.")
        else:
            out.print("Found no keys to copy — nothing importable was found "
                      "in that file.")
        return
    for provider, key_id in added:
        out.print(f"Copied {tui_render.esc(provider)} -> {tui_render.esc(key_id)}")
    out.print(f"\nCopied {len(added)} key(s). Your old file was not changed; "
              f"delete it when you're happy.")


@cli.command()
def doctor():
    """Show where flexrouter keeps things and which key it will use."""
    report = tui_facts.doctor_report()
    if report.error:
        if report.error_is_missing_field:
            click.echo(f"Your settings file is missing something it needs: "
                       f"{report.error}", err=True)
        else:
            click.echo(f"Could not read your settings: {report.error}", err=True)
        raise SystemExit(1)
    tui_render.console().print(tui_render.doctor_panel(report))
    for warning in report.warnings:
        click.echo(f"\n! {warning}", err=True)


if __name__ == "__main__":  # pragma: no cover
    cli()
