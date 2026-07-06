"""Windowed, lazy line reader for viewing a *downloaded* text file.

Reads only enough of the file to fill the visible window (plus whatever has already
been scrolled past), so a large file opens instantly and is never loaded in full
unless the user scrolls to the very end. The file is already local (downloaded); the
download behaviour is unchanged and stays explicit.
"""

from __future__ import annotations

from pathlib import Path


class LazyLines:
    """A text file read on demand, one batch at a time, keeping lines seen so far."""

    def __init__(self, path: str | Path, chunk_bytes: int = 65536):
        # Universal newlines (default) so \r\n collapses to \n before we strip it.
        self._fh = open(path, "r", encoding="utf-8", errors="replace")
        self._chunk = chunk_bytes
        self._lines: list[str] = []
        self._eof = False

    def _read_more(self) -> None:
        batch = self._fh.readlines(self._chunk)  # ~chunk_bytes worth of lines
        if not batch:
            self._eof = True
            return
        self._lines.extend(line.rstrip("\n") for line in batch)

    def ensure(self, count: int) -> None:
        """Make sure at least ``count`` lines are loaded (or the whole file if fewer)."""
        while not self._eof and len(self._lines) < count:
            self._read_more()

    def window(self, top: int, height: int) -> list[str]:
        """The ``height`` lines starting at ``top`` (0-based), loading as needed."""
        if height <= 0:
            return []
        top = max(top, 0)
        self.ensure(top + height)
        return self._lines[top : top + height]

    def load_all(self) -> None:
        """Read the rest of the file (used only on an explicit jump-to-end)."""
        while not self._eof:
            self._read_more()

    @property
    def loaded(self) -> int:
        return len(self._lines)

    @property
    def eof(self) -> bool:
        return self._eof

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass
