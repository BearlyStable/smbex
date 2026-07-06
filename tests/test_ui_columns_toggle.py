"""Parent/preview column visibility: '[' and ']' toggles, initial flags, and the
hidden columns skipping their backend fetch."""

from __future__ import annotations

from smbex.ui.columns import Column


def _hidden(app, col_id: str) -> bool:
    return app.query_one(col_id, Column).has_class("hidden")


async def test_brackets_toggle_columns(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        assert not _hidden(app, "#parent") and not _hidden(app, "#preview")

        await pilot.press("[")  # hide parent
        assert _hidden(app, "#parent") and app._show_parent is False
        await pilot.press("]")  # hide preview
        assert _hidden(app, "#preview") and app._show_preview is False

        await pilot.press("[")  # show parent again
        assert not _hidden(app, "#parent") and app._show_parent is True
        await pilot.press("q")


async def test_initial_flags_hide_columns(make_app):
    app = make_app(show_parent=False, show_preview=False)
    async with app.run_test() as pilot:
        assert _hidden(app, "#parent") and _hidden(app, "#preview")
        # current column still works
        await pilot.press("j")
        await pilot.press("l")
        assert app.browser.path == "share"
        await pilot.press("q")


async def test_hidden_preview_skips_its_fetch(make_app):
    # With the preview column hidden, moving the cursor onto a dir must NOT fetch its
    # listing (no preview to render) — saves a round-trip on a slow link.
    app = make_app(show_preview=False)
    async with app.run_test() as pilot:
        backend = app._gateway._backend
        await pilot.press("j")  # select "share" (a dir); preview hidden -> no fetch
        assert "share" not in backend.list_calls  # its listing was never requested
        await pilot.press("q")
