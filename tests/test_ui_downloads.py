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


async def test_pushing_the_running_transfer_down_makes_it_yield(make_app, tmp_path):
    """'J' on the transfer in flight hands the wire to the one behind it."""
    app = make_app(dict(TREE), download_root=tmp_path)
    async with app.run_test() as pilot:
        dl = app.downloads
        running = DownloadItem("share/big.bin", Path("x"), size=100, downloaded=40,
                               status="running")
        waiting = DownloadItem("share/small.txt", Path("x"), status="queued")
        dl.items.extend([running, waiting])

        await pilot.press("w")
        app._panel_cursor = 0  # the running one
        await pilot.press("J")

        assert [i.remote_path for i in dl.items] == ["share/small.txt", "share/big.bin"]
        assert running.control == "yield"  # it stops at the next chunk boundary
        assert "yields at 40%" in str(app.query_one("#status").render())
        await pilot.press("q")


async def test_pulling_one_up_past_the_running_transfer_preempts_it(make_app, tmp_path):
    app = make_app(dict(TREE), download_root=tmp_path)
    async with app.run_test() as pilot:
        dl = app.downloads
        running = DownloadItem("share/big.bin", Path("x"), size=100, status="running")
        waiting = DownloadItem("share/small.txt", Path("x"), status="queued")
        dl.items.extend([running, waiting])

        await pilot.press("w")
        app._panel_cursor = 1  # the queued one
        await pilot.press("K")

        assert [i.remote_path for i in dl.items] == ["share/small.txt", "share/big.bin"]
        assert running.control == "yield"
        await pilot.press("q")


async def test_x_cancels_the_selected_transfer(make_app, tmp_path):
    app = make_app(dict(TREE), download_root=tmp_path)
    async with app.run_test() as pilot:
        dl = app.downloads
        item = DownloadItem("share/one.bin", Path("x"), status="queued")
        dl.items.append(item)

        await pilot.press("w")
        app._panel_cursor = 0
        await pilot.press("x")

        assert item.status == "cancelled"
        assert item in dl.items  # still listed, so you can see what happened
        await pilot.press("q")


async def test_x_clears_a_finished_entry(make_app, tmp_path):
    app = make_app(dict(TREE), download_root=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("l")
        await pilot.press("a")  # grab the files here
        await app.downloads.join()
        await pilot.press("w")
        assert app.downloads.items

        for _ in range(len(app.downloads.items)):
            app._panel_cursor = 0
            await pilot.press("x")
        assert app.downloads.items == []  # the list can be cleared entry by entry
        await pilot.press("q")


async def test_cancelling_a_running_download_stops_it_through_the_ui(
    make_app, tmp_path, chunk_gate
):
    """End-to-end: a real transfer in flight, cancelled with 'x' from the panel."""
    from smbex.download import CHUNK

    big = b"B" * (CHUNK * 3)
    app = make_app({"share": {"big.bin": big, "small.txt": b"hi"}}, download_root=tmp_path)
    gate = chunk_gate(allow=1)
    app._gateway._backend.read_gates["share/big.bin"] = gate

    async with app.run_test() as pilot:
        await pilot.press("l")
        await pilot.press("a")  # queue both files
        await gate.wait_until_held()  # big.bin is mid-transfer
        await pilot.pause()

        await pilot.press("w")
        app._panel_cursor = [i.remote_path for i in app._panel_items()].index("share/big.bin")
        await pilot.press("x")
        gate.release()
        await app.downloads.join()

        cancelled = next(i for i in app.downloads.items if i.remote_path == "share/big.bin")
        assert cancelled.status == "cancelled"
        partial = (tmp_path / "share" / "big.bin").stat().st_size
        assert 0 < partial < len(big)  # partial kept, so re-grabbing resumes
        assert (tmp_path / "share" / "small.txt").read_bytes() == b"hi"  # queue moved on
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


async def test_ftp_refuses_to_stop_a_running_transfer_and_says_why(make_app, tmp_path):
    """On FTP the keys stay, but they explain instead of throwing a file away."""
    app = make_app(dict(TREE), download_root=tmp_path)
    app._gateway._backend.interruptible = False  # as the FTP backend declares
    async with app.run_test() as pilot:
        dl = app.downloads
        running = DownloadItem("share/big.bin", Path("x"), size=100, status="running")
        waiting = DownloadItem("share/small.txt", Path("x"), status="queued")
        dl.items.extend([running, waiting])

        await pilot.press("w")
        assert "queued only" in _panel(app).rendered_text  # the hint says so up front

        app._panel_cursor = 0  # the running one
        await pilot.press("x")
        assert running.status == "running" and running.control == ""
        assert "can't be stopped early" in str(app.query_one("#status").render())

        await pilot.press("J")  # ... and it can't be pushed down either
        assert [i.remote_path for i in dl.items] == ["share/big.bin", "share/small.txt"]

        app._panel_cursor = 1  # but the queued one can still be cancelled
        await pilot.press("x")
        assert waiting.status == "cancelled"
        await pilot.press("q")
