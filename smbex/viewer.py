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


class LazyHex:
    """Windowed xxd-style hex view of a local file — 16 bytes per row, random-access.

    Seeks straight to the requested rows, so scrolling a large binary reads only the
    visible window; the total row count is known up front from the file size."""

    def __init__(self, path: str | Path, width: int = 16):
        self._fh = open(path, "rb")
        self._width = width
        self._size = self._fh.seek(0, 2)  # end -> size

    def window(self, top: int, height: int) -> list[str]:
        if height <= 0:
            return []
        start = max(top, 0) * self._width
        self._fh.seek(start)
        data = self._fh.read(height * self._width)
        rows = []
        for i in range(0, len(data), self._width):
            off = start + i
            chunk = data[i : i + self._width]
            hexs = " ".join(f"{b:02x}" for b in chunk).ljust(self._width * 3 - 1)
            ascii_ = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
            rows.append(f"{off:08x}  {hexs}  {ascii_}")
        return rows

    @property
    def rows(self) -> int:
        return (self._size + self._width - 1) // self._width

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass
