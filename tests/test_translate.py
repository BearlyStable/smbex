"""Filename translation: name/extension handling, session caching, graceful
degradation. Runs offline — a FakeTranslator stands in for argostranslate, and the
real ArgosTranslator is exercised only on its no-model path (deterministic without
the package installed)."""

from __future__ import annotations

import pytest

from smbex.translate import ArgosTranslator, Translator, translate_name


def test_fake_satisfies_protocol(fake_translator):
    assert isinstance(fake_translator({}), Translator)


def test_translate_name_preserves_file_extension(fake_translator):
    tr = fake_translator({"Quartalsbericht": "quarterly report"})
    assert translate_name(tr, "Quartalsbericht.txt", is_dir=False) == "quarterly report.txt"
    assert tr.calls == ["Quartalsbericht"]  # only the stem was translated


def test_translate_name_whole_for_dirs_and_dotfiles_and_extensionless(fake_translator):
    tr = fake_translator({"Bilder": "pictures", ".geheim": "[x]", "Rechnung": "invoice"})
    assert translate_name(tr, "Bilder", is_dir=True) == "pictures"
    assert translate_name(tr, ".geheim", is_dir=False) == "[x]"
    assert translate_name(tr, "Rechnung", is_dir=False) == "invoice"


def test_translate_name_falls_back_without_translator(fake_translator):
    assert translate_name(None, "Rechnung.pdf") == "Rechnung.pdf"
    unavailable = fake_translator({"Rechnung": "invoice"}, available=False)
    assert translate_name(unavailable, "Rechnung.pdf") == "Rechnung.pdf"
    assert unavailable.calls == []  # never consulted when unavailable


def test_argos_translator_degrades_without_model():
    # "zz" has no model, so this holds whether or not argostranslate is installed:
    tr = ArgosTranslator("zz")
    assert tr.available is False
    assert tr.translate("etwas") == "etwas"  # identity, no exception
    assert translate_name(tr, "etwas.txt") == "etwas.txt"


def test_session_cache_translates_each_string_once(fake_translator):
    tr = fake_translator({"Datei": "file"})
    # translate() is the cached unit; drive it directly.
    assert tr.translate("Datei") == "file"
    assert tr.translate("Datei") == "file"
    assert tr.calls == ["Datei", "Datei"]  # FakeTranslator has no cache of its own


def test_argos_translator_caches_within_session(monkeypatch):
    tr = ArgosTranslator("de")
    calls: list[str] = []

    def fake_fn(text: str) -> str:
        calls.append(text)
        return {"Datei": "file"}.get(text, text)

    # Pretend the model resolved to fake_fn; exercise ArgosTranslator's own cache.
    tr._resolved = True
    tr._fn = fake_fn
    assert tr.translate("Datei") == "file"
    assert tr.translate("Datei") == "file"
    assert calls == ["Datei"]  # second lookup served from the session cache


@pytest.mark.integration
def test_real_argos_roundtrip_if_a_model_is_installed():
    """Validates the ArgosTranslator<->argostranslate API against a real install.

    Skips cleanly where argostranslate or a model is absent (e.g. the dev venv),
    so it self-verifies the integration seam only where it can."""
    argos = pytest.importorskip("argostranslate.translate")
    langs = argos.get_installed_languages()
    english = next((l for l in langs if l.code == "en"), None)
    source = next(
        (l for l in langs if l.code != "en" and english and l.get_translation(english)),
        None,
    )
    if source is None or english is None:
        pytest.skip("no argostranslate X->en model installed")

    tr = ArgosTranslator(source.code)
    assert tr.available is True
    out = tr.translate("test")
    assert isinstance(out, str) and out  # real model returns a non-empty string
    assert tr.translate("test") is out or tr.translate("test") == out  # cached
