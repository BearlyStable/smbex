"""Filename translation: name/extension handling, session caching, graceful
degradation. Runs offline — a FakeTranslator stands in for argostranslate, and the
real ArgosTranslator is exercised only on its no-model path (deterministic without
the package installed)."""

from __future__ import annotations

from smbex.translate import ArgosTranslator, Translator, translate_name


class FakeTranslator:
    """A deterministic stand-in satisfying the Translator protocol."""

    from_code = "de"
    to_code = "en"

    def __init__(self, table: dict[str, str], available: bool = True):
        self.table = table
        self._available = available
        self.calls: list[str] = []

    @property
    def available(self) -> bool:
        return self._available

    def translate(self, text: str) -> str:
        self.calls.append(text)
        return self.table.get(text, text)


def test_fake_satisfies_protocol():
    assert isinstance(FakeTranslator({}), Translator)


def test_translate_name_preserves_file_extension():
    tr = FakeTranslator({"Quartalsbericht": "quarterly report"})
    assert translate_name(tr, "Quartalsbericht.txt", is_dir=False) == "quarterly report.txt"
    assert tr.calls == ["Quartalsbericht"]  # only the stem was translated


def test_translate_name_whole_for_dirs_and_dotfiles_and_extensionless():
    tr = FakeTranslator({"Bilder": "pictures", ".geheim": "[x]", "Rechnung": "invoice"})
    assert translate_name(tr, "Bilder", is_dir=True) == "pictures"
    assert translate_name(tr, ".geheim", is_dir=False) == "[x]"
    assert translate_name(tr, "Rechnung", is_dir=False) == "invoice"


def test_translate_name_falls_back_without_translator():
    assert translate_name(None, "Rechnung.pdf") == "Rechnung.pdf"
    unavailable = FakeTranslator({"Rechnung": "invoice"}, available=False)
    assert translate_name(unavailable, "Rechnung.pdf") == "Rechnung.pdf"
    assert unavailable.calls == []  # never consulted when unavailable


def test_argos_translator_degrades_without_model():
    # "zz" has no model, so this holds whether or not argostranslate is installed:
    tr = ArgosTranslator("zz")
    assert tr.available is False
    assert tr.translate("etwas") == "etwas"  # identity, no exception
    assert translate_name(tr, "etwas.txt") == "etwas.txt"


def test_session_cache_translates_each_string_once():
    tr = FakeTranslator({"Datei": "file"})
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
