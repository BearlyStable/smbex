"""A Miller column: a listing with an optional highlighted cursor row."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from smbex.backend.base import DirEntry


class Column(Static):
    #: The entries currently displayed (a directory listing), for inspection/tests.
    shown: list[DirEntry] = []

    def show(self, entries: list[DirEntry], cursor: int | None = None, active: bool = False) -> None:
        self.shown = entries
        text = Text()
        if not entries:
            text.append("(empty)", style="dim italic")
        for i, entry in enumerate(entries):
            label = entry.name + ("/" if entry.is_dir else "")
            if cursor == i:
                style = "reverse" if active else "bold"
            elif entry.is_dir:
                style = "cyan"
            else:
                style = ""
            text.append(label + "\n", style=style)
        self.update(text)

    def show_file(self, entry: DirEntry | None) -> None:
        self.shown = []
        if entry is None:
            self.update(Text("(nothing selected)", style="dim italic"))
            return
        text = Text()
        text.append(entry.name + "\n", style="bold")
        text.append(f"{entry.size} bytes", style="dim")
        self.update(text)
