"""Status gutter in the current column: cached folders, queued/downloaded items.

Pure render over in-memory state (browser.cache + DownloadManager.items) — no
backend calls. Driven headlessly with the Pilot."""

from __future__ import annotations

from pathlib import Path

from smbex.download import DownloadItem
from smbex.ui.columns import Column


async def test_cached_folder_gets_dot_marker(make_app, settle):
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("j")  # select "share"
        await pilot.press("l")  # enter it; the deferred preview caches share/docs
        await settle(app, pilot)
        current = app.query_one("#current", Column)
        assert app.browser.child_path("docs") in app.browser.cache
        assert "·" in current.rendered_text  # cached dir shows the cache glyph
        await pilot.press("q")


async def test_download_states_render_glyphs(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("j")
        await pilot.press("l")  # into "share": docs/, pics/, readme.txt
        b = app.browser
        current = app.query_one("#current", Column)

        # A finished file download -> ✓ on readme.txt
        readme = b.child_path("readme.txt")
        app._downloads.items.append(
            DownloadItem(readme, Path("x"), size=5, downloaded=5, status="done")
        )
        # A queued file *inside* pics/ -> the pics folder aggregates to ↓
        app._downloads.items.append(
            DownloadItem(b.child_path("pics") + "/1.png", Path("y"), status="queued")
        )
        app._on_downloads_change()  # re-render the gutter from new state

        assert "✓" in current.rendered_text  # completed file
        assert "↓" in current.rendered_text  # folder with a queued child
        await pilot.press("q")


async def test_error_download_shows_cross(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("j")
        await pilot.press("l")
        b = app.browser
        app._downloads.items.append(
            DownloadItem(b.child_path("readme.txt"), Path("x"), status="error", error="boom")
        )
        app._on_downloads_change()
        assert "✗" in app.query_one("#current", Column).rendered_text
        await pilot.press("q")


async def test_no_state_no_download_glyphs_at_root(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        # At roots, nothing is downloaded/queued; the two root dirs may be cached only
        # once previewed. The download glyphs must be absent regardless.
        current = app.query_one("#current", Column)
        assert "↓" not in current.rendered_text
        assert "✓" not in current.rendered_text
        assert "✗" not in current.rendered_text
        await pilot.press("q")


async def test_cancelled_download_shows_the_partial_glyph(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("j")
        await pilot.press("l")  # into "share"
        app._downloads.items.append(
            DownloadItem(
                app.browser.child_path("readme.txt"), Path("x"), size=5, downloaded=2,
                status="cancelled",
            )
        )
        app._on_downloads_change()
        assert "⊘" in app.query_one("#current", Column).rendered_text
        await pilot.press("q")
