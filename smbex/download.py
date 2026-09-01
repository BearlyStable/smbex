"""Background download manager.

Downloads run on an asyncio worker, one file at a time, streaming each file in
chunks through the gateway at DOWNLOAD priority. Because every chunk is a separate
low-priority gateway job, a queued browse request is served between chunks —
browsing stays responsive and downloads take only the leftover bandwidth.

Two local layouts (``flat=False|True``): the remote tree is **mirrored** under
``root`` (``root / share / dir / file``), or **flattened** so every file lands
directly in ``root`` with its remote path folded into the name
(``share/2024/report.pdf`` -> ``share_2024_report.pdf``) — one folder per host, no
digging, and the origin still readable. Existing-file policy (default ``resume``): continue a partial file from where it
stopped, and skip files already fully present; ``overwrite`` re-fetches; ``skip``
never touches an existing path.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from smbex.gateway import Gateway, Priority

CHUNK = 256 * 1024

#: Joins the remote path's components in a flat filename.
FLAT_SEP = "_"
#: Marks a de-duplication counter — deliberately *not* FLAT_SEP, so "a~2.txt" can
#: never be confused with a real remote component named "2".
DEDUPE_SEP = "~"
#: Path separators, control characters and the chars that aren't portable in a
#: filename (Windows/SMB/exFAT); everything else, incl. non-ASCII, is kept as-is.
_UNSAFE = re.compile(r'[\x00-\x1f<>:"|?*\\/]')


def _fit(name: str, max_bytes: int) -> str:
    """Truncate to ``max_bytes`` *bytes* (not characters — a filesystem limit is in
    bytes and CJK names are 3 bytes a character), keeping the extension."""
    if len(name.encode()) <= max_bytes:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or len(ext) > 16:  # no usable extension: truncate the whole name
        stem, dot, ext = name, "", ""
    keep = max(1, max_bytes - len(f"{dot}{ext}".encode()))
    stem = stem.encode()[:keep].decode(errors="ignore")  # drop a split character
    return f"{stem}{dot}{ext}"


def flat_name(remote_path: str, *, max_bytes: int = 200) -> str:
    """The single filename encoding a whole remote path, for the flat layout.

    ``share/2024/report.pdf`` -> ``share_2024_report.pdf``. Left under a 255-byte
    filesystem limit with room for a ``~N`` de-duplication suffix.
    """
    parts = [p for p in remote_path.replace("\\", "/").split("/") if p and p != ".."]
    name = FLAT_SEP.join(_UNSAFE.sub("_", part) for part in parts)
    return _fit(name or "download", max_bytes)


@dataclass
class DownloadItem:
    remote_path: str
    local_path: Path
    size: int = 0
    downloaded: int = 0
    status: str = "queued"  # queued | running | done | skipped | error
    error: str = ""

    @property
    def progress(self) -> float:
        if self.status in ("done", "skipped"):
            return 1.0
        if self.size <= 0:
            return 0.0
        return min(self.downloaded / self.size, 1.0)


class DownloadManager:
    def __init__(
        self,
        gateway: Gateway,
        root: Path | str,
        *,
        exists_policy: str = "resume",
        flat: bool = False,
        on_change: Callable[[], None] | None = None,
    ):
        self.gateway = gateway
        self.root = Path(root)
        self.exists_policy = exists_policy
        self.flat = flat
        self.on_change = on_change
        # Flat layout only: local path <-> remote path, so a name is assigned once
        # (stable for resume and for the preview's "is this downloaded?" lookup) and
        # two different remote paths can never land on the same file.
        self._assigned: dict[str, Path] = {}
        self._claimed: dict[Path, str] = {}
        self.items: list[DownloadItem] = []
        self._queue: "asyncio.Queue[DownloadItem]" = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    # --- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    async def join(self) -> None:
        """Wait until every queued download has been processed."""
        await self._queue.join()

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change()

    # --- enqueueing -----------------------------------------------------------
    def _local_for(self, remote_path: str) -> Path:
        """Where ``remote_path`` is stored locally — mirrored, or flattened into one
        per-host folder. Deterministic per remote path, so re-grabbing a file resumes
        it instead of writing a second copy."""
        if not self.flat:
            parts = [p for p in remote_path.replace("\\", "/").split("/") if p]
            return self.root.joinpath(*parts)
        local = self._assigned.get(remote_path)
        if local is None:
            local = self._claim(self.root / flat_name(remote_path), remote_path)
            self._assigned[remote_path] = local
        return local

    def _claim(self, candidate: Path, remote_path: str) -> Path:
        """Reserve ``candidate`` for ``remote_path``, numbering on a clash.

        Only a clash with *another remote path* is numbered — an existing file on
        disk for the same remote path is the whole point of resume, so it is kept.
        Flattening makes clashes rare but possible ('a/b_c' and 'a_b/c' fold alike).
        """
        owner = self._claimed.get(candidate)
        if owner is None or owner == remote_path:
            self._claimed[candidate] = remote_path
            return candidate
        stem, dot, ext = candidate.name.rpartition(".")
        if not dot or len(ext) > 16:
            stem, dot, ext = candidate.name, "", ""
        for n in range(2, 10_000):
            numbered = candidate.with_name(f"{stem}{DEDUPE_SEP}{n}{dot}{ext}")
            if self._claimed.get(numbered) in (None, remote_path):
                self._claimed[numbered] = remote_path
                return numbered
        raise RuntimeError(f"cannot find a free local name for {remote_path}")

    async def add_file(self, remote_path: str, size: int = 0) -> DownloadItem:
        item = DownloadItem(remote_path, self._local_for(remote_path), size)
        self.items.append(item)
        await self._queue.put(item)
        self._notify()
        return item

    async def add_files(self, files: Iterable[tuple[str, int]]) -> list[DownloadItem]:
        return [await self.add_file(rp, size) for rp, size in files]

    async def add_dir(self, remote_path: str, *, recursive: bool) -> list[DownloadItem]:
        return await self.add_files(await self._collect(remote_path, recursive))

    async def _collect(self, path: str, recursive: bool) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        try:
            entries = await self.gateway.list(path, priority=Priority.DOWNLOAD)
        except Exception:
            return out  # unreadable dir: skip rather than abort the whole job
        for entry in entries:
            child = f"{path}/{entry.name}" if path else entry.name
            if entry.is_dir:
                if recursive:
                    out.extend(await self._collect(child, recursive))
            else:
                out.append((child, entry.size))
        return out

    # --- worker ---------------------------------------------------------------
    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self._download(item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - record, keep the queue moving
                item.status = "error"
                item.error = str(exc)
            finally:
                self._notify()
                self._queue.task_done()

    async def _download(self, item: DownloadItem) -> None:
        item.local_path.parent.mkdir(parents=True, exist_ok=True)
        existing = item.local_path.stat().st_size if item.local_path.exists() else 0

        if not item.size:
            try:
                item.size = (
                    await self.gateway.stat(item.remote_path, priority=Priority.DOWNLOAD)
                ).size
            except Exception:
                item.size = 0

        if existing:
            if self.exists_policy == "skip":
                item.downloaded, item.status = existing, "skipped"
                return
            if self.exists_policy == "overwrite":
                existing = 0
            elif self.exists_policy == "resume" and item.size and existing >= item.size:
                item.downloaded, item.status = existing, "skipped"
                return

        item.downloaded = existing
        item.status = "running"
        self._notify()

        mode = "ab" if existing else "wb"
        # Open the remote file once and read successive ranges — each range is its
        # own low-priority job (so browsing still preempts), but we don't reopen
        # per chunk. That keeps the wire/audit footprint like a normal client.
        remote = await self.gateway.open_file(item.remote_path, priority=Priority.DOWNLOAD)
        try:
            with open(item.local_path, mode) as local:
                offset = existing
                while True:
                    chunk = await self.gateway.read_file(
                        remote, offset, CHUNK, priority=Priority.DOWNLOAD
                    )
                    if not chunk:
                        break
                    local.write(chunk)
                    offset += len(chunk)
                    item.downloaded = offset
                    self._notify()
                    if item.size and offset >= item.size:
                        break
        finally:
            await self.gateway.close_file(remote, priority=Priority.DOWNLOAD)
        item.status = "done"
