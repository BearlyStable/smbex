#!/usr/bin/env python3
"""Throwaway local SMB server for trying out smbex — no root required.

Serves a small sample tree over SMB2 on 127.0.0.1:4455 as share "DEMO". Any
username/password is accepted (the server does not validate). Ctrl-C to stop.

The tree includes a Japanese folder (日本語) with Japanese-named files and folders,
so you can see filename translation live:

    python scripts/demo_server.py
    # then, in another terminal:
    python -m smbex 'demo:demo@127.0.0.1' --port 4455
    # with translation (needs the ja model:  python -m smbex --install-lang ja):
    python -m smbex --translate ja 'demo:demo@127.0.0.1' --port 4455   # press 't' to toggle
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
    # A non-English name to eyeball translation (run with --translate de).
    (docs / "Quartalsbericht.txt").write_text("Bericht\n")
    (docs / "notes.md").write_text("# Notes\n")

    (root / "empty").mkdir()

    _populate_japanese(root / "日本語")  # browse with --translate ja


def _populate_japanese(base: Path) -> None:
    """A tree of Japanese-named files/folders that the ja->en model renders well.

    Comments show the expected English so you know what to look for with 't' on.
    """
    tree: dict[str, list[str]] = {
        "写真": ["家族.jpg", "東京.jpg", "旅行.png"],        # Photos: Family, Tokyo, Travel
        "仕事": ["契約書.pdf", "領収書.pdf", "会議メモ.txt"],  # Work: Contract, Receipt, Conference notes
        "音楽": ["ニュース.txt"],                             # Music: News
        "映画": [],                                          # Movie (empty)
    }
    for folder, files in tree.items():
        (base / folder).mkdir(parents=True)
        for name in files:
            (base / folder / name).write_text(f"{folder}/{name}\n")
    # A few loose files at the top of 日本語/ too.
    for name in ("地図.png", "買い物リスト.txt", "天気.txt"):  # Map, Shopping list, Weather
        (base / name).write_text(name + "\n")


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="smbex-demo-"))
    populate(root)

    server = SimpleSMBServer(listenAddress=HOST, listenPort=PORT)
    server.addShare(SHARE, str(root), "smbex demo share")
    server.setSMB2Support(True)

    print(f"Serving {root} as //{HOST}:{PORT}/{SHARE} (SMB2). Any user/pass works.")
    print("Connect:  python -m smbex 'demo:demo@127.0.0.1' --port 4455")
    print("Translate (see 日本語/):  python -m smbex --translate ja 'demo:demo@127.0.0.1' --port 4455")
    print("Ctrl-C to stop.")
    try:
        server.start()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
