"""Textual Pilot: 'y' / 'Y' copy the selected entry's name / full remote path to
the clipboard. Listings render as a rich Table (not selectable text), so these keys
copy the entry directly — full and untruncated, with the English rendering appended
only while translation is active."""

from __future__ import annotations


async def test_y_copies_bare_name(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("j")  # select "share"
        await pilot.press("l")  # enter it: docs/, pics/, readme.txt
        await pilot.press("G")  # to readme.txt
        assert app.browser.selected.name == "readme.txt"
        await pilot.press("y")
        assert app.clipboard == "readme.txt"
        await pilot.press("q")


async def test_Y_copies_full_remote_path(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("j")
        await pilot.press("l")
        await pilot.press("G")  # readme.txt inside share
        await pilot.press("Y")
        assert app.clipboard == "/share/readme.txt"
        await pilot.press("q")


async def test_y_appends_translation_when_active(make_app, fake_translator):
    tr = fake_translator({"readme": "liesmich", "docs": "dokumente"})
    app = make_app(translator=tr)  # configured language -> display on
    async with app.run_test() as pilot:
        await pilot.press("j")
        await pilot.press("l")
        await pilot.press("G")  # readme.txt
        await pilot.press("y")
        assert app.clipboard == "readme.txt → liesmich.txt"  # stem translated, ext kept

        await pilot.press("t")  # translation display off
        await pilot.press("y")
        assert app.clipboard == "readme.txt"  # bare name, no arrow
        await pilot.press("q")


async def test_Y_appends_translation_when_active(make_app, fake_translator):
    tr = fake_translator({"docs": "dokumente"})
    app = make_app(translator=tr)
    async with app.run_test() as pilot:
        await pilot.press("j")
        await pilot.press("l")  # docs/ is first (dirs before files)
        assert app.browser.selected.name == "docs"
        await pilot.press("Y")
        assert app.clipboard == "/share/docs → dokumente"
        await pilot.press("q")


async def test_copy_with_nothing_selected_is_noop(make_app):
    app = make_app({})  # empty root: no entries
    async with app.run_test() as pilot:
        assert app.browser.selected is None
        await pilot.press("y")  # must not raise
        assert app.clipboard == ""  # nothing copied
        await pilot.press("q")
