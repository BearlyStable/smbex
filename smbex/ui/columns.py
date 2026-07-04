"""A Miller column: a listing with an optional highlighted cursor row.

Files show a right-aligned human-readable size; directories show none (a folder's
recursive size can't be known without walking it — see CLAUDE.md)."""

from __future__ import annotations

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
        """Render the listing. ``translations`` (parallel to ``entries``) shows each
        English rendering beside the original name; ``markers`` prefixes each row with
        a one-char status glyph (cached / queued / downloaded)."""
        self.shown = entries
        text = Text(no_wrap=True, overflow="ellipsis")
        if not entries:
            text.append("(empty)", style="dim italic")
        name_width = max((len(e.name) + (1 if e.is_dir else 0) for e in entries), default=0)
        for i, entry in enumerate(entries):
            if markers is not None:
                glyph = markers[i] if i < len(markers) else " "
                text.append(glyph + " ", style=MARKER_STYLE.get(glyph, ""))
            label = (entry.name + ("/" if entry.is_dir else "")).ljust(name_width)
            if cursor == i:
                style = "reverse" if active else "bold"
            elif entry.is_dir:
                style = "cyan"
            else:
                style = ""
            text.append(label, style=style)
            if translations is not None and i < len(translations):
                rendered = translations[i]
                if rendered and rendered != entry.name:
                    text.append("  → ", style="dim")
                    text.append(rendered, style="italic green")
            if not entry.is_dir:
                text.append("  " + human_size(entry.size).rjust(7), style="dim")
            text.append("\n")
        self.rendered_text = text.plain
        self.update(text)

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
        self.rendered_text = text.plain
        self.update(text)
