"""End-to-end: the impacket backend against a real local SMB server.

Marked ``integration`` — it binds a local port and speaks real SMB2. The fixture
skips cleanly if the server can't start.
"""

from __future__ import annotations

import pytest

from smbex.auth import build_smb_auth
from smbex.backend.impacket_backend import ImpacketBackend

pytestmark = pytest.mark.integration


def _connect(server, **opts) -> ImpacketBackend:
    auth = build_smb_auth(
        f"smbex:smbex@{server['host']}", port=server["port"], **opts
    )
    return ImpacketBackend.connect(auth)


def test_list_shares(smb_server):
    backend = _connect(smb_server)
    try:
        names = [e.name for e in backend.roots()]
        assert smb_server["share"] in names
    finally:
        backend.close()


def test_list_dir_and_read_file(smb_server):
    backend = _connect(smb_server)
    share = smb_server["share"]
    try:
        top = {e.name: e for e in backend.list(f"/{share}")}
        assert "hello.txt" in top
        assert "sub" in top and top["sub"].is_dir
        assert top["hello.txt"].size == len(b"hello smb")

        data = b"".join(backend.open_read(f"/{share}/hello.txt"))
        assert data == b"hello smb"

        inner = {e.name for e in backend.list(f"/{share}/sub")}
        assert "inner.bin" in inner
    finally:
        backend.close()


async def test_recursive_download_over_real_smb(smb_server, tmp_path):
    from smbex.download import DownloadManager
    from smbex.gateway import Gateway

    share = smb_server["share"]
    async with Gateway(_connect(smb_server)) as gw:  # gateway closes the backend
        mgr = DownloadManager(gw, tmp_path)
        mgr.start()
        await mgr.add_dir(share, recursive=True)
        await mgr.join()
        await mgr.stop()

    assert (tmp_path / share / "hello.txt").read_bytes() == b"hello smb"
    assert (tmp_path / share / "sub" / "inner.bin").read_bytes() == b"\x00\x01\x02inner"
