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

from smbex.browser import Browser, SORT_BY_LABEL, SORT_LABELS
from smbex.download import DownloadManager
from smbex.gateway import Gateway
from smbex.translate import Translator, translate_name
from smbex.ui.columns import Column
from smbex.ui.downloads import DownloadPanel
from smbex.ui.help import HelpScreen


# Friendly names for --theme / config; anything else is passed through to Textual.
_THEME_ALIASES = {"dark": "textual-dark", "light": "textual-light"}
# What the 'T' key cycles through (filtered to those Textual actually has).
_THEME_CYCLE = ["textual-dark", "textual-light", "nord", "gruvbox"]


class _FileViewState:
    """A downloaded file open in the content viewer (windowed; lazy translate/hex)."""

    def __init__(self, name: str, source, kind: str):
        self.name = name
        self.lazy = source  # viewer.LazyLines (text) or viewer.LazyHex (hex)
        self.kind = kind  # "text" | "hex"
        self.top = 0  # index of the first visible line / hex row
        self.translations: dict[int, str] = {}  # line index -> translated text


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
        Binding("l,right,enter", "enter", "Open"),
        Binding("h,left", "leave", "Up"),
        Binding("g", "cursor_top", "Top"),
        Binding("G", "cursor_bottom", "Bottom"),
        Binding("pagedown", "page_down", "Page dn", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("escape", "escape_view", "Back", show=False),
        Binding("d", "download", "Download"),
        Binding("a", "download_all", "Grab all"),
        Binding("w", "toggle_downloads", "Tasks"),
        Binding("p", "toggle_preload", "Preload"),
        Binding("t", "toggle_translate", "Translate"),
        Binding("o", "cycle_sort", "Sort"),
        Binding("r", "reconnect", "Reconnect"),
        Binding("T", "cycle_theme", "Theme"),
        Binding("[", "toggle_parent", "Parent col"),
        Binding("]", "toggle_preview", "Preview col"),
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
        sort: str = "name",
        theme: str = "dark",
        show_parent: bool = True,
        show_preview: bool = True,
    ):
        super().__init__()
        self._gateway = gateway
        self._theme_pref = theme
        self._show_parent = show_parent
        self._show_preview = show_preview
        self.browser = Browser(gateway, preload=preload)
        self.browser.sort_mode = SORT_BY_LABEL.get(sort, "name")  # initial view sort
        self._downloads = DownloadManager(gateway, Path(download_root))
        self._start_path = start_path
        self._label = label
        self._translator = translator
        # On when a language was configured (--translate); 't' toggles the display.
        self.translate_enabled = translator is not None
        self._conn_state = "connected"  # updated by the gateway on link changes
        self._preview_key = None  # what the preview last rendered (to re-render on change)
        self._view: _FileViewState | None = None  # set while a file's content is open

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
        self.theme = self._resolve_theme(self._theme_pref)  # dark by default
        self.query_one("#parent", Column).set_class(not self._show_parent, "hidden")
        self.query_one("#preview", Column).set_class(not self._show_preview, "hidden")
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

    def on_resize(self, event) -> None:
        if self._view is not None:  # re-fit the content window to the new height
            self._render_view()

    # --- navigation actions ---------------------------------------------------
    async def action_cursor_down(self) -> None:
        if self._view is not None:
            self._scroll_view(1)
            return
        self.browser.move(1)
        await self._refresh()

    async def action_cursor_up(self) -> None:
        if self._view is not None:
            self._scroll_view(-1)
            return
        self.browser.move(-1)
        await self._refresh()

    async def action_cursor_top(self) -> None:
        if self._view is not None:
            self._view.top = 0
            self._render_view()
            return
        self.browser.move_to(0)
        await self._refresh()

    async def action_cursor_bottom(self) -> None:
        if self._view is not None:
            v = self._view
            if v.kind == "hex":
                v.top = max(0, v.lazy.rows - self._view_rows())
            else:
                v.lazy.load_all()  # explicit jump-to-end reads the rest
                v.top = max(0, v.lazy.loaded - self._view_rows())
            self._render_view()
            return
        self.browser.move_to(self.browser.count - 1)
        await self._refresh()

    async def action_page_down(self) -> None:
        if self._view is not None:
            self._scroll_view(self._view_page())
        else:
            self.browser.move(max(1, self._view_rows() - 1))
            await self._refresh()

    async def action_page_up(self) -> None:
        if self._view is not None:
            self._scroll_view(-self._view_page())
        else:
            self.browser.move(-max(1, self._view_rows() - 1))
            await self._refresh()

    async def action_enter(self) -> None:
        if self._view is not None:
            return
        sel = self.browser.selected
        if sel is not None and not sel.is_dir:  # a file -> open the content view
            self._enter_file_view(sel)
            return
        try:
            if await self.browser.enter():
                await self._refresh()
        except Exception as exc:
            self._status_error(exc)

    async def action_leave(self) -> None:
        if self._view is not None:  # leave the content view, back to browsing
            self._close_file_view()
            await self._refresh()
            return
        try:
            if await self.browser.go_parent():
                await self._refresh()
        except Exception as exc:
            self._status_error(exc)

    async def action_escape_view(self) -> None:
        if self._view is not None:  # Esc leaves the viewer; a no-op while browsing
            self._close_file_view()
            await self._refresh()

    def action_toggle_preload(self) -> None:
        self.browser.preload_enabled = not self.browser.preload_enabled
        if self.browser.preload_enabled:
            self.browser.preload_surroundings()  # warm the current neighbourhood now
        self._update_status()

    async def action_toggle_translate(self) -> None:
        self.translate_enabled = not self.translate_enabled
        if self._view is not None:
            self._render_view()  # switch the viewer between two-page and orig|translation
        else:
            await self._refresh()  # re-render with/without the English column

    async def action_cycle_sort(self) -> None:
        self.browser.cycle_sort()  # re-sorts the current view, keeping the selection
        await self._refresh()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    async def action_toggle_parent(self) -> None:
        self._show_parent = not self._show_parent
        self.query_one("#parent", Column).set_class(not self._show_parent, "hidden")
        await self._refresh()  # (re)fetch the parent listing if newly shown

    async def action_toggle_preview(self) -> None:
        self._show_preview = not self._show_preview
        self.query_one("#preview", Column).set_class(not self._show_preview, "hidden")
        await self._refresh()

    def _resolve_theme(self, name: str) -> str:
        """Map a friendly/config theme name to a registered Textual theme."""
        resolved = _THEME_ALIASES.get(name, name)
        return resolved if resolved in self.available_themes else "textual-dark"

    def action_cycle_theme(self) -> None:
        cycle = [t for t in _THEME_CYCLE if t in self.available_themes]
        if not cycle:
            return
        try:
            i = cycle.index(self.theme)
        except ValueError:
            i = -1
        self.theme = cycle[(i + 1) % len(cycle)]

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
        # the preview fetch so its cache marker reflects the just-warmed child. Hidden
        # columns skip their fetch entirely (saves a round-trip on a slow link).
        parent: list = []
        if self._show_parent:
            try:
                parent = await browser.parent_entries()
            except Exception:
                parent = []
        preview = None
        if self._show_preview:
            try:
                preview = await browser.preview_entries()
            except Exception:
                preview = None

        self._render_current()
        if self._show_parent:
            self.query_one("#parent", Column).show(
                parent, cursor=browser.parent_cursor(parent), translations=self._entry_translations(parent)
            )
        if self._show_preview:
            self._render_preview(preview)
        self._refresh_downloads()

    def _render_preview(self, preview) -> None:
        """Preview pane: a selected directory's listing, a downloaded file's content
        (text or hex), or otherwise the selected file's metadata."""
        col = self.query_one("#preview", Column)
        browser = self.browser
        self._preview_key = self._preview_state()  # remember what we're about to show
        if preview is not None:  # a directory is selected
            col.show(preview, translations=self._entry_translations(preview))
            return
        sel = browser.selected
        local = self._downloaded_local_path(sel)
        if local is None:  # not downloaded (or a non-file): just metadata
            col.show_file(sel, translated=self._name_translation(sel))
            return
        try:
            from smbex.preview import read_preview

            kind, body, truncated = read_preview(local)
        except Exception:
            col.show_file(sel, translated=self._name_translation(sel))
            return
        col.show_preview(sel, kind, body, truncated, translated=self._name_translation(sel))
        if kind == "text":
            self._maybe_translate_preview(sel, body, truncated)

    def _preview_state(self):
        """A key for what the preview should show, so we know when to re-render it
        (e.g. a selected file finishing its download while the cursor stays put)."""
        sel = self.browser.selected
        if sel is None or sel.is_dir:
            return None  # only file previews change under a still cursor
        return (self.browser.path, sel.name, self._downloaded_local_path(sel) is not None)

    def _downloaded_local_path(self, entry):
        """The local mirror path if ``entry`` is a fully-downloaded file, else None."""
        if entry is None or entry.is_dir:
            return None
        remote = self.browser.child_path(entry.name)
        local = self._downloads._local_for(remote)
        if not local.is_file():
            return None
        done = any(
            it.remote_path == remote and it.status in ("done", "skipped")
            for it in self._downloads.items
        )
        if done or (entry.size and local.stat().st_size >= entry.size):
            return local
        return None

    def _maybe_translate_preview(self, entry, body: str, truncated: bool) -> None:
        """When translation is on, translate the text preview (bounded) off the event
        loop and re-render — exclusive so switching files cancels a stale translation."""
        if not (self.translate_enabled and self._translator and self._translator.available):
            return
        key = (self.browser.path, entry.name)
        self.run_worker(
            self._translate_preview(entry, body, truncated, key),
            group="preview-xlate",
            exclusive=True,
        )

    async def _translate_preview(self, entry, body: str, truncated: bool, key) -> None:
        import asyncio

        lines = body.splitlines()[:40]  # bounded: never translate a whole large file

        def work() -> str:
            return "\n".join(
                self._translator.translate(ln) if ln.strip() else ln for ln in lines
            )

        try:
            translated = await asyncio.to_thread(work)
        except Exception:
            return
        sel = self.browser.selected  # discard if the selection moved on
        if not self._show_preview or sel is None or (self.browser.path, sel.name) != key:
            return
        self.query_one("#preview", Column).show_preview(
            entry, "text", body, truncated,
            translated=self._name_translation(entry), translated_body=translated,
        )

    # --- file content viewer (windowed, lazy translate) -----------------------
    def _enter_file_view(self, entry) -> None:
        """Open a downloaded file's content in the columns — text (scroll + translate)
        or an xxd hex view for a binary. No-op (with a hint) for a file that isn't
        downloaded; downloads stay explicit."""
        local = self._downloaded_local_path(entry)
        if local is None:
            self._status_note(f"'{entry.name}' isn't downloaded yet — press 'd' first")
            return
        from smbex.preview import looks_binary
        from smbex.viewer import LazyHex, LazyLines

        try:
            with open(local, "rb") as f:
                binary = looks_binary(f.read(4096))
            source, kind = (LazyHex(local), "hex") if binary else (LazyLines(local), "text")
        except Exception as exc:
            self._status_error(exc)
            return
        self._view = _FileViewState(entry.name, source, kind)
        self.query_one("#parent", Column).add_class("hidden")  # focus on the file
        self._render_view()  # manages the preview column (shown only when translating)

    def _close_file_view(self) -> None:
        if self._view is None:
            return
        self._view.lazy.close()
        self._view = None
        # restore the columns to the user's toggle state
        self.query_one("#parent", Column).set_class(not self._show_parent, "hidden")
        self.query_one("#preview", Column).set_class(not self._show_preview, "hidden")

    def _view_rows(self) -> int:
        # The columns auto-size to content, so take the available height from their
        # container (minus the column's round border top+bottom).
        h = self.query_one("#columns").size.height
        return (h - 2) if h and h > 2 else 30

    def _view_page(self) -> int:
        """Lines/rows to advance for a page — one screenful of the (single) column."""
        return self._view_rows()

    def _view_translating(self) -> bool:
        return bool(
            self._view is not None
            and self._view.kind == "text"  # no translation for a hex view
            and self.translate_enabled
            and self._translator
            and self._translator.available
        )

    def _scroll_view(self, delta: int) -> None:
        v = self._view
        if v is None:
            return
        v.top = max(0, v.top + delta)
        self._render_view()

    def _render_view(self) -> None:
        v = self._view
        if v is None:
            return
        rows = self._view_rows()
        mid = self.query_one("#current", Column)
        right = self.query_one("#preview", Column)
        # One wide column, except the translation case which needs original | English.
        translating = self._view_translating()
        right.set_class(not translating, "hidden")

        if v.kind == "hex":  # xxd view — a single scrolling column
            v.top = max(0, min(v.top, max(0, v.lazy.rows - 1)))
            mid.show_lines(v.lazy.window(v.top, rows), gutter=False)
            self._update_status()
            return

        if v.lazy.eof:  # text: don't scroll past the end once the length is known
            v.top = max(0, min(v.top, v.lazy.loaded - 1))
        lines = v.lazy.window(v.top, rows)
        mid.show_lines(lines, start=v.top)
        if translating:  # middle = original, right = translation (aligned)
            translated = [v.translations.get(v.top + i) for i in range(len(lines))]
            right.show_lines([""] * len(lines), start=v.top, translated=translated)
            self._translate_view_window(v, v.top, lines)
        self._update_status()

    def _translate_view_window(self, v, top: int, lines: list) -> None:
        missing = [(top + i, ln) for i, ln in enumerate(lines) if (top + i) not in v.translations]
        if not missing:
            return
        self.run_worker(self._do_translate_view(v, missing), group="view-xlate", exclusive=True)

    async def _do_translate_view(self, v, missing) -> None:
        import asyncio

        translator = self._translator

        def work():
            return [(i, translator.translate(ln) if ln.strip() else "") for i, ln in missing]

        try:
            results = await asyncio.to_thread(work)
        except Exception:
            return
        if self._view is not v:  # viewer closed or a different file since
            return
        for i, text in results:
            v.translations[i] = text
        self._render_view()  # cached now -> no new worker spawned

    def _status_note(self, message: str) -> None:
        self.query_one("#status", Static).update(Text(f" {message}", style="yellow"))

    def _refresh_downloads(self) -> None:
        self.query_one("#downloads", DownloadPanel).render_items(self._downloads.items)
        self._update_status()

    def _on_downloads_change(self) -> None:
        try:
            self._refresh_downloads()
            if self._view is not None:
                return  # the content viewer owns the columns; don't repaint the browser
            self._render_current()  # keep the status gutter in sync with progress
            # If the file under the (unmoved) cursor just finished downloading, flip
            # its preview from metadata to content.
            sel = self.browser.selected
            if (
                self._show_preview
                and sel is not None
                and not sel.is_dir
                and self._preview_state() != self._preview_key
            ):
                self._render_preview(None)
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
        if self._view is not None:  # content-viewer status
            v = self._view
            if v.kind == "hex":
                unit, total, mode, hint = "row", v.lazy.rows, "hex", "j/k scroll · h/Esc back"
            else:
                unit = "line"
                total = f"{v.lazy.loaded}{'' if v.lazy.eof else '+'}"
                mode = "orig|translation" if self._view_translating() else "text"
                hint = "j/k scroll · t translate · h/Esc back"
            self.query_one("#status", Static).update(
                f" VIEW {v.name} │ {unit} {v.top + 1}/{total} │ {mode} │ {hint}"
            )
            return
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
