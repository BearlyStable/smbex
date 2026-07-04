"""Japanese end-to-end translation, driving a real ja->en model via CTranslate2 +
SentencePiece (no argostranslate at runtime).

Marked ``integration``: these run only where ctranslate2/sentencepiece and a ja->en
model are installed (``python -m smbex --install-lang ja``); otherwise they skip
cleanly, so the offline suite is unaffected. They exercise the real model, the
extension handling, and the in-app 't' toggle over a Japanese-named tree."""

from __future__ import annotations

import pytest

from smbex.translate import Ct2Translator, translate_name

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def ja() -> Ct2Translator:
    pytest.importorskip("ctranslate2")
    pytest.importorskip("sentencepiece")
    tr = Ct2Translator("ja")
    if not tr.available:
        pytest.skip("ja->en model not installed (run: python -m smbex --install-lang ja)")
    return tr


def test_japanese_words_translate_to_english(ja):
    # Stable, high-confidence renderings from the ja->en model.
    assert ja.translate("写真") == "Photos"
    assert ja.translate("音楽") == "Music"
    assert ja.translate("会議メモ") == "Conference notes"


def test_japanese_filename_keeps_extension(ja):
    assert translate_name(ja, "地図.png") == "Map.png"  # stem translated, ext kept
    assert translate_name(ja, "写真", is_dir=True) == "Photos"  # directory translated whole


def test_translation_is_english_ascii(ja):
    # Robust property (version-independent): a known word yields non-empty ASCII.
    out = ja.translate("旅行")  # "Travel"
    assert out and out.isascii() and out != "旅行"


# --- the on/off toggle, with the real model, over Japanese filenames ---------

JP_TREE = {
    "写真": {"家族.jpg": b"x", "東京.jpg": b"y"},  # Photos: Family, Tokyo
    "地図.png": b"m",  # Map
}


async def test_t_toggles_japanese_translation_on_and_off(ja, make_app):
    app = make_app(JP_TREE, translator=ja)  # a language configured -> display starts on
    async with app.run_test() as pilot:
        from smbex.ui.columns import Column

        current = app.query_one("#current", Column)
        assert app.translate_enabled is True
        assert "写真" in current.rendered_text  # original kept
        assert "Photos" in current.rendered_text  # English shown beside it

        await pilot.press("t")  # toggle OFF
        assert app.translate_enabled is False
        assert "Photos" not in current.rendered_text
        assert "写真" in current.rendered_text  # original still there

        await pilot.press("t")  # toggle back ON
        assert app.translate_enabled is True
        assert "Photos" in app.query_one("#current", Column).rendered_text
        await pilot.press("q")
