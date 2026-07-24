"""Pilot tests for the --mux socket picker (headless, offline)."""

from __future__ import annotations

from smbex.mux import MasterInfo
from smbex.ui.mux_picker import MuxPicker

MASTERS = [
    MasterInfo("/run/a.sock", 11, "alice@a"),
    MasterInfo("/run/b.sock", 22, "bob@b"),
    MasterInfo("/run/c.sock", None, "cm-9f8e"),
]


async def test_enter_selects_highlighted_after_moving():
    app = MuxPicker(MASTERS)
    async with app.run_test() as pilot:
        await pilot.press("j")      # -> second row
        await pilot.press("enter")  # OptionList selects highlighted
    assert app.return_value == "/run/b.sock"


async def test_l_key_selects():
    app = MuxPicker(MASTERS)
    async with app.run_test() as pilot:
        await pilot.press("j")
        await pilot.press("j")
        await pilot.press("l")      # vim-style select
    assert app.return_value == "/run/c.sock"


async def test_k_moves_up():
    app = MuxPicker(MASTERS)
    async with app.run_test() as pilot:
        await pilot.press("j")
        await pilot.press("j")
        await pilot.press("k")      # back to the second
        await pilot.press("enter")
    assert app.return_value == "/run/b.sock"


async def test_q_cancels_to_none():
    app = MuxPicker(MASTERS)
    async with app.run_test() as pilot:
        await pilot.press("q")
    assert app.return_value is None


async def test_escape_cancels_to_none():
    app = MuxPicker(MASTERS)
    async with app.run_test() as pilot:
        await pilot.press("escape")
    assert app.return_value is None
