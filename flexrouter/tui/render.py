"""Terminal rendering, styled with rich.

Both the ordinary commands and the TUI draw through here, so running one
command and opening the TUI look like the same tool. Nothing in this module
reads the home or knows any flexrouter rule - it takes the plain data from
`flexrouter/tui/facts.py` and draws it.

The one rule that matters: every value that came from outside this module
(provider names, key ids, masks, paths, labels) goes through `esc` first, so a
stray `[` in someone's data is never mistaken for markup.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

from rich.console import Console, Group
from rich.markup import escape as _escape
from rich.panel import Panel
from rich.table import Table

from flexrouter import home
from flexrouter.keys import mask


def esc(value: object) -> str:
    """Make any value safe to drop into a markup string. `None` -> empty."""
    if value is None:
        return ""
    return _escape(str(value))


def is_terminal() -> bool:
    return sys.stdout.isatty()


def console() -> Console:
    """A Console that never wraps or truncates when the output is not a
    terminal.

    Piped output and the test runner both capture the stream, and in that case
    a path or a key id has to survive verbatim for a script - or an assertion -
    to find it. An 80-column width would fold a long path across two lines and
    break both. Only a real terminal gets real wrapping.
    """
    if is_terminal():
        return Console()
    return Console(width=10_000, soft_wrap=True)


def _expand() -> bool:
    """Whether a border should stretch to the whole width.

    True only for a real terminal. Under the wide no-wrap console a panel that
    expanded would draw a 10,000-column border, so a piped panel is sized to
    its content instead.
    """
    return is_terminal()


def providers_table(rows) -> Table:
    """Every provider, with a key or without one.

    `rows` is `facts.providers_and_keys()` output.
    """
    table = Table(title="Providers", title_justify="left", header_style="bold")
    table.add_column("Provider")
    table.add_column("In settings", justify="center")
    table.add_column("Key")
    table.add_column("Notes", style="dim")

    for row in rows:
        if row.saved:
            parts = []
            for record in row.saved:
                shown = f"{esc(record.id)} {esc(mask(record.secret))}"
                if not record.enabled:
                    shown += " [yellow](off)[/yellow]"
                elif record.label:
                    shown += f"  {esc(record.label)}"
                parts.append(shown)
            key = ", ".join(parts)
            notes = f"{len(row.saved)} saved"
        elif row.resolved_from:
            key = f"{esc(row.resolved_masked)} ({esc(row.resolved_from)})"
            notes = "no key saved here"
        else:
            key = "[red]no key[/red]"
            notes = "add one with: flexrouter keys add " + esc(row.name)

        table.add_row(
            esc(row.name),
            "[green]yes[/green]" if row.in_settings else "[yellow]no[/yellow]",
            key,
            notes,
        )
    return table


def keys_table(vault) -> Table:
    """The saved keys, masked - the same rows `flexrouter keys list` shows.

    `vault` is `keys.load_keys()` output (provider -> list[KeyRecord]).
    """
    table = Table(title="Saved keys", title_justify="left", header_style="bold")
    table.add_column("Provider")
    table.add_column("Key id")
    table.add_column("Secret", style="cyan")
    table.add_column("Label")
    table.add_column("Source", style="dim")
    table.add_column("State", justify="center")

    for provider, records in sorted(vault.items()):
        for record in records:
            table.add_row(
                esc(provider),
                esc(record.id),
                esc(mask(record.secret)),
                esc(record.label or "—"),
                esc(record.source),
                "[green]on[/green]" if record.enabled else "[yellow]off[/yellow]",
            )
    return table


def status_panel(overview) -> Panel:
    """The `flexrouter status` view: totals, then spend per provider."""
    body = Table.grid(padding=(0, 2))
    body.add_column(style="bold", justify="right")
    body.add_column()

    if not overview.loaded:
        body.add_row("settings", f"[red]could not read:[/red] {esc(overview.config_error)}")
    body.add_row("total cost", f"${overview.total_cost_usd:.4f}")
    body.add_row(
        "buckets",
        f"{overview.bucket_count}  [dim]({', '.join(esc(b) for b in overview.buckets)})[/dim]",
    )
    body.add_row("models", str(overview.model_count))
    body.add_row("keys", f"{overview.keys_enabled}/{overview.key_count} enabled")
    body.add_row("port", str(overview.port if overview.port is not None else home.DEFAULT_PORT))
    if overview.loaded and not overview.health_present:
        body.add_row("", "[yellow]no requests recorded yet[/yellow]")

    spend = Table(title="Spend today", title_justify="left", header_style="bold")
    spend.add_column("Provider")
    spend.add_column("Today", justify="right")
    if overview.providers:
        for p in overview.providers:
            spend.add_row(esc(p.name), f"${p.daily_cost_usd:.4f}")
    else:
        spend.add_row("[dim]—[/dim]", "[dim]$0.0000[/dim]")

    return Panel(Group(body, spend), title="flexrouter status",
                 border_style="cyan", expand=_expand())


def doctor_text(report) -> str:
    """`flexrouter doctor`, as a markup string. Kept line-for-line the same
    account the plain-text doctor always gave, so it stays greppable."""
    lines: list[str] = [
        f"[bold]flexrouter home:[/bold] {esc(report.home_dir)}",
        f"  settings   {esc(report.config_name)}",
        f"  keys       {esc(report.keys_name)}",
        f"  changes    {esc(report.overrides_name)}",
        f"  records    {esc(report.state_name)}{os.sep}",
        "",
    ]
    if report.is_new_home:
        lines += [
            "This is a brand-new flexrouter home — nothing was here yet, so an "
            "empty starter settings file was just created. The counts below are "
            "that empty default, not your own configuration. Add a key to get "
            "started: flexrouter keys add <provider>",
            "",
        ]
    lines += [
        f"{report.bucket_count} bucket(s), {report.model_count} model(s), "
        f"{report.provider_count} provider(s). Serving on port {report.port}.",
        "",
        "Which key each provider will use:",
    ]
    for source in report.key_sources:
        tail = f"  {esc(source.masked)}" if source.masked else ""
        extra = f"  [dim](+{source.extra} more)[/dim]" if source.extra > 0 else ""
        lines.append(f"  {esc(source.name)}  {esc(source.where)}{tail}{extra}")
    lines.append("")
    if not report.overrides:
        lines.append("No dashboard changes on top of your settings file.")
    else:
        lines.append("Changes layered on top of your settings file:")
        for key, value in report.overrides:
            lines.append(f"  {esc(key)}: {esc(value)}")
    return "\n".join(lines)


def doctor_panel(report) -> Panel:
    return Panel(doctor_text(report), title="flexrouter doctor",
                 border_style="cyan", expand=_expand())


def banner(port: int, config_path: str) -> Panel:
    """The startup banner `flexrouter serve` / `dashboard` print."""
    url = f"http://localhost:{port}"
    body = Table.grid(padding=(0, 2))
    body.add_column(style="bold", justify="right")
    body.add_column()
    body.add_row("dashboard", url)
    body.add_row("OpenAI API", f"{url}/v1")
    body.add_row("API docs", f"{url}/docs")
    body.add_row("settings", esc(config_path))
    return Panel(body, title=f"[bold]flexrouter[/bold] running at {url}",
                 border_style="green", expand=_expand())
