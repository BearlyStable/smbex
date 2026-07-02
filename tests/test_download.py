"""DownloadManager: local mirror layout, current-folder vs recursive enumeration,
and the resume / skip / overwrite policies for files already present locally."""

from __future__ import annotations

from smbex.backend.fake_backend import FakeBackend
from smbex.download import DownloadManager
from smbex.gateway import Gateway

TREE = {
    "share": {
        "readme.txt": b"hello world",  # 11 bytes
        "docs": {"a.txt": b"aaaa", "b.txt": b"bbbbbb"},
        "pics": {"deep": {"c.bin": b"\x00\x01\x02\x03"}},
    },
}


async def _drain(mgr: DownloadManager) -> None:
    mgr.start()
    await mgr.join()
    await mgr.stop()


async def test_single_file_mirrors_remote_path(tmp_path):
    async with Gateway(FakeBackend(TREE)) as gw:
        mgr = DownloadManager(gw, tmp_path)
        await mgr.add_file("share/readme.txt", 11)
        await _drain(mgr)
    assert (tmp_path / "share" / "readme.txt").read_bytes() == b"hello world"


async def test_current_folder_files_are_not_recursive(tmp_path):
    async with Gateway(FakeBackend(TREE)) as gw:
        mgr = DownloadManager(gw, tmp_path)
        await mgr.add_files([("share/readme.txt", 11)])  # what "grab all here" enqueues
        await _drain(mgr)
    assert (tmp_path / "share" / "readme.txt").exists()
    assert not (tmp_path / "share" / "docs").exists()


async def test_recursive_download_mirrors_whole_tree(tmp_path):
    async with Gateway(FakeBackend(TREE)) as gw:
        mgr = DownloadManager(gw, tmp_path)
        await mgr.add_dir("share", recursive=True)
        await _drain(mgr)
    base = tmp_path / "share"
    assert (base / "readme.txt").read_bytes() == b"hello world"
    assert (base / "docs" / "a.txt").read_bytes() == b"aaaa"
    assert (base / "docs" / "b.txt").read_bytes() == b"bbbbbb"
    assert (base / "pics" / "deep" / "c.bin").read_bytes() == b"\x00\x01\x02\x03"


async def test_resume_completes_a_partial_file(tmp_path):
    dest = tmp_path / "share" / "readme.txt"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"hello")  # correct 5-byte prefix of 11
    async with Gateway(FakeBackend(TREE)) as gw:
        mgr = DownloadManager(gw, tmp_path, exists_policy="resume")
        item = await mgr.add_file("share/readme.txt", 11)
        await _drain(mgr)
    assert dest.read_bytes() == b"hello world"
    assert item.status == "done"


async def test_skip_already_complete_file(tmp_path):
    dest = tmp_path / "share" / "readme.txt"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"hello world")
    async with Gateway(FakeBackend(TREE)) as gw:
        mgr = DownloadManager(gw, tmp_path, exists_policy="resume")
        item = await mgr.add_file("share/readme.txt", 11)
        await _drain(mgr)
    assert item.status == "skipped"


async def test_overwrite_replaces_existing(tmp_path):
    dest = tmp_path / "share" / "readme.txt"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"XXXXXXXXXXX")  # wrong content, same length
    async with Gateway(FakeBackend(TREE)) as gw:
        mgr = DownloadManager(gw, tmp_path, exists_policy="overwrite")
        await mgr.add_file("share/readme.txt", 11)
        await _drain(mgr)
    assert dest.read_bytes() == b"hello world"


async def test_multi_chunk_file_is_opened_once(tmp_path):
    """A file spanning several chunks is opened/closed once, not per chunk
    (keeps the SMB wire/audit footprint like a normal client)."""
    from smbex.download import CHUNK

    data = b"y" * (3 * CHUNK + 100)  # 4 read_file calls
    backend = FakeBackend({"share": {"big.bin": data}})
    async with Gateway(backend) as gw:
        mgr = DownloadManager(gw, tmp_path)
        await mgr.add_file("share/big.bin", len(data))
        await _drain(mgr)

    assert (tmp_path / "share" / "big.bin").read_bytes() == data
    assert backend.events.count("open:share/big.bin") == 1
    assert backend.events.count("close:share/big.bin") == 1


async def test_browse_preempts_an_active_download(tmp_path):
    """While a large file is downloading, a browse listing is served before the
    download's next chunk — you can keep navigating during a big transfer, even
    though the remote file stays open across chunks."""
    import asyncio
    import threading

    from smbex.download import CHUNK
    from smbex.gateway import Priority

    backend = FakeBackend({"big": b"x" * (2 * CHUNK), "marker": {}})
    gate = threading.Event()
    backend.read_gates["big"] = gate  # hold the download's first chunk read

    async with Gateway(backend) as gw:
        mgr = DownloadManager(gw, tmp_path)
        mgr.start()
        await mgr.add_file("big", 2 * CHUNK)

        for _ in range(200):  # wait until the first chunk read is in flight
            if "read-start:big@0" in backend.events:
                break
            await asyncio.sleep(0.005)

        browse = asyncio.ensure_future(gw.list("marker", priority=Priority.BROWSE))
        await asyncio.sleep(0.03)
        gate.set()  # release the download
        await browse
        await mgr.join()
        await mgr.stop()

    # the browse listing ran before the download's SECOND chunk
    assert backend.events.index("list:marker") < backend.events.index(f"read:big@{CHUNK}")
