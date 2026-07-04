"""Filename translation: name/extension handling, session caching, graceful
degradation, and model install/discovery. Runs offline — a FakeTranslator stands
in for the engine, and Ct2Translator is exercised on its no-model path and with a
synthetic .argosmodel (no real model or network needed)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from smbex.translate import Ct2Translator, Translator, find_model, install_from_file, translate_name


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


def test_session_cache_translates_each_string_once(fake_translator):
    tr = fake_translator({"Datei": "file"})
    # translate() is the cached unit; drive it directly.
    assert tr.translate("Datei") == "file"
    assert tr.translate("Datei") == "file"
    assert tr.calls == ["Datei", "Datei"]  # FakeTranslator has no cache of its own


# --- Ct2Translator, without any real model ----------------------------------
def test_ct2_translator_degrades_without_model(tmp_path, monkeypatch):
    # Point discovery at an empty dir so no model is ever found.
    monkeypatch.setenv("SMBEX_MODEL_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    tr = Ct2Translator("zz")
    assert tr.available is False
    assert tr.translate("etwas") == "etwas"  # identity, no exception
    assert translate_name(tr, "etwas.txt") == "etwas.txt"


def test_ct2_translator_caches_within_session():
    tr = Ct2Translator("de")
    calls: list[str] = []
    # Pretend the model resolved; exercise Ct2Translator's own session cache.
    tr._resolved = True
    tr._ct = object()  # non-None so _resolve() reports available
    tr._sp = object()
    tr._run = lambda text: (calls.append(text), {"Datei": "file"}.get(text, text))[1]
    assert tr.translate("Datei") == "file"
    assert tr.translate("Datei") == "file"
    assert calls == ["Datei"]  # second lookup served from the session cache


# --- model install + discovery, with a synthetic .argosmodel ----------------
def _fake_argosmodel(path: Path, code: str = "xx_en") -> Path:
    """A minimal, well-formed .argosmodel zip (no real weights)."""
    archive = path / "fake.argosmodel"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"{code}/metadata.json", json.dumps({"target_prefix": ""}))
        zf.writestr(f"{code}/sentencepiece.model", b"not-a-real-spm")
        zf.writestr(f"{code}/model/model.bin", b"not-a-real-ct2-model")
        zf.writestr(f"{code}/model/shared_vocabulary.txt", "a\nb\n")
    return archive


def test_install_from_file_then_find_model(tmp_path, monkeypatch):
    monkeypatch.setenv("SMBEX_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "empty"))  # no argos models here
    archive = _fake_argosmodel(tmp_path)

    assert find_model("xx", "en") is None  # nothing installed yet
    dest = install_from_file(archive, "xx", "en")
    assert dest.is_dir()
    assert (dest / "sentencepiece.model").is_file()
    assert (dest / "model").is_dir()
    assert find_model("xx", "en") == dest  # now discoverable
