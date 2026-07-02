"""Phase 0 smoke tests: the package imports, the CLI parses, the app boots dark."""

from __future__ import annotations

import pytest

import smbex
from smbex.cli import build_parser
from smbex.ui.app import SmbexApp


def test_package_has_version() -> None:
    assert smbex.__version__


def test_parser_version_flag() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0


def _is_dark(app: SmbexApp) -> bool:
    """Resolve dark-mode across Textual versions (theme object or ``dark`` flag)."""
    theme = getattr(app, "current_theme", None)
    if theme is not None and hasattr(theme, "dark"):
        return bool(theme.dark)
    return bool(getattr(app, "dark", False))


async def test_app_boots_in_dark_mode() -> None:
    app = SmbexApp()
    async with app.run_test() as pilot:
        assert _is_dark(app)
        await pilot.press("q")
