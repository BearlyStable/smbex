"""Phase 7 — offline filename translation (argostranslate).

Translation runs entirely on this machine: argostranslate does inference locally
via CTranslate2, so **no filename ever leaves the box**. The only network this
module performs is one-time model setup in :meth:`ArgosTranslator.install_model`
(downloading a ``.argosmodel``); the translate path touches solely the already
installed local model and never the argos package index.

argostranslate is optional and not apt-installable, so it is imported lazily — the
core app stays fully functional and testable without it. When argostranslate or the
requested language model is absent, the translator reports itself unavailable and
callers fall back to the original name (see :func:`translate_name`).
"""

from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

EN = "en"


@runtime_checkable
class Translator(Protocol):
    """What the UI needs from a translator; a fake can satisfy this in tests."""

    from_code: str
    to_code: str

    @property
    def available(self) -> bool:
        """True only when translation can actually be performed locally."""
        ...

    def translate(self, text: str) -> str:
        """Translate ``text`` to English, or return it unchanged if unavailable."""
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


class ArgosTranslator:
    """Local, offline translator backed by argostranslate.

    Lazy and defensive: constructing one never imports argostranslate or hits the
    network. Availability and the translation callable are resolved on first use
    and memoised; any import/model error degrades to unavailable (identity output).
    """

    def __init__(self, from_code: str, to_code: str = EN):
        self.from_code = from_code
        self.to_code = to_code
        self._cache: dict[str, str] = {}  # session-only; never persisted
        self._fn: Callable[[str], str] | None = None
        self._resolved = False

    # --- availability ---------------------------------------------------------
    def _resolve(self) -> Callable[[str], str] | None:
        """Return the installed from->to translate callable, or None."""
        if self._resolved:
            return self._fn
        self._resolved = True
        try:
            from argostranslate import translate as _t  # lazy, optional dependency

            langs = _t.get_installed_languages()
            src = next((l for l in langs if l.code == self.from_code), None)
            dst = next((l for l in langs if l.code == self.to_code), None)
            if src is not None and dst is not None:
                self._fn = src.get_translation(dst).translate
        except Exception:
            self._fn = None  # argostranslate or the model is absent/broken
        return self._fn

    @property
    def available(self) -> bool:
        return self._resolve() is not None

    # --- translation ----------------------------------------------------------
    def translate(self, text: str) -> str:
        if not text:
            return text
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        fn = self._resolve()
        if fn is None:
            return text  # unavailable: identity, so the UI just shows the original
        try:
            out = fn(text).strip() or text
        except Exception:
            out = text
        self._cache[text] = out
        return out

    # --- one-time, explicit, online setup ------------------------------------
    def install_model(self) -> None:
        """Download and install the ``from_code -> to_code`` model.

        The only networked operation in this module; call it deliberately (the
        ``--install-lang`` CLI path), never during browsing. Raises on failure.
        """
        from argostranslate import package  # lazy, optional dependency

        package.update_package_index()
        available = package.get_available_packages()
        match = next(
            (p for p in available if p.from_code == self.from_code and p.to_code == self.to_code),
            None,
        )
        if match is None:
            raise LookupError(
                f"no argostranslate model for {self.from_code}->{self.to_code} in the index"
            )
        package.install_from_path(match.download())
        self._resolved = False  # re-resolve now that the model is present

    def status(self) -> str:
        """A short human-readable state string for the status bar / CLI."""
        try:
            import argostranslate  # noqa: F401  (probe only)
        except Exception:
            return "argostranslate not installed (pip install argostranslate)"
        if self.available:
            return f"{self.from_code}→{self.to_code}"
        return f"{self.from_code} model missing: run  python -m smbex --install-lang {self.from_code}"
