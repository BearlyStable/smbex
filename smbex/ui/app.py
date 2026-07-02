"""The Textual application: a ranger-style, three-column browser.

Layout is Miller columns (parent | current | preview). Navigation mirrors ranger:
``h/j/k/l`` (+ arrows), ``g``/``G`` for top/bottom. Downloads (``d``), translation
(``t``) and the preload toggle (``p``) are wired to their keys; ``d``/``t`` are
reserved no-ops until their phases land. Dark mode is the default.

The app owns the gateway lifecycle and renders a ``Browser``. Construct it with a
started-or-unstarted ``Gateway`` over any backend (real SMB, or a fake in tests).
"""

from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Static

from smbex.browser import Browser
from smbex.gateway import Gateway
from smbex.ui.columns import Column


class SmbexApp(App):
    TITLE = "smbex"

    CSS = """
    #columns { height: 1fr; }
    Column {
        width: 1fr;
        border: round $panel;
        padding: 0 1;
        overflow-y: auto;
    }
    #current { border: round $accent; }
    #status {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("j,down", "cursor_down", "Down"),
        Binding("k,up", "cursor_up", "Up"),
        Binding("l,right", "enter", "Open"),
        Binding("h,left", "leave", "Up"),
        Binding("g", "cursor_top", "Top"),
        Binding("G", "cursor_bottom", "Bottom"),
        Binding("p", "toggle_preload", "Preload"),
        # Reserved for later phases; visible in the footer, no-ops for now.
        Binding("d", "noop", "Download"),
        Binding("t", "noop", "Translate"),
    ]

    def __init__(
        self,
        gateway: Gateway,
        *,
        start_path: str = "",
        preload: bool = False,
        label: str = "",
    ):
        super().__init__()
        self._gateway = gateway
        self.browser = Browser(gateway, preload=preload)
        self._start_path = start_path
        self._label = label

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="columns"):
            yield Column(id="parent")
            yield Column(id="current")
            yield Column(id="preview")
        yield Static("", id="status")
        yield Footer()

    async def on_mount(self) -> None:
        self.theme = "textual-dark"  # dark by default
        await self._gateway.start()
        try:
            await self.browser.load(self._start_path)
        except Exception as exc:  # surface conn/list errors instead of crashing
            self._status_error(exc)
            return
        await self._refresh()

    async def on_unmount(self) -> None:
        await self._gateway.stop()

    # --- navigation actions ---------------------------------------------------
    async def action_cursor_down(self) -> None:
        self.browser.move(1)
        await self._refresh()

    async def action_cursor_up(self) -> None:
        self.browser.move(-1)
        await self._refresh()

    async def action_cursor_top(self) -> None:
        self.browser.move_to(0)
        await self._refresh()

    async def action_cursor_bottom(self) -> None:
        self.browser.move_to(self.browser.count - 1)
        await self._refresh()

    async def action_enter(self) -> None:
        try:
            if await self.browser.enter():
                await self._refresh()
        except Exception as exc:
            self._status_error(exc)

    async def action_leave(self) -> None:
        try:
            if await self.browser.go_parent():
                await self._refresh()
        except Exception as exc:
            self._status_error(exc)

    def action_toggle_preload(self) -> None:
        self.browser.preload_enabled = not self.browser.preload_enabled
        self._update_status()

    def action_noop(self) -> None:
        """Reserved binding (download/translate arrive in later phases)."""

    # --- rendering ------------------------------------------------------------
    async def _refresh(self) -> None:
        browser = self.browser
        parent = await browser.parent_entries()
        preview = await browser.preview_entries()

        self.query_one("#parent", Column).show(parent, cursor=browser.parent_cursor(parent))
        self.query_one("#current", Column).show(
            browser.entries, cursor=browser.cursor, active=True
        )
        preview_col = self.query_one("#preview", Column)
        if preview is None:
            preview_col.show_file(browser.selected)
        else:
            preview_col.show(preview)
        self._update_status()

    def _update_status(self) -> None:
        browser = self.browser
        total = browser.cache.hits + browser.cache.misses
        preload = "on" if browser.preload_enabled else "off"
        text = (
            f" {self._label or 'smbex'} │ /{browser.path} │ "
            f"preload:{preload} │ cache:{browser.cache.hits}/{total}"
        )
        self.query_one("#status", Static).update(text)

    def _status_error(self, exc: Exception) -> None:
        self.query_one("#status", Static).update(Text(f" error: {exc}", style="bold red"))
