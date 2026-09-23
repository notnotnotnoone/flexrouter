"""The `flexrouter tui` app: the router's terminal surface.

Four views over the same home `flexrouter` reads everywhere else - Overview,
Keys, Requests, Doctor. It reads the home's files through
`flexrouter/tui/facts.py`, so it works with no service running; it never talks
to a provider, and the only thing it writes is a key added or removed on
purpose from the Keys view.

Security rule, the same one `flexrouter/keys.py` states for the whole project:
the full secret never reaches the screen. Saved keys are shown through
`keys.mask()`, and the secret field of the add form is a password input.
"""
from __future__ import annotations

from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from flexrouter import home
from flexrouter import keys as keyvault
from flexrouter.tui import facts, render


class AddKeyScreen(ModalScreen):
    """A small form: pick or type a provider, paste the key, name it."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, providers: list[str]) -> None:
        super().__init__()
        self._providers = providers

    def compose(self) -> ComposeResult:
        with Vertical(id="add-key-box"):
            yield Label("[bold]Add a key[/bold]")
            yield Select(
                [(name, name) for name in self._providers],
                prompt="Provider (or type one below)",
                allow_blank=True,
                id="provider-select",
            )
            yield Input(placeholder="provider name", id="provider-input")
            yield Input(placeholder="secret — never shown once saved",
                        password=True, id="secret-input")
            yield Input(placeholder="label (optional)", id="label-input")
            with Horizontal(id="add-key-buttons"):
                yield Button("Save", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "save":
            self._save()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _save(self) -> None:
        chosen = self.query_one("#provider-select", Select).value
        typed = self.query_one("#provider-input", Input).value.strip()
        provider = typed or ("" if chosen is Select.BLANK else str(chosen))
        secret = self.query_one("#secret-input", Input).value
        label = self.query_one("#label-input", Input).value.strip()
        if not provider:
            self.notify("Give the provider a name", severity="warning")
            return
        if not secret.strip():
            self.notify("Paste the key", severity="warning")
            return
        self.dismiss((provider, secret, label))


class ConfirmScreen(ModalScreen):
    """A yes/no the caller acts on - used before anything is deleted."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(render.esc(self._question))
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes", id="yes", variant="error")
                yield Button("No", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


class FlexRouterApp(App):
    """The whole terminal surface, in four tabs."""

    TITLE = "flexrouter"
    SUB_TITLE = "the router, in your terminal"

    CSS = """
    Screen { layout: vertical; }
    #overview-stats { padding: 1 2; height: auto; }
    #overview-table, #keys-table, #requests-table { height: 1fr; }
    #key-buttons { height: auto; padding: 1 2; }
    #key-buttons Button { margin-right: 1; }
    #doctor-body { padding: 1 2; }
    AddKeyScreen, ConfirmScreen { align: center middle; }
    #add-key-box, #confirm-box {
        width: 64; height: auto; padding: 1 2;
        border: thick $accent; background: $surface;
    }
    #add-key-box Input, #add-key-box Select { margin-bottom: 1; }
    #confirm-buttons, #add-key-buttons { height: auto; margin-top: 1; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_data", "Refresh"),
        Binding("a", "add_key", "Add key"),
        Binding("d", "remove_key", "Remove key"),
    ]

    def __init__(self, config_path=None) -> None:
        super().__init__()
        self._config_path = config_path
        # Row index -> (provider, key id or None) for the Keys table, because
        # one provider with no saved key still needs a row a person can select
        # and read, and DataTable's own row keys only carry what we hand them.
        self._key_row_refs: list[tuple[str, Optional[str]]] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent():
            with TabPane("Overview", id="overview"):
                yield Static(id="overview-stats")
                yield DataTable(id="overview-table", zebra_stripes=True)
            with TabPane("Keys", id="keys"):
                with Horizontal(id="key-buttons"):
                    yield Button("Add key", id="add-key", variant="primary")
                    yield Button("Remove selected", id="remove-key", variant="error")
                    yield Button("Toggle enabled", id="toggle-key")
                yield DataTable(id="keys-table", zebra_stripes=True)
            with TabPane("Requests", id="requests"):
                yield DataTable(id="requests-table", zebra_stripes=True)
            with TabPane("Doctor", id="doctor"):
                yield Static(id="doctor-body")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#overview-table", DataTable).add_columns(
            "Provider", "State", "Keys", "Models", "Today")
        self.query_one("#keys-table", DataTable).add_columns(
            "Provider", "Key id", "Secret", "Label", "Source", "State")
        self.query_one("#requests-table", DataTable).add_columns(
            "When", "Bucket", "Provider", "Model", "Tokens", "Cost", "ms", "Status")
        for table in self.query(DataTable):
            table.cursor_type = "row"
        self.refresh_data()
        self.set_interval(2.0, self.refresh_data)

    def action_refresh_data(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        """Re-read the home and repaint every view.

        Anything that goes wrong is shown, not raised: a TUI that dies on one
        torn state file would take the whole terminal down with it.
        """
        try:
            overview = facts.overview(self._config_path)
            provider_rows = facts.providers_and_keys(self._config_path)
            requests = facts.recent_requests(config_path=self._config_path)
            report = facts.doctor_report(self._config_path)
        except Exception as exc:  # pragma: no cover - defensive only
            self.query_one("#overview-stats", Static).update(
                f"[red]Could not read the flexrouter home:[/red] {render.esc(exc)}")
            return

        self._show_overview(overview)
        self._show_keys(provider_rows)
        self._show_requests(requests)
        self.query_one("#doctor-body", Static).update(render.doctor_text(report))

    def _show_overview(self, overview) -> None:
        stats = self.query_one("#overview-stats", Static)
        if not overview.loaded:
            stats.update(
                f"[red]Could not read your settings:[/red] {render.esc(overview.config_error)}\n\n"
                "Open the Doctor tab for the paths, or run [bold]flexrouter doctor[/bold].")
        else:
            port = overview.port if overview.port is not None else home.DEFAULT_PORT
            stats.update(
                f"[bold]${overview.total_cost_usd:.4f}[/bold] this session   "
                f"·   [bold]{overview.bucket_count}[/bold] buckets   "
                f"·   [bold]{overview.model_count}[/bold] models   "
                f"·   [bold]{overview.keys_enabled}/{overview.key_count}[/bold] keys live   "
                f"·   port [bold]{port}[/bold]"
            )

        table = self.query_one("#overview-table", DataTable)
        table.clear()
        for p in overview.providers:
            table.add_row(
                p.name,
                p.state,
                f"{p.keys_enabled}/{p.key_count}",
                str(p.models_total),
                f"${p.daily_cost_usd:.4f}",
            )

    def _show_keys(self, provider_rows) -> None:
        table = self.query_one("#keys-table", DataTable)
        table.clear()
        self._key_row_refs = []
        for row in provider_rows:
            if row.saved:
                for record in row.saved:
                    table.add_row(
                        row.name, record.id, keyvault.mask(record.secret),
                        record.label or "—", record.source,
                        "on" if record.enabled else "off",
                    )
                    self._key_row_refs.append((row.name, record.id))
            elif row.resolved_from:
                table.add_row(
                    row.name, "—", row.resolved_masked, "—", row.resolved_from, "on",
                )
                self._key_row_refs.append((row.name, None))
            else:
                table.add_row(row.name, "—", "—", "—", "—", "no key")
                self._key_row_refs.append((row.name, None))

    def _show_requests(self, requests) -> None:
        table = self.query_one("#requests-table", DataTable)
        table.clear()
        for r in requests:
            table.add_row(
                r.timestamp, r.bucket, r.provider, r.model,
                f"{r.prompt_tokens}+{r.completion_tokens}",
                f"${r.cost_usd:.6f}", str(r.latency_ms), r.status,
            )

    def _selected_key(self) -> Optional[tuple[str, Optional[str]]]:
        table = self.query_one("#keys-table", DataTable)
        if not self._key_row_refs:
            return None
        index = table.cursor_row
        if index is None or index < 0 or index >= len(self._key_row_refs):
            return None
        return self._key_row_refs[index]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-key":
            self.action_add_key()
        elif event.button.id == "remove-key":
            self.action_remove_key()
        elif event.button.id == "toggle-key":
            self.action_toggle_key()

    def action_add_key(self) -> None:
        providers = [row.name for row in facts.providers_and_keys(self._config_path)]
        self.push_screen(AddKeyScreen(providers), self._on_key_added)

    def _on_key_added(self, result) -> None:
        if not result:
            return
        provider, secret, label = result
        try:
            record = keyvault.add_key(provider, secret.strip(), label=label)
        except Exception as exc:  # pragma: no cover - disk errors only
            self.notify(f"Could not save the key: {exc}", severity="error")
            return
        self.notify(f"Saved {record.id} for {provider}")
        self.refresh_data()

    def action_remove_key(self) -> None:
        selected = self._selected_key()
        if selected is None:
            self.notify("Nothing selected", severity="warning")
            return
        provider, key_id = selected
        if key_id is None:
            self.notify(f"{provider} has no saved key to remove", severity="warning")
            return
        self.push_screen(
            ConfirmScreen(f"Remove {key_id} from {provider}?"),
            lambda confirmed: self._remove_key(provider, key_id) if confirmed else None,
        )

    def _remove_key(self, provider: str, key_id: str) -> None:
        if keyvault.remove_key(provider, key_id):
            self.notify(f"Removed {key_id} from {provider}")
        else:
            self.notify(f"No key {key_id} for {provider}", severity="error")
        self.refresh_data()

    def action_toggle_key(self) -> None:
        selected = self._selected_key()
        if selected is None:
            self.notify("Nothing selected", severity="warning")
            return
        provider, key_id = selected
        if key_id is None:
            self.notify(f"{provider} has no saved key to toggle", severity="warning")
            return
        vault = keyvault.load_keys()
        for record in vault.get(provider, []):
            if record.id == key_id:
                record.enabled = not record.enabled
                keyvault.save_keys(vault)
                self.notify(f"{key_id} is now {'on' if record.enabled else 'off'}")
                self.refresh_data()
                return
        self.notify(f"No key {key_id} for {provider}", severity="error")


def run_tui(config_path=None) -> None:
    FlexRouterApp(config_path).run()
