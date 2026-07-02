"""Background download manager.

Downloads run on an asyncio worker, one file at a time, streaming each file in
chunks through the gateway at DOWNLOAD priority. Because every chunk is a separate
low-priority gateway job, a queued browse request is served between chunks —
browsing stays responsive and downloads take only the leftover bandwidth.

The remote tree is mirrored locally under ``root`` (``root / share / dir / file``).
Existing-file policy (default ``resume``): continue a partial file from where it
stopped, and skip files already fully present; ``overwrite`` re-fetches; ``skip``
never touches an existing path.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from smbex.gateway import Gateway, Priority

CHUNK = 256 * 1024


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
        on_change: Callable[[], None] | None = None,
    ):
        self.gateway = gateway
        self.root = Path(root)
        self.exists_policy = exists_policy
        self.on_change = on_change
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
        parts = [p for p in remote_path.replace("\\", "/").split("/") if p]
        return self.root.joinpath(*parts)

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
