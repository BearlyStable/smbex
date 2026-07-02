"""Async, priority-scheduled front-end to a blocking backend.

The gateway owns one backend connection and a single worker. Callers submit jobs
with a priority; the worker always runs the highest-priority queued job next, on a
thread (``asyncio.to_thread``) so the event loop — and thus browsing — never blocks.

Browse and preload jobs outrank downloads, so a queued directory listing is served
before more of a slow download. Downloads (Phase 4) additionally yield between
chunks, submitting each chunk as its own low-priority job, which lets browsing
preempt them mid-transfer — the "throttle downloads to keep browsing snappy" rule.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable

from smbex.backend.base import Backend, DirEntry


class Priority(IntEnum):
    BROWSE = 0
    PRELOAD = 5
    DOWNLOAD = 10


@dataclass(order=True)
class _Job:
    priority: int
    seq: int
    func: Callable[[], Any] = field(compare=False)
    future: "asyncio.Future" = field(compare=False)


class Gateway:
    def __init__(self, backend: Backend):
        self._backend = backend
        self._queue: "asyncio.PriorityQueue[_Job]" = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._worker: asyncio.Task | None = None

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                result = await asyncio.to_thread(job.func)
                if not job.future.done():
                    job.future.set_result(result)
            except Exception as exc:  # propagate to the awaiting caller
                if not job.future.done():
                    job.future.set_exception(exc)
            finally:
                self._queue.task_done()

    async def _submit(self, priority: int, func: Callable[[], Any]) -> Any:
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._queue.put(_Job(int(priority), next(self._seq), func, future))
        return await future

    # --- public API -----------------------------------------------------------
    async def roots(self, priority: int = Priority.BROWSE) -> list[DirEntry]:
        return await self._submit(priority, self._backend.roots)

    async def list(self, path: str, priority: int = Priority.BROWSE) -> list[DirEntry]:
        return await self._submit(priority, lambda: self._backend.list(path))

    async def stat(self, path: str, priority: int = Priority.BROWSE) -> DirEntry:
        return await self._submit(priority, lambda: self._backend.stat(path))

    async def read(self, path: str, priority: int = Priority.BROWSE) -> bytes:
        """Read a whole file's bytes (previews, small files)."""
        return await self._submit(
            priority, lambda: b"".join(self._backend.open_read(path))
        )

    async def read_range(
        self, path: str, offset: int, length: int, priority: int = Priority.DOWNLOAD
    ) -> bytes:
        """Read up to ``length`` bytes from ``offset`` as one low-priority job.

        Downloads call this per chunk so that between chunks a queued browse job
        (higher priority) is served first — the cooperative throttle that keeps
        browsing responsive during a transfer.
        """

        def _do() -> bytes:
            buf = bytearray()
            gen = self._backend.open_read(path, offset)
            try:
                for chunk in gen:
                    buf += chunk
                    if len(buf) >= length:
                        break
            finally:
                gen.close()  # run the backend generator's finally -> close handle
            return bytes(buf[:length])

        return await self._submit(priority, _do)

    async def open_file(self, path: str, priority: int = Priority.DOWNLOAD):
        """Open a remote file handle (one low-priority job)."""
        return await self._submit(priority, lambda: self._backend.open_file(path))

    async def read_file(
        self, handle, offset: int, length: int, priority: int = Priority.DOWNLOAD
    ) -> bytes:
        """Read a range from an already-open handle as one low-priority job, so a
        queued browse still runs between a download's chunks."""
        return await self._submit(priority, lambda: handle.read(offset, length))

    async def close_file(self, handle, priority: int = Priority.DOWNLOAD) -> None:
        await self._submit(priority, handle.close)

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
        await asyncio.to_thread(self._backend.close)

    async def __aenter__(self) -> "Gateway":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()
