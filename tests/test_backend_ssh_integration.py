"""End-to-end: the SSH backend against a real in-process paramiko SFTP server."""

from __future__ import annotations

import pytest

from smbex.auth import build_ssh_auth
from smbex.backend.ssh_backend import SshBackend

pytestmark = pytest.mark.integration


def test_ssh_backend_lists_and_reads(sftp_server):
    auth = build_ssh_auth(
        f"ssh://tester:secret@{sftp_server['host']}:{sftp_server['port']}"
    )
    backend = SshBackend.connect(auth)
    try:
        names = sorted(e.name for e in backend.list(""))  # "" -> server "/"
        assert "readme.txt" in names
        assert "logs" in names

        assert b"".join(backend.open_read("readme.txt")) == b"hello ssh"

        # seekable/resumable read
        assert b"".join(backend.open_read("readme.txt", offset=6)) == b"ssh"

        logs = [e.name for e in backend.list("logs")]
        assert "app.log" in logs
    finally:
        backend.close()


async def test_browse_ssh_through_the_tui(sftp_server):
    from smbex.gateway import Gateway
    from smbex.ui.app import SmbexApp

    auth = build_ssh_auth(
        f"ssh://tester:secret@{sftp_server['host']}:{sftp_server['port']}"
    )
    backend = SshBackend.connect(auth)
    app = SmbexApp(Gateway(backend), start_path=backend.start_rel, label="ssh")

    async with app.run_test() as pilot:
        names = [e.name for e in app.browser.entries]
        assert "readme.txt" in names and "logs" in names

        app.browser.move_to(names.index("logs"))
        await pilot.press("l")
        assert app.browser.path.endswith("logs")
        assert "app.log" in [e.name for e in app.browser.entries]
        await pilot.press("q")


async def test_ssh_reconnect_after_drop(sftp_server):
    """A dropped SSH transport is classified as a connection error and the manual
    reconnect (the 'r' path) rebuilds the session against the same server."""
    from smbex.gateway import Gateway

    auth = build_ssh_auth(
        f"ssh://tester:secret@{sftp_server['host']}:{sftp_server['port']}"
    )
    backend = SshBackend.connect(auth)
    backend.list("")  # establish
    raw = backend._client.get_transport().sock  # realistic drop: kill the socket
    raw.close()

    states: list[str] = []
    async with Gateway(backend, on_status=states.append) as gw:
        with pytest.raises(Exception):
            await gw.list("")  # default is manual: report + propagate, no auto-heal
        assert gw.connection_lost is True  # i.e. classified as a connection error
        assert states == ["disconnected"]

        assert await gw.reconnect() is True  # rebuild the SSH/SFTP session
        names = sorted(e.name for e in await gw.list(""))
        assert "readme.txt" in names
    assert states == ["disconnected", "reconnecting", "connected"]


async def test_recursive_download_over_real_ssh(sftp_server, tmp_path):
    from smbex.download import DownloadManager
    from smbex.gateway import Gateway

    auth = build_ssh_auth(
        f"ssh://tester:secret@{sftp_server['host']}:{sftp_server['port']}"
    )
    async with Gateway(SshBackend.connect(auth)) as gw:  # exercises SSH open_file
        mgr = DownloadManager(gw, tmp_path)
        mgr.start()
        await mgr.add_dir("", recursive=True)  # from the server root
        await mgr.join()
        await mgr.stop()

    assert (tmp_path / "readme.txt").read_bytes() == b"hello ssh"
    assert (tmp_path / "logs" / "app.log").read_bytes() == b"line1\nline2\n"
