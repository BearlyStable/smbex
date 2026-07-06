"""Theming: startup theme from --theme/config, the 'T' cycle key, and fallback."""

from __future__ import annotations


async def test_default_theme_is_dark(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        assert app.theme == "textual-dark"
        await pilot.press("q")


async def test_theme_flag_sets_startup_theme(make_app):
    app = make_app(theme="light")
    async with app.run_test() as pilot:
        assert app.theme == "textual-light"
        await pilot.press("q")


async def test_unknown_theme_falls_back_to_dark(make_app):
    app = make_app(theme="not-a-real-theme")
    async with app.run_test() as pilot:
        assert app.theme == "textual-dark"
        await pilot.press("q")


async def test_T_cycles_theme(make_app):
    app = make_app()  # starts textual-dark (first in the cycle)
    async with app.run_test() as pilot:
        await pilot.press("T")
        assert app.theme == "textual-light"  # next in the cycle
        first = app.theme
        # a few more presses return to the start without error
        for _ in range(3):
            await pilot.press("T")
        assert app.theme in app.available_themes
        assert app.theme != first  # actually moved on
        await pilot.press("q")


async def test_theme_pass_through_named(make_app):
    app = make_app(theme="nord")  # a real Textual theme, used directly
    async with app.run_test() as pilot:
        assert app.theme == "nord"
        await pilot.press("q")
