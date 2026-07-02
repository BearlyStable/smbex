"""The Textual application shell.

Phase 0 is a minimal, dark-by-default app that boots and shows a placeholder.
The ranger-style Miller-column browser is added in Phase 2.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Static

PLACEHOLDER = (
    "smbex — remote file explorer (SMB + SSH)\n\n"
    "Foundation scaffold is up. The connect flow and ranger-style browser "
    "arrive in Phase 2.\n\n"
    "Press q to quit."
)


class SmbexApp(App):
    """Root application. Dark mode is the default (see ``theme``)."""

    TITLE = "smbex"

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(PLACEHOLDER, id="placeholder")
        yield Footer()

    def on_mount(self) -> None:
        # Dark mode is the default. Set the reactive at runtime (not as a class
        # attribute, which would shadow Textual's `theme` reactive descriptor).
        self.theme = "textual-dark"
