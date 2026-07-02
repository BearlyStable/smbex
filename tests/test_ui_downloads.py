"""Downloading through the TUI: a keypress enqueues, the file lands in the mirror,
and the task panel toggles."""

from __future__ import annotations


async def test_download_file_via_keypress(make_app, tmp_path):
    app = make_app(download_root=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("j")  # select "share"
        await pilot.press("l")  # enter it
        await pilot.press("G")  # bottom row -> "readme.txt" (a file)
        assert app.browser.selected.name == "readme.txt"

        await pilot.press("d")  # download it
        await app.downloads.join()

        assert (tmp_path / "share" / "readme.txt").read_bytes() == b"hello"
        assert not app.query_one("#downloads").has_class("hidden")  # panel revealed
        await pilot.press("q")


async def test_download_panel_toggles(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        panel = app.query_one("#downloads")
        assert panel.has_class("hidden")
        await pilot.press("w")
        assert not panel.has_class("hidden")
        await pilot.press("w")
        assert panel.has_class("hidden")
        await pilot.press("q")
