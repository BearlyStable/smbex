"""The '?' help overlay: opens a modal listing keys, dismissible with Esc/?/q."""

from __future__ import annotations

from smbex.ui.help import HelpScreen, help_text


def test_help_text_covers_the_keys():
    plain = help_text().plain
    for token in ("download", "reconnect", "translate", "preload", "sort", "quit"):
        assert token in plain


async def test_question_mark_opens_and_esc_closes(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        assert not isinstance(app.screen, HelpScreen)
        await pilot.press("question_mark")
        assert isinstance(app.screen, HelpScreen)  # overlay is up

        await pilot.press("escape")
        assert not isinstance(app.screen, HelpScreen)  # dismissed
        await pilot.press("q")


async def test_help_does_not_disturb_navigation(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("j")  # select "share"
        await pilot.press("question_mark")  # open help
        await pilot.press("q")  # closes the overlay (not the app)
        assert not isinstance(app.screen, HelpScreen)
        assert app.browser.selected.name == "share"  # underlying state intact
        await pilot.press("l")  # still navigable
        assert app.browser.path == "share"
        await pilot.press("q")