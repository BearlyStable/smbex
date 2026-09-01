"""End-to-end: the FTP backend against a real in-process pyftpdlib server."""

from __future__ import annotations

import pytest

from smbex.auth import build_ftp_auth
from smbex.backend.ftp_backend import FtpBackend

pytestmark = pytest.mark.integration


def _auth(server):
    return build_ftp_auth(f"ftp://tester:secret@{server['host']}:{server['port']}")


def test_ftp_backend_lists_reads_and_stats(ftp_server):
    backend = FtpBackend.connect(_auth(ftp_server))
    try:
        names = sorted(e.name for e in backend.list(""))  # "" -> server "/"
        assert "readme.txt" in names and "logs" in names and "big.bin" in names

        assert b"".join(backend.open_read("readme.txt")) == b"hello ftp"
        assert b"".join(backend.open_read("readme.txt", offset=6)) == b"ftp"  # REST

        readme = next(e for e in backend.list("") if e.name == "readme.txt")
        assert not readme.is_dir and readme.size == 9 and readme.mtime > 0  # MLSD facts
        assert backend.stat("readme.txt").size == 9

        assert "app.log" in [e.name for e in backend.list("logs")]
    finally:
        backend.close()


def test_binary_read_not_corrupted_after_a_listing(ftp_server):
    # Regression: FTP defaults to ASCII and MLSD leaves the session in TYPE A, so a
    # RETR without an explicit TYPE I translates \n -> \r\n. Listing first is what
    # triggers it; app.log carries newlines that would be corrupted.
    backend = FtpBackend.connect(_auth(ftp_server))
    try:
        backend.list("")  # MLSD -> TYPE A
        assert b"".join(backend.open_read("logs/app.log")) == b"line1\nline2\n"
    finally:
        backend.close()


def test_ftp_partial_read_then_control_channel_survives(ftp_server):
    backend = FtpBackend.connect(_auth(ftp_server))
    try:
        gen = backend.open_read("big.bin")
        first = next(gen)  # one recv, then abandon the transfer
        assert first and len(first) <= 65536
        gen.close()  # aborts mid-stream
        # The control channel must still work after the abort:
        assert "big.bin" in [e.name for e in backend.list("")]
    finally:
        backend.close()


async def test_recursive_download_over_real_ftp(ftp_server, tmp_path):
    from smbex.download import DownloadManager
    from smbex.gateway import Gateway

    async with Gateway(FtpBackend.connect(_auth(ftp_server))) as gw:  # exercises open_file
        mgr = DownloadManager(gw, tmp_path)
        mgr.start()
        await mgr.add_dir("", recursive=True)  # from the server root
        await mgr.join()
        await mgr.stop()

    assert (tmp_path / "readme.txt").read_bytes() == b"hello ftp"
    assert (tmp_path / "logs" / "app.log").read_bytes() == b"line1\nline2\n"
    assert (tmp_path / "big.bin").read_bytes() == b"x" * 200_000  # multi-chunk transfer


async def test_ftp_reconnect_after_drop(ftp_server):
    from smbex.gateway import Gateway

    import socket

    backend = FtpBackend.connect(_auth(ftp_server))
    backend.list("")  # establish
    backend._ftp.sock.shutdown(socket.SHUT_RDWR)  # realistic drop: tear down the TCP link

    states: list[str] = []
    async with Gateway(backend, on_status=states.append) as gw:  # default: manual
        with pytest.raises(Exception):
            await gw.list("")
        assert gw.connection_lost is True  # classified as a connection error
        assert states == ["disconnected"]

        assert await gw.reconnect() is True  # the 'r' path rebuilds the session
        assert "readme.txt" in [e.name for e in await gw.list("")]
    assert states == ["disconnected", "reconnecting", "connected"]


async def test_browse_ftp_through_the_tui(ftp_server):
    from smbex.gateway import Gateway
    from smbex.ui.app import SmbexApp

    backend = FtpBackend.connect(_auth(ftp_server))
    app = SmbexApp(Gateway(backend), start_path=backend.start_rel, label="ftp")
    async with app.run_test() as pilot:
        names = [e.name for e in app.browser.entries]
        assert "readme.txt" in names and "logs" in names

        app.browser.move_to(names.index("logs"))
        await pilot.press("l")
        assert app.browser.path.endswith("logs")
        assert "app.log" in [e.name for e in app.browser.entries]
        await pilot.press("q")


async def test_running_transfer_is_not_interruptible_over_real_ftp(ftp_server, tmp_path):
    """A RETR can't be abandoned cheaply, so the manager refuses to stop one."""
    from smbex.download import DownloadManager
    from smbex.gateway import Gateway

    async with Gateway(FtpBackend.connect(_auth(ftp_server))) as gw:
        assert gw.interruptible is False  # declared by FtpBackend
        mgr = DownloadManager(gw, tmp_path)
        assert mgr.can_interrupt is False
        item = await mgr.add_file("big.bin", 200_000)
        item.status = "running"  # pretend it is the one in flight
        assert mgr.cancel(item) == "uninterruptible"
        assert item.control == ""
