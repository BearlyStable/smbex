"""Interrupting transfers: cancel one, or push a running one down the queue.

Both work between chunks and lean on the same resume the existing-file policy uses:
whatever has already been written stays on disk, so a yielded transfer picks up
where it stopped and a cancelled one resumes if it is grabbed again.
"""

from __future__ import annotations

from smbex.backend.fake_backend import FakeBackend
from smbex.download import CHUNK, DownloadManager
from smbex.gateway import Gateway

BIG = b"B" * (CHUNK * 3)  # three chunks: interruptible at two boundaries
TREE = {"share": {"big.bin": BIG, "small.txt": b"hello", "other.txt": b"bye"}}


def _resumed_offsets(backend, path: str) -> list[int]:
    """Offsets the file was (re)opened and read from, past the start."""
    prefix = f"read-start:{path}@"
    return [int(e[len(prefix):]) for e in backend.events if e.startswith(prefix)]


async def test_cancel_a_queued_transfer_never_fetches_it(tmp_path):
    backend = FakeBackend(TREE)
    async with Gateway(backend) as gw:
        mgr = DownloadManager(gw, tmp_path)
        keep = await mgr.add_file("share/small.txt", 5)
        drop = await mgr.add_file("share/other.txt", 3)
        assert mgr.cancel(drop) == "cancelled"

        mgr.start()
        await mgr.join()
        await mgr.stop()

    assert drop.status == "cancelled" and keep.status == "done"
    assert not (tmp_path / "share" / "other.txt").exists()
    assert "open:share/other.txt" not in backend.events  # never touched the wire


async def test_cancel_a_running_transfer_stops_it_and_keeps_the_partial(tmp_path, chunk_gate):
    backend = FakeBackend(TREE)
    gate = chunk_gate(allow=1)  # one chunk through, then hold
    backend.read_gates["share/big.bin"] = gate
    async with Gateway(backend) as gw:
        mgr = DownloadManager(gw, tmp_path)
        big = await mgr.add_file("share/big.bin", len(BIG))
        small = await mgr.add_file("share/small.txt", 5)
        mgr.start()

        await gate.wait_until_held()  # mid-file, with a chunk already written
        assert big.status == "running" and big.downloaded == CHUNK
        assert mgr.cancel(big) == "stopping"
        gate.release()
        await mgr.join()
        await mgr.stop()

    assert big.status == "cancelled"
    partial = (tmp_path / "share" / "big.bin").stat().st_size
    assert 0 < partial < len(BIG)  # stopped at a chunk boundary, partial kept
    assert small.status == "done"  # ... and the queue kept moving
    assert (tmp_path / "share" / "small.txt").read_bytes() == b"hello"


async def test_deprioritized_transfer_lets_the_small_one_through_then_resumes(tmp_path, chunk_gate):
    """The reported case: a big file is hogging the link, the small one is wanted now."""
    backend = FakeBackend(TREE)
    gate = chunk_gate(allow=1)
    backend.read_gates["share/big.bin"] = gate
    async with Gateway(backend) as gw:
        mgr = DownloadManager(gw, tmp_path)
        big = await mgr.add_file("share/big.bin", len(BIG))
        small = await mgr.add_file("share/small.txt", 5)
        mgr.start()

        await gate.wait_until_held()
        assert mgr.reorder(big, 1) is True  # push it below the small one
        assert big.control == "yield"
        gate.release()

        await mgr.join()
        await mgr.stop()

    # The small file went first, and the big one finished from where it left off.
    assert [i.remote_path for i in mgr.items] == ["share/small.txt", "share/big.bin"]
    assert small.status == "done" and big.status == "done"
    assert (tmp_path / "share" / "big.bin").read_bytes() == BIG
    # Resumed rather than restarted: it re-opened part-way through the file.
    assert any(offset > 0 for offset in _resumed_offsets(backend, "share/big.bin"))


async def test_pulling_a_queued_transfer_up_preempts_the_running_one(tmp_path, chunk_gate):
    """Same thing from the other side: 'run this next' has to stop what's running."""
    backend = FakeBackend(TREE)
    gate = chunk_gate(allow=1)
    backend.read_gates["share/big.bin"] = gate
    async with Gateway(backend) as gw:
        mgr = DownloadManager(gw, tmp_path)
        big = await mgr.add_file("share/big.bin", len(BIG))
        small = await mgr.add_file("share/small.txt", 5)
        mgr.start()

        await gate.wait_until_held()
        assert mgr.reorder(small, -1) is True  # raise the small one above it
        assert big.control == "yield"
        gate.release()

        await mgr.join()
        await mgr.stop()

    assert [i.remote_path for i in mgr.items] == ["share/small.txt", "share/big.bin"]
    assert big.status == "done" and small.status == "done"
    assert (tmp_path / "share" / "big.bin").read_bytes() == BIG


async def test_a_lone_transfer_has_nothing_to_yield_to(tmp_path):
    backend = FakeBackend(TREE)
    async with Gateway(backend) as gw:
        mgr = DownloadManager(gw, tmp_path)
        only = await mgr.add_file("share/small.txt", 5)
        assert mgr.reorder(only, 1) is False  # nothing behind it
        assert mgr.reorder(only, -1) is False  # nothing in front either
        assert only.control == ""
        await _finish(mgr)
    assert only.status == "done"


async def test_cancel_clears_a_finished_entry(tmp_path):
    async with Gateway(FakeBackend(TREE)) as gw:
        mgr = DownloadManager(gw, tmp_path)
        item = await mgr.add_file("share/small.txt", 5)
        await _finish(mgr)
        assert item.status == "done"
        assert mgr.cancel(item) == "cleared"
        assert mgr.items == []  # the entry is gone from the list, the file stays
    assert (tmp_path / "share" / "small.txt").exists()


async def test_a_cancelled_transfer_resumes_when_grabbed_again(tmp_path, chunk_gate):
    backend = FakeBackend(TREE)
    gate = chunk_gate(allow=1)
    backend.read_gates["share/big.bin"] = gate
    async with Gateway(backend) as gw:
        mgr = DownloadManager(gw, tmp_path)
        first = await mgr.add_file("share/big.bin", len(BIG))
        mgr.start()
        await gate.wait_until_held()
        mgr.cancel(first)
        gate.release()
        await mgr.join()
        partial = first.downloaded
        assert 0 < partial < len(BIG)

        again = await mgr.add_file("share/big.bin", len(BIG))  # 'd' on it once more
        await mgr.join()
        await mgr.stop()

    assert again.status == "done"
    assert (tmp_path / "share" / "big.bin").read_bytes() == BIG
    assert partial in _resumed_offsets(backend, "share/big.bin")  # resumed, not restarted


async def _finish(mgr: DownloadManager) -> None:
    mgr.start()
    await mgr.join()
    await mgr.stop()
