"""Phase 6 — surrounding-folder preloader.

When enabled (the ``p`` toggle), the preloader warms the in-session listing cache
for the folders adjacent to the current one, so a following ``l`` (enter the
selection), ``j``/``k`` then ``l`` (enter a sibling), or ``h`` (go to the parent)
is served from cache instead of the wire.

Prefetches run at ``Priority.PRELOAD`` — below browsing, above downloads — so they
consume only the bandwidth active navigation leaves free and never delay a
keystroke. The work is fire-and-forget: the caller (the browser, on navigation) is
never blocked, and a folder that won't list on preload is simply left uncached; a
later real browse surfaces the error.

Neighbourhood of the current directory, in fetch order (best first):
  * the **selected child** — the directory under the cursor, the most likely ``l``;
  * its **siblings** — the other subdirectories of the current folder;
  * the **parent** directory.

Already-cached and already-in-flight paths are skipped, so preloading is idempotent
and avoids duplicate wire traffic on a slow link. Listings are cached in the same
sort order the browser uses, so a preload-warmed folder renders identically to a
browsed one.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from smbex.backend.base import DirEntry
from smbex.cache import ListingCache
from smbex.gateway import Gateway, Priority


def _join(path: str, name: str) -> str:
    return name if not path else f"{path}/{name}"


def _parent(path: str) -> str:
    return "" if "/" not in path else path.rsplit("/", 1)[0]


def _sort(entries: list[DirEntry]) -> list[DirEntry]:
    # Must match smbex.browser._sort so a preloaded listing renders like a browsed one.
    return sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))


class Preloader:
    """Warms the cache for a directory's neighbours at ``Priority.PRELOAD``."""

    def __init__(
        self, gateway: Gateway, cache: ListingCache, *, priority: int = Priority.PRELOAD
    ):
        self.gateway = gateway
        self.cache = cache
        self.priority = int(priority)
        self._tasks: set[asyncio.Task] = set()
        self._inflight: set[str] = set()
        #: Called (on the event loop) with a path just warmed into the cache, so a
        #: view can repaint its "listing cached" marker live instead of only on the
        #: next navigation. Set by the UI; left None for headless/test use.
        self.on_warm: Callable[[str], None] | None = None

    def neighbors(
        self, path: str, entries: list[DirEntry], selected: DirEntry | None
    ) -> list[str]:
        """Paths worth preloading for the current view, best-first, de-duplicated.

        The selected subdirectory first, then the other subdirectories of ``path``
        (siblings), then the parent. Files are never targets.
        """
        targets: list[str] = []
        if selected is not None and selected.is_dir:
            targets.append(_join(path, selected.name))
        for entry in entries:
            if entry.is_dir:
                targets.append(_join(path, entry.name))
        if path:  # roots ("") have no parent
            targets.append(_parent(path))

        seen: set[str] = set()
        ordered: list[str] = []
        for target in targets:
            if target not in seen:
                seen.add(target)
                ordered.append(target)
        return ordered

    def preload(
        self,
        path: str,
        entries: list[DirEntry],
        selected: DirEntry | None,
        *,
        enabled: bool = True,
    ) -> list[asyncio.Task]:
        """Fire-and-forget prefetch of the current view's neighbours.

        No-op when ``enabled`` is false (the toggle is off). Returns the spawned
        tasks so a caller (or a test) can await them; navigation itself never does.
        """
        if not enabled:
            return []
        spawned: list[asyncio.Task] = []
        for target in self.neighbors(path, entries, selected):
            if target in self._inflight or target in self.cache:
                continue
            self._inflight.add(target)
            task = asyncio.create_task(self._warm(target))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            spawned.append(task)
        return spawned

    async def _warm(self, path: str) -> None:
        try:
            entries = await self.gateway.list(path, priority=self.priority)
            if path not in self.cache:  # a real browse may have filled it meanwhile
                self.cache.put(path, _sort(entries))
                if self.on_warm is not None:
                    self.on_warm(path)  # let the UI repaint the "cached" marker now
        except Exception:
            pass  # unreadable on preload: leave uncached; a real browse will surface it
        finally:
            self._inflight.discard(path)

    async def wait(self) -> None:
        """Await all outstanding prefetches (a test / shutdown aid)."""
        while self._tasks:
            pending = tuple(self._tasks)
            self._tasks.clear()
            await asyncio.gather(*pending, return_exceptions=True)

    async def stop(self) -> None:
        """Cancel outstanding prefetches — call before tearing down the gateway."""
        pending = tuple(self._tasks)
        self._tasks.clear()
        self._inflight.clear()
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
