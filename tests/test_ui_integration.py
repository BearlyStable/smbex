"""Full-stack end-to-end: the Textual app driving the real impacket backend over
a live SMB server. Exercises UI keys → Browser → Gateway (to_thread) → impacket →
SimpleSMBServer, which no single-layer test covers."""

from __future__ import annotations

import pytest

from smbex.auth import build_smb_auth
from smbex.backend.impacket_backend import ImpacketBackend
from smbex.gateway import Gateway
from smbex.ui.app import SmbexApp

pytestmark = pytest.mark.integration


async def test_browse_real_share_through_the_tui(smb_server):
    auth = build_smb_auth(f"smbex:smbex@{smb_server['host']}", port=smb_server["port"])
    app = SmbexApp(Gateway(ImpacketBackend.connect(auth)), label="smb")

    async with app.run_test() as pilot:
        browser = app.browser
        root_names = [e.name for e in browser.entries]
        assert smb_server["share"] in root_names

        # Select the test share among the real shares (IPC$, etc.) and enter it.
        browser.move_to(root_names.index(smb_server["share"]))
        await pilot.press("l")

        assert browser.path == smb_server["share"]
        names = [e.name for e in browser.entries]
        assert "hello.txt" in names
        assert "sub" in names
        await pilot.press("q")


async def test_flat_download_and_task_panel_over_real_smb(smb_server, tmp_path):
    """The whole download path end-to-end on a live server: flat naming, the panel
    appearing while the transfer runs and hiding itself after, and 'w' listing it."""
    auth = build_smb_auth(f"smbex:smbex@{smb_server['host']}", port=smb_server["port"])
    app = SmbexApp(
        Gateway(ImpacketBackend.connect(auth)), label="smb", download_root=tmp_path, flat=True
    )

    async with app.run_test() as pilot:
        browser = app.browser
        share = smb_server["share"]
        browser.move_to([e.name for e in browser.entries].index(share))
        await pilot.press("l")
        browser.move_to([e.name for e in browser.entries].index("sub"))

        await pilot.press("d")  # grab sub/ recursively
        for _ in range(400):
            await pilot.pause()
            if app.downloads.items and not app.downloads.pending:
                break
        assert not app.downloads.pending, "download did not finish"

        # Flat layout: the remote path is folded into the name, in one directory.
        assert (tmp_path / f"{share}_sub_inner.bin").read_bytes() == b"\x00\x01\x02inner"
        assert not (tmp_path / share).exists()

        await pilot.pause()
        panel = app.query_one("#downloads")
        assert panel.has_class("hidden")  # drained -> out of the way

        await pilot.press("w")  # ... and 'w' brings up the finished transfer
        assert not panel.has_class("hidden")
        assert "inner.bin" in panel.rendered_text
        await pilot.press("q")
