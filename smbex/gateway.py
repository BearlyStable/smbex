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
    def __init__(
        self,
        backend: Backend,
        *,
        on_status: Callable[[str], None] | None = None,
        reconnect_attempts: int = 3,
        reconnect_delay: float = 0.5,
    ):
        self._backend = backend
        self._queue: "asyncio.PriorityQueue[_Job]" = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._worker: asyncio.Task | None = None
        #: Called with "reconnecting" / "connected" / "disconnected" on link changes.
        self.on_status = on_status
        self._reconnect_attempts = reconnect_attempts
        self._reconnect_delay = reconnect_delay

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                result = await self._execute(job.func)
                if not job.future.done():
                    job.future.set_result(result)
            except Exception as exc:  # propagate to the awaiting caller
                if not job.future.done():
                    job.future.set_exception(exc)
            finally:
                self._queue.task_done()

    async def _execute(self, func: Callable[[], Any]) -> Any:
        """Run a job; on a lost connection, reconnect once and retry it.

        Only connection-class errors (per the backend's ``is_connection_error``)
        trigger recovery — normal failures like "not found" propagate untouched.
        Handle-based jobs (an in-flight download's ranged read) can't be retried on
        the fresh connection, so they surface an error; the transfer resumes if
        re-queued. Browsing, being stateless, recovers transparently.
        """
        try:
            return await asyncio.to_thread(func)
        except Exception as exc:
            if not self._is_connection_error(exc):
                raise
            await self._reconnect()  # raises if it gives up -> caller sees the error
            return await asyncio.to_thread(func)  # one retry on the new connection

    def _is_connection_error(self, exc: BaseException) -> bool:
        checker = getattr(self._backend, "is_connection_error", None)
        try:
            return bool(checker and checker(exc))
        except Exception:
            return False

    async def _reconnect(self) -> None:
        reconnect = getattr(self._backend, "reconnect", None)
        if reconnect is None:
            raise RuntimeError("backend cannot reconnect")
        self._set_status("reconnecting")
        last: BaseException | None = None
        for _ in range(self._reconnect_attempts):
            try:
                await asyncio.to_thread(reconnect)
                self._set_status("connected")
                return
            except Exception as exc:  # keep trying until attempts run out
                last = exc
                await asyncio.sleep(self._reconnect_delay)
        self._set_status("disconnected")
        raise last if last is not None else RuntimeError("reconnect failed")

    def _set_status(self, state: str) -> None:
        if self.on_status is not None:
            try:
                self.on_status(state)
            except Exception:
                pass  # a UI hiccup must never break the worker

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
