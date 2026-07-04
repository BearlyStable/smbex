"""Offline filename translation — lean, local, and torch-free.

Translation drives an Argos-format model **directly** with CTranslate2 (inference)
and SentencePiece (tokenisation). That is the whole runtime dependency (~65 MB of
wheels): we deliberately do **not** use the ``argostranslate`` library, whose
transitive ``stanza -> torch`` pull is ~5 GB of CUDA/torch that a filename
translator never needs (torch/stanza only do sentence segmentation, irrelevant for
short names). Output is identical to argostranslate for filenames — the same model,
the same tokeniser, the same ``translate_batch`` parameters.

Privacy: inference is on-box; no filename leaves the machine. The only networked
operation is :func:`install_model` (fetching a model), never the translate path.

A language is one downloadable ``.argosmodel`` file (a zip of ``model/`` +
``sentencepiece.model`` + ``metadata.json``). Models live under the smbex data dir
and are also discovered from an existing argostranslate install, so either source
works. CTranslate2/SentencePiece are imported lazily: if they or the model are
absent, the translator reports unavailable and callers keep the original name.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

EN = "en"

# The Argos community package index (static JSON; each entry has from/to codes and
# a list of ``.argosmodel`` download links). Overridable for tests/mirrors.
INDEX_URL = os.environ.get(
    "SMBEX_MODEL_INDEX",
    "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json",
)

# Some model hosts reject the default urllib User-Agent (HTTP 403), so send a plain
# browser-like one. This is model *download* only — never the translate path.
_UA = "Mozilla/5.0 (compatible; smbex model fetch)"


def _urlopen(url: str, timeout: int):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=timeout
    )


# --- model storage & discovery ----------------------------------------------
def models_root() -> Path:
    """Where smbex keeps installed models (``SMBEX_MODEL_DIR`` overrides)."""
    override = os.environ.get("SMBEX_MODEL_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "smbex" / "models"


def _argos_dirs() -> list[Path]:
    """Existing argostranslate package dirs, so their models are reused as-is."""
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    dirs = [base / "argos-translate" / "packages"]
    env = os.environ.get("ARGOS_PACKAGES_DIR")
    if env:
        dirs.append(Path(env))
    return dirs


def _is_model_dir(path: Path) -> bool:
    return (path / "sentencepiece.model").is_file() and (path / "model").is_dir()


def find_model(from_code: str, to_code: str = EN) -> Path | None:
    """Locate an installed ``from->to`` model dir (smbex's, else argos's)."""
    name = f"{from_code}_{to_code}"
    for parent in [models_root(), *_argos_dirs()]:
        candidate = parent / name
        if _is_model_dir(candidate):
            return candidate
    return None


# --- translator --------------------------------------------------------------
@runtime_checkable
class Translator(Protocol):
    """What the UI needs from a translator; a fake can satisfy this in tests."""

    from_code: str
    to_code: str

    @property
    def available(self) -> bool:
        ...

    def translate(self, text: str) -> str:
        ...


def translate_name(translator: Translator | None, name: str, is_dir: bool = False) -> str:
    """Translate a listing entry, preserving a file's extension.

    Directories, dotfiles (``.bashrc``) and extension-less names are translated
    whole; ``report.txt`` translates the ``report`` stem and keeps ``.txt``. Falls
    back to the original ``name`` when there is no usable translator.
    """
    if translator is None or not translator.available:
        return name
    if is_dir or name.startswith(".") or "." not in name:
        return translator.translate(name)
    stem, _, ext = name.rpartition(".")
    return f"{translator.translate(stem)}.{ext}"


class Ct2Translator:
    """Local translator driving an Argos model via CTranslate2 + SentencePiece.

    Lazy and defensive: constructing one loads nothing. The model, tokeniser and
    availability resolve on first use and are memoised; any import/model error
    degrades to unavailable (identity output). Translations are cached per session
    and never persisted.
    """

    def __init__(self, from_code: str, to_code: str = EN, model_dir: str | Path | None = None):
        self.from_code = from_code
        self.to_code = to_code
        self._model_dir = Path(model_dir) if model_dir else None
        self._cache: dict[str, str] = {}
        self._resolved = False
        self._ct = None  # ctranslate2.Translator
        self._sp = None  # sentencepiece.SentencePieceProcessor
        self._target_prefix = ""

    def _resolve(self) -> bool:
        if self._resolved:
            return self._ct is not None
        self._resolved = True
        try:
            import ctranslate2
            import sentencepiece as spm
        except Exception:
            return False  # runtime stack not installed
        model_dir = self._model_dir or find_model(self.from_code, self.to_code)
        if model_dir is None or not _is_model_dir(model_dir):
            return False
        try:
            self._sp = spm.SentencePieceProcessor(model_file=str(model_dir / "sentencepiece.model"))
            self._ct = ctranslate2.Translator(str(model_dir / "model"), device="cpu")
            meta = model_dir / "metadata.json"
            if meta.is_file():
                self._target_prefix = json.loads(meta.read_text()).get("target_prefix", "") or ""
        except Exception:
            self._ct = None
            return False
        return True

    @property
    def available(self) -> bool:
        return self._resolve()

    def translate(self, text: str) -> str:
        if not text:
            return text
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        if not self._resolve():
            return text  # unavailable: identity, so the UI just shows the original
        try:
            out = self._run(text) or text
        except Exception:
            out = text
        self._cache[text] = out
        return out

    def _run(self, text: str) -> str:
        # Mirrors argostranslate's per-sentence path (same params) so filename
        # output is identical, minus the torch/stanza sentence splitter we skip.
        tokens = self._sp.encode(text, out_type=str)
        prefix = [[self._target_prefix]] if self._target_prefix else None
        result = self._ct.translate_batch(
            [tokens],
            target_prefix=prefix,
            replace_unknowns=True,  # copy source subword instead of emitting <unk>
            max_batch_size=32,
            batch_type="tokens",
            beam_size=4,
            num_hypotheses=1,
            length_penalty=0.2,
        )
        out_tokens = result[0].hypotheses[0]
        value = self._sp.decode_pieces(out_tokens).replace("▁", " ").replace("_", " ")
        if self._target_prefix and value.startswith(self._target_prefix):
            value = value[len(self._target_prefix):]
        return value[1:] if value[:1] == " " else value


# --- one-time, explicit, online model setup ---------------------------------
def install_from_file(argosmodel: str | Path, from_code: str, to_code: str = EN) -> Path:
    """Install a local ``.argosmodel`` (zip) into the smbex model dir. Offline."""
    dest = models_root() / f"{from_code}_{to_code}"
    _extract_argosmodel(Path(argosmodel), dest)
    return dest


def install_model(from_code: str, to_code: str = EN) -> Path:
    """Download the ``from->to`` model from the Argos index and install it.

    The only networked operation in this module; call it deliberately (the
    ``--install-lang`` CLI path), never during browsing. Raises on failure.
    """
    with _urlopen(INDEX_URL, timeout=30) as resp:
        index = json.loads(resp.read())
    entry = next(
        (e for e in index
         if e.get("from_code") == from_code and e.get("to_code") == to_code and e.get("links")),
        None,
    )
    if entry is None:
        raise LookupError(f"no model for {from_code}->{to_code} in the index")
    url = next((link for link in entry["links"] if str(link).startswith("http")), None)
    if url is None:
        raise LookupError(f"no downloadable link for {from_code}->{to_code}")

    tmp = Path(tempfile.mkdtemp()) / "model.argosmodel"
    try:
        with _urlopen(url, timeout=300) as resp, open(tmp, "wb") as f:
            shutil.copyfileobj(resp, f)
        return install_from_file(tmp, from_code, to_code)
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


def _extract_argosmodel(argosmodel: Path, dest: Path) -> None:
    if not zipfile.is_zipfile(argosmodel):
        raise ValueError(f"not a valid .argosmodel (must be a zip): {argosmodel}")
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(argosmodel) as zf:
            zf.extractall(tmp)  # trusted source (Argos index); extracts to a temp dir
        src = _find_model_root(Path(tmp))
        if src is None:
            raise ValueError("archive contains no sentencepiece.model + model/ directory")
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))


def _find_model_root(base: Path) -> Path | None:
    """The dir inside an extracted archive that holds the model (it may be nested)."""
    if _is_model_dir(base):
        return base
    for spm_file in base.rglob("sentencepiece.model"):
        if _is_model_dir(spm_file.parent):
            return spm_file.parent
    return None
