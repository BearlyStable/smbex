"""The Textual application: a ranger-style, three-column browser.

Layout is Miller columns (parent | current | preview). Navigation mirrors ranger:
``h/j/k/l`` (+ arrows), ``g``/``G`` for top/bottom. Downloads run in the background
and never block browsing: ``d`` downloads the selected item (a file, or a directory
recursively), ``a`` grabs every file in the current folder, and ``w`` toggles the
task panel. ``t`` (translation) is reserved for a later phase. Dark mode is default.

The app owns the gateway and download-manager lifecycles and renders a ``Browser``.
"""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Static

from smbex.browser import Browser, SORT_LABELS
from smbex.download import DownloadManager
from smbex.gateway import Gateway
from smbex.translate import Translator, translate_name
from smbex.ui.columns import Column
from smbex.ui.downloads import DownloadPanel
from smbex.ui.help import HelpScreen


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
    #downloads {
        height: auto;
        max-height: 40%;
        border: round $warning;
        padding: 0 1;
        overflow-y: auto;
    }
    .hidden { display: none; }
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
        Binding("d", "download", "Download"),
        Binding("a", "download_all", "Grab all"),
        Binding("w", "toggle_downloads", "Tasks"),
        Binding("p", "toggle_preload", "Preload"),
        Binding("t", "toggle_translate", "Translate"),
        Binding("o", "cycle_sort", "Sort"),
        Binding("r", "reconnect", "Reconnect"),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(
        self,
        gateway: Gateway,
        *,
        start_path: str = "",
        preload: bool = False,
        label: str = "",
        download_root: Path | str = "downloads",
        translator: Translator | None = None,
    ):
        super().__init__()
        self._gateway = gateway
        self.browser = Browser(gateway, preload=preload)
        self._downloads = DownloadManager(gateway, Path(download_root))
        self._start_path = start_path
        self._label = label
        self._translator = translator
        # On when a language was configured (--translate); 't' toggles the display.
        self.translate_enabled = translator is not None
        self._conn_state = "connected"  # updated by the gateway on link changes

    @property
    def downloads(self) -> DownloadManager:
        return self._downloads

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="columns"):
            yield Column(id="parent")
            yield Column(id="current")
            yield Column(id="preview")
        yield DownloadPanel("", id="downloads", classes="hidden")
        yield Static("", id="status")
        yield Footer()

    async def on_mount(self) -> None:
        self.theme = "textual-dark"  # dark by default
        self._gateway.on_status = self._on_conn_status  # surface reconnect state
        await self._gateway.start()
        self._downloads.on_change = self._on_downloads_change
        self._downloads.start()
        try:
            await self.browser.load(self._start_path)
        except Exception as exc:  # surface conn/list errors instead of crashing
            self._status_error(exc)
            return
        await self._refresh()

    async def on_unmount(self) -> None:
        await self._downloads.stop()  # stop before the gateway it depends on
        await self.browser.preloader.stop()  # cancel prefetches before the gateway
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
        if self.browser.preload_enabled:
            self.browser.preload_surroundings()  # warm the current neighbourhood now
        self._update_status()

    async def action_toggle_translate(self) -> None:
        self.translate_enabled = not self.translate_enabled
        await self._refresh()  # re-render with/without the English column

    async def action_cycle_sort(self) -> None:
        self.browser.cycle_sort()  # re-sorts the current view, keeping the selection
        await self._refresh()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    async def action_reconnect(self) -> None:
        """Operator-driven reconnect (the 'r' key). The only way the link comes back
        unless --auto-reconnect is set — so a new login event is intentional."""
        if self._conn_state == "reconnecting":
            return
        if await self._gateway.reconnect():
            try:
                await self._refresh()  # re-render (cached listings still serve)
            except Exception as exc:
                self._status_error(exc)

    def _entry_translations(self, entries: list) -> list[str] | None:
        """English renderings parallel to ``entries``, or None when off/unavailable."""
        if not (self.translate_enabled and self._translator and self._translator.available):
            return None
        return [translate_name(self._translator, e.name, e.is_dir) for e in entries]

    def _name_translation(self, entry) -> str | None:
        if entry is None or not (
            self.translate_enabled and self._translator and self._translator.available
        ):
            return None
        return translate_name(self._translator, entry.name, entry.is_dir)

    def _entry_markers(self, entries: list) -> list[str] | None:
        """One status glyph per current-dir entry, from in-memory state only.

        ``↓`` queued/downloading, ``✓`` downloaded, ``✗`` error, ``·`` listing cached
        (dirs), space otherwise. A folder aggregates the download items beneath it;
        a file matches its own remote path. No backend calls.
        """
        if not entries:
            return None
        items = self._downloads.items
        cache = self.browser.cache
        markers: list[str] = []
        for entry in entries:
            path = self.browser.child_path(entry.name)
            if entry.is_dir:
                prefix = path + "/"
                rel = [it for it in items if it.remote_path == path or it.remote_path.startswith(prefix)]
            else:
                rel = [it for it in items if it.remote_path == path]
            glyph = " "
            if rel:
                statuses = {it.status for it in rel}
                if statuses & {"queued", "running"}:
                    glyph = "↓"
                elif "error" in statuses:
                    glyph = "✗"
                elif statuses <= {"done", "skipped"}:
                    glyph = "✓"
            if glyph == " " and entry.is_dir and path in cache:
                glyph = "·"
            markers.append(glyph)
        return markers

    def _render_current(self) -> None:
        """(Re)render the current column from in-memory state — cheap, no fetch."""
        browser = self.browser
        self.query_one("#current", Column).show(
            browser.entries,
            cursor=browser.cursor,
            active=True,
            translations=self._entry_translations(browser.entries),
            markers=self._entry_markers(browser.entries),
        )

    # --- download actions -----------------------------------------------------
    async def action_download(self) -> None:
        sel = self.browser.selected
        if sel is None:
            return
        path = self.browser.child_path(sel.name)
        if sel.is_dir:
            # Enumeration can be slow over a slow link; do it in the background.
            self.run_worker(self._downloads.add_dir(path, recursive=True), exclusive=False)
        else:
            await self._downloads.add_file(path, sel.size)
        self._show_downloads()

    async def action_download_all(self) -> None:
        files = [
            (self.browser.child_path(e.name), e.size)
            for e in self.browser.entries
            if not e.is_dir
        ]
        if files:
            await self._downloads.add_files(files)
            self._show_downloads()

    def action_toggle_downloads(self) -> None:
        self.query_one("#downloads", DownloadPanel).toggle_class("hidden")
        self._refresh_downloads()

    def _show_downloads(self) -> None:
        self.query_one("#downloads", DownloadPanel).remove_class("hidden")
        self._refresh_downloads()

    # --- rendering ------------------------------------------------------------
    async def _refresh(self) -> None:
        browser = self.browser
        # The parent/preview columns fetch (cache-backed); tolerate a dropped link and
        # just leave them empty. The current column renders from in-memory entries (no
        # fetch), so a drop never blanks the view you're on — and it renders *after*
        # the preview fetch so its cache marker reflects the just-warmed child.
        try:
            parent = await browser.parent_entries()
        except Exception:
            parent = []
        try:
            preview = await browser.preview_entries()
        except Exception:
            preview = None

        self._render_current()
        self.query_one("#parent", Column).show(
            parent, cursor=browser.parent_cursor(parent), translations=self._entry_translations(parent)
        )
        preview_col = self.query_one("#preview", Column)
        if preview is None:
            preview_col.show_file(browser.selected, translated=self._name_translation(browser.selected))
        else:
            preview_col.show(preview, translations=self._entry_translations(preview))
        self._refresh_downloads()

    def _refresh_downloads(self) -> None:
        self.query_one("#downloads", DownloadPanel).render_items(self._downloads.items)
        self._update_status()

    def _on_downloads_change(self) -> None:
        try:
            self._refresh_downloads()
            self._render_current()  # keep the status gutter in sync with progress
        except Exception:
            pass  # widgets may be gone during teardown

    def _on_conn_status(self, state: str) -> None:
        """Gateway link-state callback ('reconnecting'/'connected'/'disconnected')."""
        self._conn_state = state
        try:
            self._update_status()
        except Exception:
            pass  # widgets may be gone during teardown

    def _update_status(self) -> None:
        browser = self.browser
        total = browser.cache.hits + browser.cache.misses
        preload = "on" if browser.preload_enabled else "off"
        text = (
            f" {self._label or 'smbex'} │ /{browser.path} │ "
            f"sort:{SORT_LABELS[browser.sort_mode]} │ "
            f"preload:{preload} │ cache:{browser.cache.hits}/{total}"
        )
        items = self._downloads.items
        if items:
            finished = sum(1 for i in items if i.status in ("done", "skipped"))
            text += f" │ dl:{finished}/{len(items)}"
        if self._translator is not None:
            t = self._translator
            if not self.translate_enabled:
                xl8 = "off"
            elif t.available:
                xl8 = f"{t.from_code}→{t.to_code}"
            else:
                xl8 = f"{t.from_code}: no model (--install-lang {t.from_code})"
            text += f" │ xl8:{xl8}"
        if self._conn_state == "connected":
            self.query_one("#status", Static).update(text)
        else:
            # Link down/recovering: lead with a prominent, coloured banner.
            if self._conn_state == "reconnecting":
                banner, style = "⟳ reconnecting…", "bold yellow"
            else:
                banner, style = "⚠ disconnected — press 'r' to reconnect", "bold red"
            line = Text(" ")
            line.append(banner, style=style)
            line.append(text)
            self.query_one("#status", Static).update(line)

    def _status_error(self, exc: Exception) -> None:
        # A dropped link already shows the reconnect banner; don't clobber it with the
        # raw exception text.
        if self._conn_state != "connected":
            self._update_status()
            return
        self.query_one("#status", Static).update(Text(f" error: {exc}", style="bold red"))
