"""Gateway: results, error propagation, and browse-before-download priority."""

from __future__ import annotations

import asyncio
import threading

import pytest

from smbex.backend.fake_backend import FakeBackend
from smbex.gateway import Gateway, Priority


async def test_list_returns_entries():
    backend = FakeBackend({"share": {"one": b"1", "sub": {}}})
    async with Gateway(backend) as gw:
        entries = await gw.list("share")
    assert sorted(e.name for e in entries) == ["one", "sub"]


async def test_read_returns_bytes():
    backend = FakeBackend({"share": {"f": b"hello world"}})
    async with Gateway(backend) as gw:
        assert await gw.read("share/f") == b"hello world"


async def test_errors_propagate_to_caller():
    backend = FakeBackend({"share": {}})
    async with Gateway(backend) as gw:
        with pytest.raises(FileNotFoundError):
            await gw.list("does/not/exist")


async def test_browse_preempts_download():
    """While a download holds the worker, queued jobs run browse < preload < download."""
    backend = FakeBackend({"block": {}, "b": {}, "p": {}, "d2": {}})
    gate = threading.Event()
    backend.gates["block"] = gate  # first job blocks until we release it

    async with Gateway(backend) as gw:
        blocked = asyncio.ensure_future(gw.list("block", priority=Priority.DOWNLOAD))
        while not backend.list_calls:  # wait until the worker actually started it
            await asyncio.sleep(0.005)

        # Enqueue mixed priorities out of order while the worker is busy.
        futs = [
            asyncio.ensure_future(gw.list("d2", priority=Priority.DOWNLOAD)),
            asyncio.ensure_future(gw.list("p", priority=Priority.PRELOAD)),
            asyncio.ensure_future(gw.list("b", priority=Priority.BROWSE)),
        ]
        await asyncio.sleep(0.05)  # let them settle into the priority queue
        gate.set()  # release the blocked download
        await asyncio.gather(blocked, *futs)

    assert backend.exec_order == ["block", "b", "p", "d2"]
