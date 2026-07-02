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
