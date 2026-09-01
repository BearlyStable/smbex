"""The download/task panel: one line per transfer with a progress bar.

The panel is meant to stay out of the way (it covers the browser): by default the
app shows only what is actually transferring and hides it again when the queue
drains — see ``SmbexApp._refresh_downloads``. ``w`` opens the full list, where the
cursor selects a transfer and a queued one can be moved up or down the queue.

The widget itself is dumb: the caller decides *which* items to show, and passes the
cursor and a summary line. Long lists are windowed around the cursor so the entries
you care about are never scrolled out of view.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from smbex.ui.columns import human_size

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


def _window(count: int, cursor: int | None, max_rows: int) -> tuple[int, int]:
    """Rows to render — a slice around ``cursor`` so the selection stays visible."""
    if count <= max_rows:
        return 0, count
    top = 0 if cursor is None else max(0, min(cursor - max_rows // 2, count - max_rows))
    return top, top + max_rows


class DownloadPanel(Static):
    #: Plain text of the last render, and the items it showed (for tests).
    rendered_text: str = ""
    shown: list = []

    def render_items(
        self,
        items,
        *,
        cursor: int | None = None,
        max_rows: int = 8,
        summary: str = "",
    ) -> None:
        self.shown = list(items)
        if not items:
            self.rendered_text = summary or "(no downloads)"
            self.update(Text(self.rendered_text, style="dim italic"))
            return

        text = Text()
        lines: list[str] = []
        if summary:
            text.append(summary + "\n", style="dim")
            lines.append(summary)

        start, stop = _window(len(items), cursor, max_rows)
        if start:
            text.append(f"  … {start} above\n", style="dim italic")
            lines.append(f"… {start} above")
        for i in range(start, stop):
            item = items[i]
            mark = "▸ " if cursor == i else "  "
            row_style = "bold" if cursor == i else ""
            size = human_size(item.size) if item.size else ""
            line = (
                f"{mark}{_bar(item.progress)} {int(item.progress * 100):3d}% "
                f"{item.remote_path}"
            )
            text.append(line, style=row_style)
            if size:
                text.append(f"  {size}", style="dim")
            text.append(f"  [{item.status}]\n", style=_STATUS_STYLE.get(item.status, ""))
            lines.append(f"{line}  {size}  [{item.status}]")
        if stop < len(items):
            text.append(f"  … {len(items) - stop} below\n", style="dim italic")
            lines.append(f"… {len(items) - stop} below")

        self.rendered_text = "\n".join(lines)
        self.update(text)
