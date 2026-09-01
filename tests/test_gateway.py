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


async def test_browse_preempts_between_download_chunks():
    """A browse request issued mid-download is served before the next chunk read.

    This is the throttle: downloads read one chunk per low-priority job, so a
    higher-priority browse queued while a chunk is in flight runs first."""
    from smbex.download import CHUNK

    backend = FakeBackend({"big": b"x" * (2 * CHUNK), "marker": {}})
    gate = threading.Event()
    backend.read_gates["big"] = gate  # hold the first download chunk

    async with Gateway(backend) as gw:
        chunk1 = asyncio.ensure_future(
            gw.read_range("big", 0, CHUNK, priority=Priority.DOWNLOAD)
        )
        for _ in range(200):  # wait until the worker is blocked inside the first read
            if "read-start:big@0" in backend.events:
                break
            await asyncio.sleep(0.005)

        # Queue a browse and the next download chunk while the first read is stuck.
        browse = asyncio.ensure_future(gw.list("marker", priority=Priority.BROWSE))
        chunk2 = asyncio.ensure_future(
            gw.read_range("big", CHUNK, CHUNK, priority=Priority.DOWNLOAD)
        )
        await asyncio.sleep(0.03)
        gate.set()
        await asyncio.gather(chunk1, chunk2, browse)

    assert backend.events.index("list:marker") < backend.events.index(
        f"read:big@{CHUNK}"
    )


async def test_cancelled_request_is_dropped_before_it_hits_the_backend():
    """A queued job whose caller gave up must never reach the wire.

    Browsing away cancels the awaiting task (and with it the future), so the job is
    dead weight — running it would delay the listing the user is actually waiting on.
    """
    backend = FakeBackend({"a": {}, "b": {}, "c": {}})
    backend.gates["a"] = threading.Event()  # hold the worker on the first job
    async with Gateway(backend) as gw:
        first = asyncio.create_task(gw.list("a"))
        await asyncio.sleep(0)  # let the worker pick "a" up and block on it
        abandoned = asyncio.create_task(gw.list("b"))
        await asyncio.sleep(0)
        abandoned.cancel()  # the caller moved on

        backend.gates["a"].set()
        assert [e.name for e in await first] == []
        assert [e.name for e in await gw.list("c")] == []  # a later job still runs
        assert "b" not in backend.list_calls  # ... and the dead one never listed
