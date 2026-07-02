#!/usr/bin/env python3
"""Throwaway local SMB server for trying out smbex — no root required.

Serves a small sample tree over SMB2 on 127.0.0.1:4455 as share "DEMO". Any
username/password is accepted (the server does not validate). Ctrl-C to stop.

    python scripts/demo_server.py
    # then, in another terminal:
    python -m smbex 'demo:demo@127.0.0.1' --port 4455
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from impacket.smbserver import SimpleSMBServer

HOST, PORT, SHARE = "127.0.0.1", 4455, "DEMO"


def populate(root: Path) -> None:
    (root / "readme.txt").write_text("hello from smbex\n")

    reports = root / "reports" / "2025"
    reports.mkdir(parents=True)
    (reports / "q1.csv").write_text("region,amount\nEU,100\n")

    docs = root / "documents"
    docs.mkdir()
    # A non-English name to eyeball once translation (Phase 7) lands.
    (docs / "Quartalsbericht.txt").write_text("Bericht\n")
    (docs / "notes.md").write_text("# Notes\n")

    (root / "empty").mkdir()


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="smbex-demo-"))
    populate(root)

    server = SimpleSMBServer(listenAddress=HOST, listenPort=PORT)
    server.addShare(SHARE, str(root), "smbex demo share")
    server.setSMB2Support(True)

    print(f"Serving {root} as //{HOST}:{PORT}/{SHARE} (SMB2). Any user/pass works.")
    print("Connect:  python -m smbex 'demo:demo@127.0.0.1' --port 4455")
    print("Ctrl-C to stop.")
    try:
        server.start()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
