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
        ("h  Left", "up to the parent folder"),
        ("l  Right  Enter", "open the selected folder"),
        ("j  Down", "move down"),
        ("k  Up", "move up"),
        ("g / G", "jump to top / bottom"),
        ("o", "cycle sort: name / newest / oldest"),
    ]),
    ("Transfer", [
        ("d", "download selected (a file, or a folder recursively)"),
        ("a", "download every file in this folder"),
        ("w", "show / hide the task panel"),
    ]),
    ("View", [
        ("[  ]", "show / hide the parent / preview column"),
        ("p", "preload surrounding folders (on / off)"),
        ("t", "translate filenames to English (on / off)"),
        ("T", "switch colour theme (dark / light / nord / ...)"),
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
        width: auto; max-width: 90%;
        height: auto; max-height: 90%;
        padding: 1 2;
        border: round $accent;
        background: $panel;
    }
    #help-title { text-style: bold; width: 100%; text-align: center; }
    #help-hint { color: $text-muted; width: 100%; text-align: center; }
    """

    def compose(self):
        with VerticalScroll(id="help-box"):
            yield Static("smbex - keybindings", id="help-title")
            yield Static(help_text())
            yield Static("?  or  Esc  to close", id="help-hint")

    def action_close(self) -> None:
        self.dismiss()
