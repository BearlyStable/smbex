"""File content viewer: windowed lazy line reading, and the in-app view mode
(enter on a downloaded text file, scroll to load more, side-by-side translation)."""

from __future__ import annotations

from smbex.ui.columns import Column
from smbex.viewer import LazyHex, LazyLines


# --- LazyLines: only read what's needed --------------------------------------
def test_lazy_lines_windows_without_reading_whole_file(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("".join(f"line{i}\n" for i in range(1000)))
    lz = LazyLines(p, chunk_bytes=128)  # tiny chunks so it must read lazily
    assert lz.window(0, 5) == [f"line{i}" for i in range(5)]
    assert lz.loaded < 1000 and not lz.eof  # did NOT load the whole file

    assert lz.window(500, 3) == ["line500", "line501", "line502"]  # scroll loads more
    lz.load_all()
    assert lz.eof and lz.loaded == 1000
    lz.close()


def test_lazy_lines_window_past_eof(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("a\nb\nc\n")
    lz = LazyLines(p)
    assert lz.window(0, 10) == ["a", "b", "c"]  # clamped to available
    assert lz.eof and lz.loaded == 3
    lz.close()


# --- in-app view mode --------------------------------------------------------
def _mirror(app, remote: str, text: str) -> None:
    local = app._downloads._local_for(remote)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(text)


async def test_enter_scroll_and_leave_file_view(make_app, tmp_path):
    content = "".join(f"row{i}\n" for i in range(200))
    app = make_app({"share": {"big.txt": content.encode()}}, download_root=tmp_path)
    async with app.run_test(size=(90, 24)) as pilot:
        _mirror(app, "share/big.txt", content)
        await pilot.press("l")  # into share (big.txt selected)
        await pilot.press("l")  # open the content view
        await pilot.pause()
        assert app._view is not None
        assert "row0" in app.query_one("#current", Column).rendered_text

        for _ in range(40):  # scroll down; later rows load lazily
            await pilot.press("j")
        mid = app.query_one("#current", Column).rendered_text
        assert "row0" not in mid and "row40" in mid

        await pilot.press("h")  # leave the viewer
        assert app._view is None and app.browser.path == "share"
        assert "docs" not in mid  # (sanity) we were showing file content, not a listing
        await pilot.press("q")


async def test_view_single_column_without_translation(make_app, tmp_path):
    content = "".join(f"row{i}\n" for i in range(200))
    app = make_app({"share": {"big.txt": content.encode()}}, download_root=tmp_path)
    async with app.run_test(size=(90, 24)) as pilot:
        _mirror(app, "share/big.txt", content)
        await pilot.press("l")
        await pilot.press("l")
        await pilot.pause()
        # no translation -> one wide column: the preview column is hidden
        assert app.query_one("#preview", Column).has_class("hidden")
        assert "row0" in app.query_one("#current", Column).rendered_text
        await pilot.press("q")


async def test_view_translation_side_by_side(make_app, tmp_path, fake_translator):
    tr = fake_translator({"hallo": "hello", "welt": "world"})
    content = "hallo\nwelt\n"
    app = make_app({"share": {"de.txt": content.encode()}}, download_root=tmp_path, translator=tr)
    async with app.run_test(size=(90, 24)) as pilot:
        _mirror(app, "share/de.txt", content)
        await pilot.press("l")
        await pilot.press("l")  # enter view; translate is on
        assert app._view is not None
        assert "hallo" in app.query_one("#current", Column).rendered_text  # original
        for _ in range(20):
            await pilot.pause()
            if "hello" in app.query_one("#preview", Column).rendered_text:
                break
        assert "hello" in app.query_one("#preview", Column).rendered_text  # translation
        await pilot.press("q")


def test_lazy_hex_windows_random_access(tmp_path):
    p = tmp_path / "b.bin"
    p.write_bytes(bytes(range(256)))
    hx = LazyHex(p)
    assert hx.rows == 16  # 256 bytes / 16
    w = hx.window(0, 2)
    assert w[0].startswith("00000000  00 01 02 03") and w[0].rstrip().endswith("........")
    assert w[1].startswith("00000010  10 11 12 13")
    assert hx.window(10, 1)[0].startswith("000000a0  a0 a1")  # seeks straight there
    hx.close()


async def test_view_needs_download_then_hex_for_binary(make_app, tmp_path):
    blob = bytes(range(256)) * 4  # 1024 bytes of binary
    tree = {"share": {"notes.txt": b"hi\n", "blob.bin": blob}}
    app = make_app(tree, download_root=tmp_path)
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.press("l")  # into share; notes.txt selected but NOT downloaded
        await pilot.press("l")
        assert app._view is None  # nothing to view -> no-op

        idx = [e.name for e in app.browser.entries].index("blob.bin")
        app.browser.move_to(idx)
        local = app._downloads._local_for("share/blob.bin")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(blob)
        await app._refresh()
        await pilot.press("l")  # downloaded binary -> scrollable hex view
        await pilot.pause()
        assert app._view is not None and app._view.kind == "hex"
        assert "00000000  00 01 02 03" in app.query_one("#current", Column).rendered_text

        for _ in range(10):  # scroll -> later offsets (seeked, not read from start)
            await pilot.press("j")
        assert "000000a0" in app.query_one("#current", Column).rendered_text
        await pilot.press("q")
