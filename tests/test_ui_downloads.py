"""The task panel: it stays out of the way, shows what's in flight, and lets a
queued transfer be reprioritized.

By default the panel is 'auto': visible only while transfers are in flight, listing
just those, and gone again when they finish. 'w' opens the full list (finished
entries included) and takes j/k for selection, J/K for reordering the queue.
"""

from __future__ import annotations

import threading
from pathlib import Path

from smbex.download import DownloadItem

TREE = {"share": {"a.bin": b"a" * 32, "b.bin": b"b" * 32, "readme.txt": b"hello"}}


def _panel(app):
    return app.query_one("#downloads")


def _hidden(app) -> bool:
    return _panel(app).has_class("hidden")


async def test_download_file_via_keypress(make_app, tmp_path):
    app = make_app(dict(TREE), download_root=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("l")  # into share
        await pilot.press("G")  # bottom row -> "readme.txt" (a file)
        assert app.browser.selected.name == "readme.txt"

        await pilot.press("d")  # download it
        await app.downloads.join()
        await pilot.pause()

        assert (tmp_path / "share" / "readme.txt").read_bytes() == b"hello"
        assert _hidden(app)  # nothing in flight any more -> out of the way again
        await pilot.press("q")


async def test_panel_shows_active_transfers_then_hides(make_app, tmp_path):
    app = make_app(dict(TREE), download_root=tmp_path)
    backend = app._gateway._backend
    gate = threading.Event()
    backend.read_gates["share/a.bin"] = gate  # hold the transfer open
    async with app.run_test() as pilot:
        await pilot.press("l")
        await pilot.press("a")  # grab every file here
        for _ in range(20):
            await pilot.pause()
            if not _hidden(app):
                break
        assert not _hidden(app)  # visible while transfers are in flight
        assert "a.bin" in _panel(app).rendered_text

        gate.set()
        await app.downloads.join()
        await pilot.pause()
        assert _hidden(app)  # ... and gone once the queue drains
        await pilot.press("q")


async def test_hidden_mode_never_shows_the_panel_by_itself(make_app, tmp_path):
    app = make_app(dict(TREE), download_root=tmp_path, download_panel="hidden")
    backend = app._gateway._backend
    gate = threading.Event()
    backend.read_gates["share/a.bin"] = gate
    async with app.run_test() as pilot:
        await pilot.press("l")
        await pilot.press("a")
        for _ in range(10):
            await pilot.pause()
        assert _hidden(app)  # in flight, but the panel stays out of the way

        await pilot.press("w")  # ... until asked for
        assert not _hidden(app)
        gate.set()
        await app.downloads.join()
        await pilot.press("q")


async def test_w_opens_the_full_list_including_finished(make_app, tmp_path):
    app = make_app(dict(TREE), download_root=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("l")
        await pilot.press("a")
        await app.downloads.join()
        await pilot.pause()
        assert _hidden(app)

        await pilot.press("w")
        assert not _hidden(app)
        rendered = _panel(app).rendered_text
        assert "a.bin" in rendered and "b.bin" in rendered  # finished ones too
        assert "downloads 3/3" in rendered

        await pilot.press("w")
        assert _hidden(app)
        await pilot.press("q")


async def test_panel_cursor_and_reordering(make_app, tmp_path):
    app = make_app(dict(TREE), download_root=tmp_path)
    async with app.run_test() as pilot:
        dl = app.downloads
        # A held transfer plus a queue behind it, built directly so the order is exact.
        dl.items.extend(
            [
                DownloadItem("share/running.bin", Path("x"), status="running"),
                DownloadItem("share/one.bin", Path("x"), status="queued"),
                DownloadItem("share/two.bin", Path("x"), status="queued"),
            ]
        )
        await pilot.press("w")  # open -> the cursor lands on the first live transfer
        assert app._panel_open and app._panel_cursor == 0

        await pilot.press("j")
        await pilot.press("j")
        assert app._panel_cursor == 2  # "two.bin"

        await pilot.press("K")  # raise it above "one.bin"
        assert [i.remote_path for i in dl.items] == [
            "share/running.bin",
            "share/two.bin",
            "share/one.bin",
        ]
        assert app._panel_cursor == 1  # the cursor follows the item it moved

        await pilot.press("J")  # ... and back down
        assert [i.remote_path for i in dl.items] == [
            "share/running.bin",
            "share/one.bin",
            "share/two.bin",
        ]
        await pilot.press("q")


async def test_running_transfer_cannot_be_reordered(make_app, tmp_path):
    app = make_app(dict(TREE), download_root=tmp_path)
    async with app.run_test() as pilot:
        dl = app.downloads
        dl.items.extend(
            [
                DownloadItem("share/one.bin", Path("x"), status="queued"),
                DownloadItem("share/running.bin", Path("x"), status="running"),
            ]
        )
        await pilot.press("w")
        app._panel_cursor = 1  # the running one
        await pilot.press("K")
        assert [i.remote_path for i in dl.items] == ["share/one.bin", "share/running.bin"]
        await pilot.press("q")


async def test_panel_keys_do_not_move_the_browser(make_app, tmp_path):
    """While the panel is open it owns j/k — the file listing must not move."""
    app = make_app(dict(TREE), download_root=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("l")
        await pilot.press("a")
        await app.downloads.join()
        await pilot.press("w")
        cursor = app.browser.cursor
        await pilot.press("j")
        await pilot.press("j")
        assert app.browser.cursor == cursor
        await pilot.press("h")  # closes the panel instead of going up a directory
        assert not app._panel_open and app.browser.path == "share"
        await pilot.press("q")


async def test_long_queue_windows_around_the_cursor(make_app, tmp_path):
    app = make_app(dict(TREE), download_root=tmp_path)
    async with app.run_test() as pilot:
        dl = app.downloads
        dl.items.extend(
            DownloadItem(f"share/f{i:02d}.bin", Path("x"), status="queued")
            for i in range(40)
        )
        await pilot.press("w")
        app._panel_cursor = 30
        app._refresh_downloads()
        rendered = _panel(app).rendered_text
        assert "f30.bin" in rendered  # the selected transfer is on screen
        assert "f00.bin" not in rendered  # ... and the panel stays small
        assert rendered.count("\n") < 16
        await pilot.press("q")
