"""Append-only TSV record of every entry a session has seen (``--index FILE``).

Written for reading back later — by you, by ``grep``/``sort``, or by an AI agent
asked "which of these look interesting?". One line per file or folder:

    path <TAB> type <TAB> size <TAB> mtime [<TAB> translation]

``type`` is ``dir``/``file``, ``size`` is bytes (``-`` for a directory, whose
recursive size can't be known without walking it), ``mtime`` is a local
``YYYY-MM-DDTHH:MM`` (``-`` when the server didn't report one), and the optional
fifth column is the English rendering when filename translation is on.

**It never fetches anything.** The index is fed from listings the session was
already going to pull — browsing, preloading, and the enumeration a recursive
download does — through ``Gateway.on_listing``. A folder served from the session
cache never reaches the gateway, so revisiting costs nothing and logs nothing
twice. There is no code path in which the index asks for data.

Deliberately distinct from the listing cache, which is session-only and never
persisted (see CLAUDE.md): this file is an explicit, opt-in export, so writing
metadata to disk is always something the operator asked for by name.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Callable, Iterable

from smbex.backend.base import DirEntry

#: Column header, written once when the file is created.
COLUMNS = "# path\ttype\tsize\tmtime\ttranslation(optional)"


def _escape(text: str) -> str:
    """Make a name safe for one TSV field (POSIX names may hold tabs/newlines)."""
    return (
        text.replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _stamp(mtime: float) -> str:
    if not mtime or mtime <= 0:
        return "-"
    try:
        return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%dT%H:%M")
    except (OverflowError, OSError, ValueError):
        return "-"  # a nonsense timestamp from the server must not stop the write


class ListingIndex:
    """Collects listings into a TSV file, one line per entry, without duplicates.

    Opens lazily on the first entry, so a session that never lists anything leaves
    no file behind. Before the first write, an existing file is read back (locally —
    no network) to seed the set of already-recorded paths, so pointing successive
    runs at the same file accumulates a share instead of repeating it.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        target: str = "",
        translate: Callable[[str, bool], str | None] | None = None,
    ):
        self.path = Path(path).expanduser()
        self.target = target
        #: Set by the UI to supply the English rendering of a name, or None.
        self.translate = translate
        self.written = 0
        self._seen: set[str] = set()
        self._primed = False
        self._file = None

    # --- lifecycle ------------------------------------------------------------
    def _prime(self) -> None:
        """Read an existing file's paths once, before deciding what is new.

        Has to happen ahead of the first write, not with the first ``open`` — the
        de-duplication decision is made before there is anything to open.
        """
        if self._primed:
            return
        self._primed = True
        if self.path.is_file():
            self._load_seen()

    def _open(self) -> None:
        if self._file is not None:
            return
        existing = self.path.is_file()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "a", encoding="utf-8")
        if not existing:
            self._file.write(f"{COLUMNS}\n")
        started = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self._file.write(f"# smbex index  target={self.target or '-'}  session={started}\n")
        self._file.flush()

    def _load_seen(self) -> None:
        """Seed the de-duplication set from a previous run's lines."""
        try:
            with open(self.path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line and not line.startswith("#"):
                        self._seen.add(line.split("\t", 1)[0])
        except OSError:
            pass  # unreadable existing file: log everything rather than nothing

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    # --- writing --------------------------------------------------------------
    def add(self, path: str, entries: Iterable[DirEntry]) -> int:
        """Record the entries of directory ``path``; returns how many were new.

        Safe to call with anything: a failure to write must never interrupt
        browsing, so I/O errors are swallowed (the index is a side-effect, not the
        job). Called from the gateway with listings that were fetched anyway.
        """
        try:
            return self._add(path, entries)
        except Exception:  # noqa: BLE001 - never let the index break a browse
            return 0

    def _add(self, path: str, entries: Iterable[DirEntry]) -> int:
        self._prime()
        base = f"/{path}" if path else ""
        lines = []
        for entry in entries:
            full = f"{base}/{entry.name}"
            if full in self._seen:
                continue
            self._seen.add(full)
            fields = [
                _escape(full),
                "dir" if entry.is_dir else "file",
                "-" if entry.is_dir else str(entry.size),
                _stamp(entry.mtime),
            ]
            rendered = self.translate(entry.name, entry.is_dir) if self.translate else None
            if rendered and rendered != entry.name:
                fields.append(_escape(rendered))
            lines.append("\t".join(fields))
        if not lines:
            return 0
        self._open()
        self._file.write("\n".join(lines) + "\n")
        self._file.flush()  # a session that ends badly still leaves what it saw
        self.written += len(lines)
        return len(lines)
