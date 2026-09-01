"""The Textual application: a ranger-style, three-column browser.

Layout is Miller columns (parent | current | preview). Navigation mirrors ranger:
``h/j/k/l`` (+ arrows), ``g``/``G`` for top/bottom. Downloads run in the background
and never block browsing: ``d`` downloads the selected item (a file, or a directory
recursively), ``a`` grabs every file in the current folder, and ``w`` toggles the
task panel. ``t`` (translation) is reserved for a later phase. Dark mode is default.

The app owns the gateway and download-manager lifecycles and renders a ``Browser``.
"""

from __future__ import annotations

from functools import partial
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

    #: Seconds to wait before fetching the parent/preview listings after the cursor
    #: moves. A held-down j/k walks over many entries; without this, each stop would
    #: queue its own listing on the wire and the last one would land seconds late.
    #: Only the *fetch* is delayed — the cursor and the current column never wait.
    SIDE_REFRESH_DELAY = 0.12

    CSS = """
    #columns { height: 1fr; }
    Column {
        width: 1fr;
        height: 100%;      /* fill the row: Column.window() sizes its render to this */
        border: round $panel;
        padding: 0 1;
        overflow: hidden hidden;
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
        Binding("y", "copy_name", "Copy name"),
        Binding("Y", "copy_path", "Copy path"),
        Binding("w", "toggle_downloads", "Tasks"),
        Binding("J", "panel_lower", "Lower prio", show=False),
        Binding("K", "panel_raise", "Raise prio", show=False),
        Binding("x", "panel_cancel", "Cancel dl", show=False),
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
        flat: bool = False,
        translator: Translator | None = None,
        sort: str = "name",
        theme: str = "dark",
        download_panel: str = "auto",
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
        self._downloads = DownloadManager(gateway, Path(download_root), flat=flat)
        self._start_path = start_path
        self._label = label
        self._translator = translator
        # On when a language was configured (--translate); 't' toggles the display.
        self.translate_enabled = translator is not None
        # Task panel: "auto" shows the transfers in flight and hides itself when the
        # queue drains; "hidden" only ever appears on 'w'. 'w' opens the full list
        # (finished entries included) and takes j/k for selection + J/K reordering.
        self._panel_mode = download_panel if download_panel in ("auto", "hidden") else "auto"
        self._panel_open = False
        self._panel_cursor = 0
        self._conn_state = "connected"  # updated by the gateway on link changes
        self._preview_key = None  # what the preview last rendered (to re-render on change)
        self._side_seq = 0  # scheduled deferred side-column refreshes ...
        self._side_ack = 0  # ... and finished ones (equal == nothing pending)
        self._dl_paint = (0.0, ())  # last download repaint: (time, status signature)
        self._note: tuple[str, float] | None = None  # transient status-bar message
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
        self.browser.preloader.on_warm = self._on_preload_warm  # live cached-marker
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
        self.workers.cancel_group(self, "side")  # deferred column fetches
        await self._downloads.stop()  # stop before the gateway it depends on
        await self.browser.preloader.stop()  # cancel prefetches before the gateway
        await self._gateway.stop()

    def on_resize(self, event) -> None:
        if self._view is not None:  # re-fit the content window to the new height
            self._render_view()

    # --- navigation actions ---------------------------------------------------
    async def action_cursor_down(self) -> None:
        if self._panel_open:
            self._panel_move(1)
            return
        if self._view is not None:
            self._scroll_view(1)
            return
        self.browser.move(1)
        await self._refresh()

    async def action_cursor_up(self) -> None:
        if self._panel_open:
            self._panel_move(-1)
            return
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
        if self._panel_open or self._view is not None:
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
        if self._panel_open:  # 'h' backs out of the task panel, like everywhere else
            self.action_toggle_downloads()
            return
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
        if self._panel_open:  # Esc closes the task panel first
            self.action_toggle_downloads()
            return
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
        cache = self.browser.cache
        base = self.browser.path
        prefix = f"{base}/" if base else ""
        # Bucket the download items by the current dir's child they live under, in one
        # pass — a per-entry scan would be O(entries x downloads) on every repaint.
        by_child: dict[str, set[str]] = {}
        for item in self._downloads.items:
            if not item.remote_path.startswith(prefix):
                continue
            rest = item.remote_path[len(prefix):]
            if rest:
                by_child.setdefault(rest.split("/", 1)[0], set()).add(item.status)
        markers: list[str] = []
        for entry in entries:
            statuses = by_child.get(entry.name)
            glyph = " "
            if statuses:
                if statuses & {"queued", "running"}:
                    glyph = "↓"
                elif "error" in statuses:
                    glyph = "✗"
                elif "cancelled" in statuses:
                    glyph = "⊘"  # stopped part-way; whatever arrived is on disk
                elif statuses <= {"done", "skipped"}:
                    glyph = "✓"
            if glyph == " " and entry.is_dir and self.browser.child_path(entry.name) in cache:
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
        self._refresh_downloads()

    async def action_download_all(self) -> None:
        files = [
            (self.browser.child_path(e.name), e.size)
            for e in self.browser.entries
            if not e.is_dir
        ]
        if files:
            await self._downloads.add_files(files)
            self._refresh_downloads()

    def action_toggle_downloads(self) -> None:
        """'w': open the full task list (and take j/k for it), or close it again."""
        self._panel_open = not self._panel_open
        if self._panel_open:
            items = self._panel_items()
            # Start on the first thing still moving — with a long queue that's what
            # you came to look at.
            self._panel_cursor = next(
                (i for i, it in enumerate(items) if it.status in ("running", "queued")), 0
            )
        self._refresh_downloads()

    def _panel_move(self, delta: int) -> None:
        items = self._panel_items()
        if items:
            self._panel_cursor = min(max(self._panel_cursor + delta, 0), len(items) - 1)
        self._refresh_downloads()

    def _panel_reorder(self, delta: int) -> None:
        """Move the selected *queued* transfer through the queue (J/K)."""
        items = self._panel_items()
        if not (self._panel_open and 0 <= self._panel_cursor < len(items)):
            return
        item = items[self._panel_cursor]
        was_running = item.status == "running"
        result = self._downloads.reorder(item, delta)
        if result in ("moved", "preempted"):
            moved = self._panel_items()
            if item in moved:
                self._panel_cursor = moved.index(item)  # follow the item
            if result == "preempted":
                self._status_note(
                    f"{item.remote_path} yields at {int(item.progress * 100)}% — "
                    "it resumes from there when its turn comes round"
                    if was_running
                    else f"{item.remote_path} goes first — the running transfer yields"
                )
        elif result == "uninterruptible":
            self._status_note(self._uninterruptible_note())
        elif item.status in ("running", "queued"):
            self._status_note("nothing to swap with — it's already at the end")
        self._refresh_downloads()

    def _uninterruptible_note(self) -> str:
        """Why a running transfer can't be stopped on this protocol."""
        return (
            "FTP sends the rest of the file whether or not we keep it, so a running "
            "transfer can't be stopped early — let it finish (queued ones can be "
            "cancelled and reordered)"
        )

    def action_panel_cancel(self) -> None:
        """'x': cancel the selected transfer, or clear an entry that's finished."""
        items = self._panel_items()
        if not (self._panel_open and 0 <= self._panel_cursor < len(items)):
            return
        item = items[self._panel_cursor]
        what = self._downloads.cancel(item)
        if what == "uninterruptible":
            self._status_note(self._uninterruptible_note())
        elif what == "stopping":
            self._status_note(
                f"cancelling {item.remote_path} — the {int(item.progress * 100)}% "
                "already fetched is kept, so 'd' resumes it"
            )
        elif what == "cancelled":
            self._status_note(f"cancelled {item.remote_path}")
        else:
            self._status_note(f"cleared {item.remote_path}")
        self._panel_cursor = min(self._panel_cursor, max(0, len(self._panel_items()) - 1))
        self._refresh_downloads()

    def action_panel_raise(self) -> None:
        self._panel_reorder(-1)

    def action_panel_lower(self) -> None:
        self._panel_reorder(1)

    # --- clipboard ------------------------------------------------------------
    # The listing panes render as a rich Table for column alignment, which Textual's
    # native text selection can't extract from (it only reads Text/Content visuals).
    # These keys copy the selected entry directly instead — full, untruncated, and
    # unaffected by on-screen ellipsis. In the content viewer, drag-select + ctrl+c
    # already works (that pane is a Text), so 'y'/'Y' just copy the viewed file here.
    def action_copy_name(self) -> None:
        self._copy_selection(full_path=False, label="name")

    def action_copy_path(self) -> None:
        self._copy_selection(full_path=True, label="path")

    def _copy_selection(self, *, full_path: bool, label: str) -> None:
        sel = self.browser.selected
        if sel is None:
            self._status_note("nothing selected to copy")
            return
        text = self._copy_text_for(sel, full_path=full_path)
        self.copy_to_clipboard(text)
        self._status_note(f"copied {label}: {text}")

    def _copy_text_for(self, entry, *, full_path: bool) -> str:
        """The clipboard string for ``entry``: bare name, or the full remote path
        (as shown in the status bar). When translation is active, append the English
        rendering so the copy matches what's on screen."""
        base = "/" + self.browser.child_path(entry.name) if full_path else entry.name
        translated = self._name_translation(entry)
        if translated and translated != entry.name:
            return f"{base} → {translated}"
        return base

    # --- rendering ------------------------------------------------------------
    # Repainting is split in two so a keystroke never waits on the network:
    #   _render_now()            everything already in memory/cache — instant;
    #   _schedule_side_refresh() fetches only what's missing, later, in a worker.
    # A dropped link therefore can't blank the view you're on, and holding down 'j'
    # over a hundred folders costs one listing fetch (of where you stopped), not a
    # hundred queued behind each other on a slow link.
    async def _refresh(self) -> None:
        self._render_now()
        self._schedule_side_refresh()

    def _render_now(self) -> None:
        """Paint every column from in-memory state — no backend call, no awaiting.

        A side column whose listing isn't cached yet shows a placeholder rather than
        a stale neighbour's listing; the deferred refresh fills it in.
        """
        browser = self.browser
        self._render_current()
        if self._show_parent:
            parent_path = browser.parent_path
            parent = [] if parent_path is None else browser.peek(parent_path)
            if parent is None:
                self.query_one("#parent", Column).show_loading()
            else:
                self.query_one("#parent", Column).show(
                    parent,
                    cursor=browser.parent_cursor(parent),
                    translations=self._entry_translations(parent),
                )
        if self._show_preview:
            preview_path = browser.preview_path
            if preview_path is None:  # a file (or nothing) is selected
                self._render_preview(None)
            else:
                preview = browser.peek(preview_path)
                if preview is None:
                    self.query_one("#preview", Column).show_loading()
                    self._preview_key = self._preview_state()
                else:
                    self._render_preview(preview)
        self._refresh_downloads()

    def _side_key(self) -> tuple:
        """What the side columns are showing — used to drop a stale fetch."""
        return (self.browser.path, self.browser.parent_path, self.browser.preview_path)

    def _missing_side_paths(self) -> list[str]:
        """Visible side-column listings that aren't cached yet (so need the wire)."""
        browser = self.browser
        wanted = []
        if self._show_parent and browser.parent_path is not None:
            wanted.append(browser.parent_path)
        if self._show_preview and browser.preview_path is not None:
            wanted.append(browser.preview_path)
        return [path for path in wanted if browser.peek(path) is None]

    def _schedule_side_refresh(self) -> None:
        """Fetch the not-yet-cached side listings after a short settle, in a worker.

        Exclusive: a newer cursor position cancels this one — during the settle that
        means the listing is never even requested, which is what keeps a fast scroll
        off the wire entirely.
        """
        if not self._missing_side_paths():
            self._side_ack = self._side_seq  # nothing to wait for
            return
        self._side_seq += 1
        # A *callable*, not a coroutine: an exclusive worker replaced before it starts
        # would otherwise leave an un-awaited coroutine behind (RuntimeWarning), and a
        # fast scroll replaces plenty of them.
        self.run_worker(
            partial(self._side_refresh, self._side_seq, self._side_key()),
            group="side",
            exclusive=True,
        )

    async def _side_refresh(self, seq: int, key: tuple) -> None:
        import asyncio

        try:
            if self.SIDE_REFRESH_DELAY:
                await asyncio.sleep(self.SIDE_REFRESH_DELAY)  # coalesce a key repeat
            for path in self._missing_side_paths():
                if self._side_key() != key or self._view is not None:
                    return  # moved on (or the file viewer took the columns)
                try:
                    await self.browser.listdir(path)
                except Exception:
                    pass  # unreadable/dropped: leave the placeholder, keep browsing
            if self._side_key() == key and self._view is None:
                self._render_now()
        finally:
            self._side_ack = max(self._side_ack, seq)

    async def wait_for_side_refresh(self) -> None:
        """Await the deferred side-column fetch (tests and scripted flows)."""
        import asyncio

        while self._side_ack < self._side_seq:
            await asyncio.sleep(0.005)

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
            partial(self._translate_preview, entry, body, truncated, key),
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
        self.run_worker(
            partial(self._do_translate_view, v, missing), group="view-xlate", exclusive=True
        )

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

    #: How long a one-off message (copied, cancelled, yielded…) keeps the status bar.
    NOTE_SECONDS = 4.0

    def _status_note(self, message: str) -> None:
        """Say something in the status bar and keep it there for a few seconds.

        Without the hold, the next repaint — a download reporting progress, or the
        panel refresh that follows the very action being reported — would wipe the
        message before it could be read.
        """
        import time

        self._note = (message, time.monotonic())
        self.query_one("#status", Static).update(Text(f" {message}", style="yellow"))

    def _panel_items(self) -> list:
        """What the panel lists: everything while open, else only what's in flight."""
        return self._downloads.items if self._panel_open else self._downloads.pending

    def _panel_summary(self, items: list) -> str:
        all_items = self._downloads.items
        finished = sum(1 for i in all_items if i.status in ("done", "skipped"))
        failed = sum(1 for i in all_items if i.status == "error")
        cancelled = sum(1 for i in all_items if i.status == "cancelled")
        parts = [f"downloads {finished}/{len(all_items)}"]
        if failed:
            parts.append(f"{failed} failed")
        if cancelled:
            parts.append(f"{cancelled} cancelled")
        if self._panel_open:
            if self._downloads.can_interrupt:
                parts.append("j/k select · J/K reprioritize · x cancel · w close")
            else:  # FTP: the running transfer owns the wire until it finishes
                parts.append("j/k select · J/K/x queued only · w close")
        elif len(items) > 1:
            parts.append("w for the full list")
        return " · ".join(parts)

    def _refresh_downloads(self) -> None:
        """Render the task panel — and decide whether it should be on screen at all.

        Open: the full list with a cursor. Otherwise it appears only while transfers
        are in flight (and only shows those), and disappears when they finish, so it
        stops covering the browser the moment it has nothing to say.
        """
        panel = self.query_one("#downloads", DownloadPanel)
        items = self._panel_items()
        visible = self._panel_open or (self._panel_mode == "auto" and bool(items))
        panel.set_class(not visible, "hidden")
        if visible:
            if self._panel_open and items:
                self._panel_cursor = min(max(self._panel_cursor, 0), len(items) - 1)
            panel.render_items(
                items,
                cursor=self._panel_cursor if self._panel_open else None,
                max_rows=12 if self._panel_open else 4,
                summary=self._panel_summary(items),
            )
        self._update_status()

    def _should_paint_downloads(self) -> bool:
        """Rate-limit progress repaints (a fast transfer reports every 256 KB), but
        never delay a state change — that's what the gutter and the panel show."""
        import time

        signature = tuple(item.status for item in self._downloads.items)
        now = time.monotonic()
        last_at, last_signature = self._dl_paint
        if signature != last_signature or now - last_at >= 0.1:
            self._dl_paint = (now, signature)
            return True
        return False

    def _on_downloads_change(self) -> None:
        if not self._should_paint_downloads():
            return
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

    def _on_preload_warm(self, path: str) -> None:
        """Preloader filled the cache for ``path`` — repaint the '·' marker live.

        Markers live only on the current column, so a warm matters only when ``path``
        is a direct child of the current directory (a visible sibling/selection); a
        parent-directory warm changes nothing on screen. The content viewer owns the
        columns while open, so skip then.
        """
        try:
            if self._view is not None:
                return
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            if parent == self.browser.path:
                self._render_current()
        except Exception:
            pass  # widgets may be gone during teardown

    def _on_conn_status(self, state: str) -> None:
        """Gateway link-state callback ('reconnecting'/'connected'/'disconnected')."""
        self._conn_state = state
        self._note = None  # a link change outranks whatever was being said
        try:
            self._update_status()
        except Exception:
            pass  # widgets may be gone during teardown

    def _update_status(self) -> None:
        import time

        if self._note is not None:
            message, at = self._note
            if time.monotonic() - at < self.NOTE_SECONDS:
                self.query_one("#status", Static).update(Text(f" {message}", style="yellow"))
                return
            self._note = None
        if self._panel_open:  # the task panel owns the keys while it's open
            items = self._panel_items()
            where = f"{min(self._panel_cursor + 1, len(items))}/{len(items)}" if items else "0/0"
            self.query_one("#status", Static).update(
                f" TASKS {where} │ j/k select · J/K reprioritize · x cancel/clear"
                " · w/h/Esc close"
            )
            return
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
            # The panel hides itself once the queue drains, so the tiny live readout
            # here is what tells you a transfer is still going (and how far along).
            running = next((i for i in items if i.status == "running"), None)
            if running is not None:
                text += f" ↓{int(running.progress * 100)}%"
            elif any(i.status == "queued" for i in items):
                text += " ↓queued"
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
