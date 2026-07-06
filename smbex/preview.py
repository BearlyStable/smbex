"""Bounded content preview for a *downloaded* file — text, or an xxd-style hex dump.

Only ever reads a bounded prefix of the local mirror file, so previewing a huge
download can't stall or blow up the UI. Text vs. binary is a cheap heuristic (NUL
byte / ratio of non-text bytes), matching what tools like git use.
"""

from __future__ import annotations

from pathlib import Path

MAX_TEXT_BYTES = 64 * 1024  # decode at most this much for a text preview
MAX_HEX_BYTES = 2 * 1024  # hex-dump at most this much (128 lines)

# Printable ASCII plus the usual whitespace/escape control chars.
_TEXT_BYTES = bytes(range(0x20, 0x7F)) + b"\n\r\t\f\b\x1b"


def looks_binary(sample: bytes) -> bool:
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    nontext = sample.translate(None, _TEXT_BYTES)
    return len(nontext) / len(sample) > 0.30


def hexdump(data: bytes, width: int = 16) -> str:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        hexs = " ".join(f"{b:02x}" for b in chunk).ljust(width * 3 - 1)
        ascii_ = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append(f"{i:08x}  {hexs}  {ascii_}")
    return "\n".join(lines)


def read_preview(
    path: Path, *, max_text: int = MAX_TEXT_BYTES, max_hex: int = MAX_HEX_BYTES
) -> tuple[str, str, bool]:
    """Return ``(kind, body, truncated)`` — ``kind`` is ``"text"`` or ``"binary"``.

    Reads at most ``max_text`` bytes; a binary file is hex-dumped to at most
    ``max_hex`` bytes. ``truncated`` says the file is larger than what's shown.
    """
    with open(path, "rb") as f:
        head = f.read(max_text)
    total = path.stat().st_size
    if looks_binary(head[:4096]):
        return "binary", hexdump(head[:max_hex]), total > max_hex
    return "text", head.decode("utf-8", errors="replace"), total > len(head)
