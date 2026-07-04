"""Navigation controller: current directory, cursor, and cache-backed listings.

Kept free of any Textual dependency so the ranger-style navigation logic is unit
testable on its own. The UI (``smbex/ui/app.py``) renders this state and turns key
presses into calls here.

Listings go through the in-session cache, so revisiting a folder is instant and
never re-hits the backend within a run. Cursor position per directory is
remembered, so stepping back into a parent lands on the child you came from —
like ranger.
"""

from __future__ import annotations

from smbex.backend.base import DirEntry
from smbex.cache import ListingCache
from smbex.gateway import Gateway, Priority
from smbex.preload import Preloader


def _join(path: str, name: str) -> str:
    return name if not path else f"{path}/{name}"


def _parent(path: str) -> str:
    return "" if "/" not in path else path.rsplit("/", 1)[0]


def _base(path: str) -> str:
    return path.rsplit("/", 1)[-1] if path else ""


def _sort(entries: list[DirEntry]) -> list[DirEntry]:
    # Directories first, then case-insensitive by name (ranger-like). This is also
    # the canonical order stored in the cache; the active view sort is applied on top.
    return sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))


#: View sort modes cycled by the 'o' key; ``mtime`` is the only cross-protocol time.
SORT_MODES = ("name", "mtime_desc", "mtime_asc")
SORT_LABELS = {"name": "name", "mtime_desc": "newest", "mtime_asc": "oldest"}


class Browser:
    def __init__(self, gateway: Gateway, cache: ListingCache | None = None, *, preload: bool = False):
        self.gateway = gateway
        self.cache: ListingCache[list[DirEntry]] = cache if cache is not None else ListingCache()
        self.preload_enabled = preload
        self.preloader = Preloader(gateway, self.cache)
        self.path = ""  # current directory ("" == roots / share picker)
        self.cursor = 0
        self.entries: list[DirEntry] = []
        self.sort_mode = "name"
        self._cursor_memory: dict[str, int] = {}

    def _sorted(self, entries: list[DirEntry]) -> list[DirEntry]:
        """Apply the active view sort. mtime modes ignore dirs-first (newest/oldest
        wins regardless of type), with name as a stable tiebreak."""
        if self.sort_mode == "mtime_desc":
            return sorted(entries, key=lambda e: (-e.mtime, e.name.lower()))
        if self.sort_mode == "mtime_asc":
            return sorted(entries, key=lambda e: (e.mtime, e.name.lower()))
        return _sort(entries)

    def cycle_sort(self) -> str:
        """Advance to the next sort mode and re-sort the current view in place,
        keeping the selected entry under the cursor. Returns the new mode."""
        self.sort_mode = SORT_MODES[(SORT_MODES.index(self.sort_mode) + 1) % len(SORT_MODES)]
        selected = self.selected
        self.entries = self._sorted(self.entries)
        if selected is not None:
            for i, entry in enumerate(self.entries):
                if entry.name == selected.name:
                    self.cursor = i
                    self._cursor_memory[self.path] = i
                    break
        return self.sort_mode

    async def listdir(self, path: str, priority: int = Priority.BROWSE) -> list[DirEntry]:
        cached = self.cache.get(path)
        if cached is not None:
            return cached
        entries = _sort(await self.gateway.list(path, priority=priority))
        self.cache.put(path, entries)
        return entries

    async def load(self, path: str | None = None) -> list[DirEntry]:
        if path is not None:
            self.path = path
        self.entries = self._sorted(await self.listdir(self.path))
        remembered = self._cursor_memory.get(self.path, 0)
        self.cursor = min(max(remembered, 0), len(self.entries) - 1) if self.entries else 0
        self.preload_surroundings()  # warm neighbouring folders (Phase 6; toggle-gated)
        return self.entries

    def preload_surroundings(self) -> None:
        """Kick off (toggle-gated) prefetch of the current view's neighbours.

        Fire-and-forget: spawns background jobs at ``Priority.PRELOAD`` and returns
        at once, so navigation stays instant. Called on every ``load`` and also when
        the ``p`` toggle is switched on so the current neighbourhood warms right away.
        """
        self.preloader.preload(
            self.path, self.entries, self.selected, enabled=self.preload_enabled
        )

    @property
    def selected(self) -> DirEntry | None:
        return self.entries[self.cursor] if 0 <= self.cursor < len(self.entries) else None

    @property
    def count(self) -> int:
        return len(self.entries)

    def move(self, delta: int) -> None:
        self.move_to(self.cursor + delta)

    def move_to(self, index: int) -> None:
        if not self.entries:
            return
        self.cursor = min(max(index, 0), len(self.entries) - 1)
        self._cursor_memory[self.path] = self.cursor

    async def enter(self) -> bool:
        sel = self.selected
        if sel is None or not sel.is_dir:
            return False
        self._cursor_memory[self.path] = self.cursor
        await self.load(_join(self.path, sel.name))
        return True

    async def go_parent(self) -> bool:
        if not self.path:
            return False
        child = _base(self.path)
        await self.load(_parent(self.path))
        for i, entry in enumerate(self.entries):
            if entry.name == child:
                self.cursor = i
                self._cursor_memory[self.path] = i
                break
        return True

    async def parent_entries(self) -> list[DirEntry]:
        if not self.path:
            return []
        return self._sorted(await self.listdir(_parent(self.path)))

    async def preview_entries(self) -> list[DirEntry] | None:
        """Listing of the selected directory, or ``None`` when a file is selected."""
        sel = self.selected
        if sel is not None and sel.is_dir:
            return self._sorted(await self.listdir(_join(self.path, sel.name)))
        return None

    def child_path(self, name: str) -> str:
        return _join(self.path, name)

    def parent_cursor(self, parent_entries: list[DirEntry]) -> int | None:
        name = _base(self.path)
        for i, entry in enumerate(parent_entries):
            if entry.name == name:
                return i
        return None
