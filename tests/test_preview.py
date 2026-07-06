"""Downloaded-file preview: text vs. hex detection, bounded reads, and the preview
pane rendering the local content only when a file has actually been downloaded."""

from __future__ import annotations

from pathlib import Path

from smbex.preview import hexdump, looks_binary, read_preview


def test_looks_binary_heuristic():
    assert looks_binary(b"\x00\x01\x02") is True  # NUL -> binary
    assert looks_binary(b"hello\nworld\n") is False
    assert looks_binary(b"") is False
    assert looks_binary(bytes(range(256))) is True  # mostly non-text


def test_read_preview_text(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_bytes(b"line1\nline2\n")
    kind, body, truncated = read_preview(p)
    assert kind == "text" and body == "line1\nline2\n" and truncated is False


def test_read_preview_binary_is_hex(tmp_path):
    p = tmp_path / "blob.bin"
    p.write_bytes(bytes(range(32)))
    kind, body, truncated = read_preview(p)
    assert kind == "binary"
    assert body.startswith("00000000  ")  # xxd-style offset
    assert "00 01 02 03" in body


def test_read_preview_is_bounded(tmp_path):
    p = tmp_path / "big.txt"
    p.write_bytes(b"A" * (300 * 1024))  # 300 KB
    kind, body, truncated = read_preview(p, max_text=64 * 1024)
    assert kind == "text" and truncated is True
    assert len(body) == 64 * 1024  # only the bounded prefix was read


def test_hexdump_format():
    dump = hexdump(b"AB\x00", width=16)
    assert dump.startswith("00000000  41 42 00")
    assert dump.endswith("AB.")  # ascii gutter, NUL -> '.'


# --- UI integration --------------------------------------------------------
from smbex.ui.columns import Column


def _mirror(app, remote: str, data: bytes) -> None:
    """Write a file into the local download mirror as if it had been downloaded."""
    local = app._downloads._local_for(remote)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)


async def test_downloaded_text_file_shows_content(make_app, tmp_path):
    tree = {"share": {"notes.txt": b"hello\nworld\n"}}
    app = make_app(tree, download_root=tmp_path)
    async with app.run_test() as pilot:
        _mirror(app, "share/notes.txt", b"hello\nworld\n")
        await pilot.press("l")  # into share; notes.txt selected
        preview = app.query_one("#preview", Column)
        assert "hello\nworld" in preview.rendered_text  # local content shown
        await pilot.press("q")


async def test_undownloaded_file_shows_metadata_only(make_app, tmp_path):
    tree = {"share": {"notes.txt": b"hello\nworld\n"}}
    app = make_app(tree, download_root=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("l")  # notes.txt selected, but NOT downloaded
        preview = app.query_one("#preview", Column)
        assert "hello\nworld" not in preview.rendered_text
        assert "bytes" in preview.rendered_text  # the metadata view ("(N bytes)")
        await pilot.press("q")


async def test_downloaded_binary_shows_hex(make_app, tmp_path):
    blob = bytes(range(48))
    tree = {"share": {"blob.bin": blob}}
    app = make_app(tree, download_root=tmp_path)
    async with app.run_test() as pilot:
        _mirror(app, "share/blob.bin", blob)
        await pilot.press("l")
        preview = app.query_one("#preview", Column)
        assert "00000000  00 01 02 03" in preview.rendered_text  # hex dump
        await pilot.press("q")


async def test_large_download_preview_is_bounded(make_app, tmp_path):
    # A big file must not stall the app: only a bounded prefix is shown, with a marker.
    big = b"A" * (300 * 1024)
    tree = {"share": {"big.txt": big}}
    app = make_app(tree, download_root=tmp_path)
    async with app.run_test() as pilot:
        _mirror(app, "share/big.txt", big)
        await pilot.press("l")
        preview = app.query_one("#preview", Column)
        assert "preview truncated" in preview.rendered_text
        assert len(preview.rendered_text) < 200 * 1024  # not the whole 300 KB
        await pilot.press("q")


async def test_preview_translation_appends_section(make_app, tmp_path, fake_translator):
    tr = fake_translator({"hallo": "hello", "welt": "world"})
    tree = {"share": {"de.txt": b"hallo\nwelt\n"}}
    app = make_app(tree, download_root=tmp_path, translator=tr)  # translate starts on
    async with app.run_test() as pilot:
        _mirror(app, "share/de.txt", b"hallo\nwelt\n")
        await pilot.press("l")  # select de.txt -> preview + translate worker
        for _ in range(20):
            await pilot.pause()
            if "translation" in app.query_one("#preview", Column).rendered_text:
                break
        rendered = app.query_one("#preview", Column).rendered_text
        assert "── translation ──" in rendered and "hello" in rendered
        await pilot.press("q")
