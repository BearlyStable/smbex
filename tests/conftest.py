"""Shared fixtures. The SMB fixture stands up a real local impacket server so the
impacket backend is exercised end-to-end without any external infrastructure."""

from __future__ import annotations

import logging
import socket
import threading
import time

import pytest

# impacket's server logs verbosely; keep test output readable.
logging.getLogger("impacket").setLevel(logging.CRITICAL)

SMB_HOST = "127.0.0.1"
SMB_PORT = 4455
SMB_SHARE = "TESTSHARE"


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def smb_server(tmp_path_factory):
    """A local SimpleSMBServer sharing a small populated tree. Skips if it can't run.

    Session-scoped: the server is a daemon thread with no portable clean-stop, so
    binding it once and sharing it avoids a port clash between test modules.
    """
    try:
        from impacket.smbserver import SimpleSMBServer
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"impacket smbserver unavailable: {exc}")

    share_dir = tmp_path_factory.mktemp("smbshare")
    (share_dir / "hello.txt").write_bytes(b"hello smb")
    sub = share_dir / "sub"
    sub.mkdir()
    (sub / "inner.bin").write_bytes(b"\x00\x01\x02inner")

    try:
        server = SimpleSMBServer(listenAddress=SMB_HOST, listenPort=SMB_PORT)
    except OSError as exc:
        pytest.skip(f"cannot bind SMB test server on {SMB_HOST}:{SMB_PORT}: {exc}")

    server.addShare(SMB_SHARE, str(share_dir), "smbex integration test share")
    server.setSMB2Support(True)

    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()

    deadline = time.time() + 5
    while time.time() < deadline and not _port_open(SMB_HOST, SMB_PORT):
        time.sleep(0.05)
    if not _port_open(SMB_HOST, SMB_PORT):
        pytest.skip("SMB test server did not come up")

    yield {"host": SMB_HOST, "port": SMB_PORT, "share": SMB_SHARE, "dir": share_dir}
    # Daemon thread; impacket's SimpleSMBServer has no portable clean-stop API.


# --- UI / browser fixtures (offline, fake backend) ---------------------------

# A small tree used by the browser and UI tests. Roots are two dirs; "share"
# holds two subdirs and a file. Sorted view: dirs first, then case-insensitive.
UI_TREE = {
    "other": {},
    "share": {
        "docs": {"a.txt": b"aaa", "b.txt": b"bb"},
        "pics": {"1.png": b"x"},
        "readme.txt": b"hello",
    },
}


@pytest.fixture
def make_app():
    """Factory building a SmbexApp over a fresh FakeBackend for Pilot tests."""
    from smbex.backend.fake_backend import FakeBackend
    from smbex.gateway import Gateway
    from smbex.ui.app import SmbexApp

    def _make(tree: dict | None = None, **kwargs):
        backend = FakeBackend(tree if tree is not None else dict(UI_TREE))
        return SmbexApp(Gateway(backend), **kwargs)

    return _make
