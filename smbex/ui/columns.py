"""A Miller column: a listing with an optional highlighted cursor row.

Files show a right-aligned human-readable size; directories show none (a folder's
recursive size can't be known without walking it — see CLAUDE.md)."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from smbex.backend.base import DirEntry


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}T"


def human_time(mtime: float, now: float | None = None) -> str:
    """Compact age of ``mtime`` (epoch secs): '5m', '3h', '2d', '3w', '6mo', '2y'.

    Empty for an unknown time (0). ``mtime`` is the only cross-protocol timestamp
    (SFTP exposes just mtime/atime; SMB also has creation/change). ``now`` is
    injectable for tests."""
    if not mtime or mtime <= 0:
        return ""
    import time

    delta = (time.time() if now is None else now) - mtime
    if delta < 0:
        delta = 0
    for cutoff, secs, suffix in (
        (3600, 60, "m"),
        (86400, 3600, "h"),
        (7 * 86400, 86400, "d"),
        (30 * 86400, 7 * 86400, "w"),
        (365 * 86400, 30 * 86400, "mo"),
    ):
        if delta < cutoff:
            return f"{int(delta // secs)}{suffix}"
    return f"{int(delta // (365 * 86400))}y"


def full_time(mtime: float) -> str:
    """Absolute local timestamp for the preview pane, or '' if unknown."""
    if not mtime or mtime <= 0:
        return ""
    import datetime

    return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


#: Style for each status-gutter glyph (see SmbexApp._entry_markers for the source).
MARKER_STYLE = {
    "↓": "yellow",     # queued / downloading
    "✓": "green",      # downloaded / complete
    "✗": "bold red",   # download error
    "·": "dim",        # listing already cached
}


class Column(Static):
    #: The entries currently displayed, and the rendered plain text (for tests).
    shown: list[DirEntry] = []
    rendered_text: str = ""

    def show(
        self,
        entries: list[DirEntry],
        cursor: int | None = None,
        active: bool = False,
        translations: list[str] | None = None,
        markers: list[str] | None = None,
    ) -> None:
        """Render the listing as an aligned table. ``translations`` (parallel to
        ``entries``) shows each English rendering beside the original name;
        ``markers`` adds a leading one-char status glyph (cached / queued / downloaded).

        A ``rich`` grid keeps the name, size and age in fixed, right-aligned columns
        regardless of translation length or column width — the name (with its optional
        translation) flexes and truncates with an ellipsis, so metadata never drifts.
        """
        self.shown = entries
        if not entries:
            self.rendered_text = "(empty)"
            self.update(Text("(empty)", style="dim italic"))
            return

        table = Table.grid(expand=True, padding=(0, 1))
        if markers is not None:
            table.add_column(width=1, no_wrap=True)  # status gutter
        table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")  # name (+ translation)
        table.add_column(justify="right", no_wrap=True)  # size
        table.add_column(justify="right", no_wrap=True)  # age

        lines: list[str] = []
        for i, entry in enumerate(entries):
            glyph = " "
            cells = []
            if markers is not None:
                glyph = markers[i] if i < len(markers) else " "
                cells.append(Text(glyph, style=MARKER_STYLE.get(glyph, "")))

            name = Text(entry.name + ("/" if entry.is_dir else ""), style="cyan" if entry.is_dir else "")
            if translations is not None and i < len(translations):
                rendered = translations[i]
                if rendered and rendered != entry.name:
                    name.append("  → ", style="dim")
                    name.append(rendered, style="green")
            size = human_size(entry.size) if not entry.is_dir else ""
            age = human_time(entry.mtime)
            cells += [name, Text(size, style="dim"), Text(age, style="dim")]

            row_style = ("reverse" if active else "bold") if cursor == i else ""
            table.add_row(*cells, style=row_style)
            lines.append(f"{glyph} {name.plain}  {size}  {age}")

        self.rendered_text = "\n".join(lines)
        self.update(table)

    def show_file(self, entry: DirEntry | None, translated: str | None = None) -> None:
        self.shown = []
        if entry is None:
            self.rendered_text = ""
            self.update(Text("(nothing selected)", style="dim italic"))
            return
        text = Text()
        text.append(entry.name + "\n", style="bold")
        if translated and translated != entry.name:
            text.append(f"→ {translated}\n", style="italic green")
        text.append(f"{human_size(entry.size)}  ({entry.size} bytes)", style="dim")
        stamp = full_time(entry.mtime)
        if stamp:
            text.append(f"\nmodified {stamp}", style="dim")
        self.rendered_text = text.plain
        self.update(text)
