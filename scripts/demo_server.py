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

import gzip
import os
import struct
import tempfile
import time
import zlib
from pathlib import Path

from impacket.smbserver import SimpleSMBServer

# Japanese sentences that the ja->en model renders well; used to build multi-screen
# text documents so the file viewer ('l' on a downloaded .txt) has something to scroll
# and translate side by side.
_JP_LINES = [
    "今日はとても良い天気です。", "朝ごはんにパンと卵を食べました。", "電車で会社まで通勤します。",
    "会議は午前十時に始まりました。", "来週までに予算を確認する必要があります。", "昼休みに公園を散歩しました。",
    "同僚と一緒に昼ご飯を食べました。", "午後は資料を作成しました。", "家族に写真を送りました。",
    "週末は旅行に行く予定です。", "地図を見ながら道を探しました。", "駅の近くに新しい店ができました。",
    "夜は音楽を聞いてくつろぎました。", "新しい映画をとても楽しみました。", "友達に電話をかけました。",
    "天気が悪いので傘を持って行きます。", "夕食にカレーを作りました。", "週末に部屋を掃除しました。",
    "図書館で静かに勉強しました。", "試験に合格してとても嬉しいです。", "上司に報告書を提出しました。",
    "銀行で口座を開きました。", "病院で健康診断を受けました。", "空港で飛行機を待っています。",
    "山に登って景色を楽しみました。", "楽しい一日をありがとうございました。",
]


def _long_doc(title: str, sections: int, per_section: int) -> str:
    """A multi-screen Japanese document with numbered sections as scroll landmarks."""
    out = [title, ""]
    i = 0
    for s in range(1, sections + 1):
        out += [f"第{s}節", ""]
        for _ in range(per_section):
            out.append(_JP_LINES[i % len(_JP_LINES)])
            i += 1
        out.append("")
    return "\n".join(out) + "\n"


def _png(w: int, h: int) -> bytes:
    """A small, valid, noisy RGB PNG (real binary — shows a hex dump / PNG header)."""
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter: none
        for x in range(w):
            v = (x * 53 + y * 101 + x * y) & 0xFF
            raw += bytes(((v * 7) & 0xFF, (v * 13) & 0xFF, (v * 29) & 0xFF))

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def _bytes_for(name: str) -> bytes:
    """Content for a demo file, by extension: real binary for media, text otherwise."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in ("png", "jpg", "jpeg"):
        return _png(40, 40)
    if ext == "gz":
        return gzip.compress(("圧縮されたテキストです。\n" * 20).encode())
    if ext in ("bin", "mp3", "pdf", "dat"):
        return os.urandom(2048)  # incompressible -> a full hex-dump screen
    return (name + "\n").encode()

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
    """Japanese-named files/folders for the demo. Download a file ('d'), then view it
    ('l'): a .txt opens the scrollable content viewer ('t' for side-by-side English),
    an image/binary opens the xxd-style hex view.
    """
    # Folders with a mix of real binary (images / pdf / mp3) and text.
    tree: dict[str, list[str]] = {
        "写真": ["家族.jpg", "東京.jpg", "旅行.png"],        # Photos (real images -> hex)
        "仕事": ["契約書.pdf", "領収書.pdf", "会議メモ.txt"],  # Work: pdf binaries + a text note
        "音楽": ["テーマ.mp3", "ニュース.txt"],              # Music: mp3 binary + text
        "映画": [],
    }
    for folder, files in tree.items():
        (base / folder).mkdir(parents=True)
        for name in files:
            (base / folder / name).write_bytes(_bytes_for(name))

    # Multi-screen Japanese documents — real content to scroll and translate.
    (base / "議事録.txt").write_text(_long_doc("会議議事録", 8, 16))  # ~150 lines
    (base / "日記.txt").write_text(_long_doc("日記", 10, 10))        # ~130 lines
    (base / "物語.txt").write_text(_long_doc("長い物語", 16, 16))    # ~300 lines

    # Binary "materials" for the xxd hex view.
    mats = base / "素材"
    mats.mkdir()
    (mats / "ロゴ.png").write_bytes(_png(48, 48))
    (mats / "音声.bin").write_bytes(os.urandom(2048))
    (mats / "書庫.gz").write_bytes(_bytes_for("book.gz"))

    # Loose files at the top of 日本語/.
    for name in ("地図.png", "買い物リスト.txt", "天気.txt"):  # Map (image), Shopping list, Weather
        (base / name).write_bytes(_bytes_for(name))

    # Stagger mtimes so timestamps ('3d', '2w', ...) and time sorting ('o') show.
    ages_days = {
        "地図.png": 0.02, "天気.txt": 0.5, "買い物リスト.txt": 9, "写真": 21,
        "仕事": 2, "音楽": 120, "映画": 400, "議事録.txt": 1, "日記.txt": 3,
        "物語.txt": 30, "素材": 0.1,
    }
    now = time.time()
    for name, days in ages_days.items():
        when = now - days * 86400
        os.utime(base / name, (when, when))


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
