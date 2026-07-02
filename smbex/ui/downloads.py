"""The download/task panel: one line per transfer with a progress bar."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

_STATUS_STYLE = {
    "running": "yellow",
    "done": "green",
    "skipped": "dim",
    "error": "bold red",
    "queued": "dim",
}


def _bar(progress: float, width: int = 12) -> str:
    filled = int(progress * width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


class DownloadPanel(Static):
    def render_items(self, items) -> None:
        if not items:
            self.update(Text("(no downloads)", style="dim italic"))
            return
        text = Text()
        for item in items:
            text.append(f"{_bar(item.progress)} {int(item.progress * 100):3d}%  ")
            text.append(item.remote_path)
            text.append(f"  [{item.status}]\n", style=_STATUS_STYLE.get(item.status, ""))
        self.update(text)
