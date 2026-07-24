"""The ``--mux`` socket picker: a small standalone app shown before the main UI.

Runs to completion with :meth:`App.run`, whose return value is the chosen socket
path (or ``None`` if cancelled). Kept separate from :class:`~smbex.ui.app.SmbexApp`
because the choice must be made *before* a connection exists.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from smbex.mux import MasterInfo


class MuxPicker(App[str]):
    """Pick one live ControlMaster socket to ride. ``run()`` -> socket path or None."""

    CSS = """
    Screen { align: center middle; }
    #mux-box {
        width: 88; max-width: 96%;
        height: auto; max-height: 90%;
        border: round $accent; padding: 1 2; background: $panel;
    }
    #mux-title { text-style: bold; width: 1fr; text-align: center; padding-bottom: 1; }
    #mux-list { height: auto; max-height: 20; }
    #mux-hint { color: $text-muted; width: 1fr; text-align: center; padding-top: 1; }
    """

    BINDINGS = [
        Binding("j,down", "cursor_down", "Down", show=False),
        Binding("k,up", "cursor_up", "Up", show=False),
        Binding("l", "select", "Select"),
        Binding("q,escape", "cancel", "Cancel"),
    ]

    def __init__(self, masters: list[MasterInfo]):
        super().__init__()
        self._masters = list(masters)

    def compose(self) -> ComposeResult:
        with Vertical(id="mux-box"):
            yield Static(
                f"Select an SSH master socket to ride  ({len(self._masters)} found)",
                id="mux-title",
            )
            yield OptionList(
                *(self._option(i, m) for i, m in enumerate(self._masters)),
                id="mux-list",
            )
            yield Static("j/k move   Enter/l select   q cancel", id="mux-hint")

    @staticmethod
    def _option(index: int, m: MasterInfo) -> Option:
        text = Text()
        text.append(m.label, style="bold")
        if m.pid is not None:
            text.append(f"   pid {m.pid}", style="dim")
        text.append("\n")
        text.append(m.path, style="dim")
        return Option(text, id=str(index))

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        ol.focus()
        if self._masters:
            ol.highlighted = 0

    def _list(self) -> OptionList:
        return self.query_one(OptionList)

    # j/k bubble up from the (unbound-for-these-keys) OptionList to here.
    def action_cursor_down(self) -> None:
        self._list().action_cursor_down()

    def action_cursor_up(self) -> None:
        self._list().action_cursor_up()

    def action_select(self) -> None:
        highlighted = self._list().highlighted
        if highlighted is not None:
            self._choose(highlighted)

    # Enter is handled natively by the OptionList -> this message.
    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._choose(event.option_index)

    def _choose(self, index: int) -> None:
        self.exit(self._masters[index].path)

    def action_cancel(self) -> None:
        self.exit(None)
