"""Textual Pilot tests: ranger keys drive navigation, and reserved keys no-op."""

from __future__ import annotations

from smbex.ui.columns import Column


async def test_ranger_navigation(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        b = app.browser
        assert b.path == ""  # starts at the share picker (roots)
        assert [e.name for e in b.entries] == ["other", "share"]

        await pilot.press("j")  # move down to "share"
        assert b.selected.name == "share"
        await pilot.press("l")  # enter it
        assert b.path == "share"
        assert [e.name for e in b.entries] == ["docs", "pics", "readme.txt"]

        await pilot.press("l")  # enter "docs"
        assert b.path == "share/docs"

        await pilot.press("h")  # back up to "share"
        assert b.path == "share"
        assert b.selected.name == "docs"  # cursor restored
        await pilot.press("q")


async def test_top_and_bottom_keys(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("l")  # enter first root ("other" is empty) -> stays? no: enter "other"
        # "other" is empty; cursor stays. Go back and into "share" for a real list.
        await pilot.press("h")
        await pilot.press("j")  # select "share"
        await pilot.press("l")  # enter "share" (3 entries)
        b = app.browser
        await pilot.press("G")  # bottom
        assert b.cursor == b.count - 1
        await pilot.press("g")  # top
        assert b.cursor == 0
        await pilot.press("q")


async def test_preload_toggle_and_reserved_keys(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        before = app.browser.preload_enabled
        await pilot.press("p")
        assert app.browser.preload_enabled is not before
        # reserved no-ops must not raise
        await pilot.press("t")
        await pilot.press("d")
        await pilot.press("q")


async def test_current_column_renders_selection(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("j")  # select "share"
        current = app.query_one("#current", Column)
        assert "share" in [e.name for e in current.shown]
        await pilot.press("q")
