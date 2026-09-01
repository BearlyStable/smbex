"""The help overlay — a modal listing keybindings by group, opened with '?'.

Kept in sync with ``SmbexApp.BINDINGS`` by hand: the grouping/wording here is the
user-facing reference, so it is curated rather than generated."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

# (section title, [(keys, description), ...]); a blank title starts an ungrouped run.
_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Navigate", [
        ("h Left", "up to the parent folder"),
        ("l Right Enter", "open folder, or view a downloaded file"),
        ("j Down", "move down / scroll"),
        ("k Up", "move up / scroll"),
        ("g / G", "jump to top / bottom"),
        ("PgUp/PgDn", "page up / down"),
        ("o", "cycle sort: name / newest / oldest"),
    ]),
    ("View a file", [
        ("l / Enter", "open a downloaded file (text or hex)"),
        ("j/k g/G", "scroll (loads lazily)"),
        ("t", "text: original + English side by side"),
        ("h / Esc", "back to the listing"),
    ]),
    ("Transfer", [
        ("d", "download selection (recursive for folders)"),
        ("a", "download all files here"),
        ("w", "open / close the task panel (all transfers)"),
        ("y / Y", "copy selection's name / full path to clipboard"),
    ]),
    ("Task panel (while open)", [
        ("j / k", "select a transfer"),
        ("K / J", "move up / down the queue (crossing the running one pauses it)"),
        ("x", "cancel a transfer, or clear a finished entry"),
        ("w h Esc", "close the panel"),
    ]),
    ("View", [
        ("[  ]", "show / hide parent / preview column"),
        ("p", "preload surrounding folders"),
        ("t", "translate filenames to English"),
        ("T", "cycle colour theme"),
    ]),
    ("Connection", [
        ("r", "reconnect after a dropped link"),
    ]),
    ("", [
        ("?", "show / hide this help"),
        ("q", "quit"),
    ]),
]


def help_text() -> Text:
    """The formatted key reference (also handy for tests via ``.plain``)."""
    key_w = max(len(key) for _, rows in _SECTIONS for key, _ in rows)
    text = Text()
    for i, (title, rows) in enumerate(_SECTIONS):
        if title:
            text.append(("\n" if i else "") + f"{title}\n", style="bold cyan")
        for key, desc in rows:
            text.append("  ")
            text.append(key.ljust(key_w), style="bold")
            text.append("  ")
            text.append(desc + "\n", style="dim")
    return text


class HelpScreen(ModalScreen):
    """A dismissible, dark-mode-aware overlay of the keybindings."""

    BINDINGS = [Binding("question_mark,escape,q", "close", "Close")]

    DEFAULT_CSS = """
    HelpScreen { align: center middle; background: $background 60%; }
    #help-box {
        width: 66; max-width: 95%;
        height: auto; max-height: 90%;
        padding: 1 2;
        border: round $accent;
        background: $panel;
    }
    #help-title { text-style: bold; width: 1fr; text-align: center; }
    #help-hint { color: $text-muted; width: 1fr; text-align: center; }
    """

    def compose(self):
        with VerticalScroll(id="help-box"):
            yield Static("smbex - keybindings", id="help-title")
            yield Static(help_text())
            yield Static("?  or  Esc  to close", id="help-hint")

    def action_close(self) -> None:
        self.dismiss()
