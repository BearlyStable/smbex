"""Textual Pilot: the 't' toggle shows/hides English filename translations beside
the originals, driven by an injected FakeTranslator (no argostranslate needed)."""

from __future__ import annotations

from smbex.ui.columns import Column


async def test_translation_shown_beside_original_and_toggles(make_app, fake_translator):
    tr = fake_translator({"docs": "documents", "pics": "pictures", "readme": "readme"})
    app = make_app(translator=tr)  # a configured language -> display starts on
    async with app.run_test() as pilot:
        await pilot.press("j")  # select "share"
        await pilot.press("l")  # enter it: docs, pics, readme.txt
        current = app.query_one("#current", Column)
        assert "docs" in current.rendered_text  # original kept
        assert "documents" in current.rendered_text  # English shown beside it
        assert "pictures" in current.rendered_text

        await pilot.press("t")  # toggle translations off
        assert app.translate_enabled is False
        assert "documents" not in current.rendered_text
        assert "docs" in current.rendered_text  # original still there

        await pilot.press("t")  # back on
        assert "documents" in app.query_one("#current", Column).rendered_text
        await pilot.press("q")


async def test_extensionful_file_keeps_extension_in_translation(make_app, fake_translator):
    tree = {"share": {"Rechnung.pdf": b"x", "Bilder": {"1.png": b"y"}}}
    tr = fake_translator({"Rechnung": "invoice", "Bilder": "pictures"})
    app = make_app(tree, translator=tr)
    async with app.run_test() as pilot:
        await pilot.press("l")  # enter "share"
        current = app.query_one("#current", Column)
        assert "invoice.pdf" in current.rendered_text  # stem translated, ext kept
        assert "pictures" in current.rendered_text  # directory translated whole
        await pilot.press("q")


async def test_no_translator_leaves_listing_untouched(make_app):
    app = make_app()  # no translator configured
    async with app.run_test() as pilot:
        assert app.translate_enabled is False
        await pilot.press("t")  # must not raise without a translator
        await pilot.press("j")
        await pilot.press("l")
        current = app.query_one("#current", Column)
        assert "→" not in current.rendered_text  # nothing added
        await pilot.press("q")
