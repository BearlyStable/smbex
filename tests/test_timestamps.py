"""Timestamps: compact age formatting, sort modes, and the 'o' key + display.

mtime is already carried on DirEntry by both backends; this covers the view layer
(format + sort). Offline via FakeBackend."""

from __future__ import annotations

from smbex.backend.base import DirEntry
from smbex.backend.fake_backend import FakeBackend
from smbex.browser import Browser
from smbex.gateway import Gateway
from smbex.ui.columns import Column, full_time, human_time


# --- formatting --------------------------------------------------------------
def test_human_time_buckets():
    now = 1_000_000_000.0
    assert human_time(0) == ""  # unknown
    assert human_time(now, now=now) == "0m"
    assert human_time(now - 5 * 60, now=now) == "5m"
    assert human_time(now - 3 * 3600, now=now) == "3h"
    assert human_time(now - 2 * 86400, now=now) == "2d"
    assert human_time(now - 3 * 7 * 86400, now=now) == "3w"
    assert human_time(now - 90 * 86400, now=now) == "3mo"
    assert human_time(now - 800 * 86400, now=now) == "2y"


def test_full_time_absolute_and_blank():
    assert full_time(0) == ""
    assert full_time(1_600_000_000.0)  # non-empty absolute stamp


# --- sort modes --------------------------------------------------------------
def _entries():
    # name order: a, b, c ; mtime: a=100, b=300, c=200
    return [
        DirEntry("a.txt", False, 1, 100.0),
        DirEntry("b.txt", False, 1, 300.0),
        DirEntry("c.txt", False, 1, 200.0),
    ]


def test_sorted_by_mode():
    b = Browser.__new__(Browser)  # avoid the gateway; exercise _sorted directly
    b.sort_mode = "name"
    assert [e.name for e in b._sorted(_entries())] == ["a.txt", "b.txt", "c.txt"]
    b.sort_mode = "mtime_desc"
    assert [e.name for e in b._sorted(_entries())] == ["b.txt", "c.txt", "a.txt"]
    b.sort_mode = "mtime_asc"
    assert [e.name for e in b._sorted(_entries())] == ["a.txt", "c.txt", "b.txt"]


async def test_cycle_sort_reorders_and_keeps_selection():
    tree = {"a.txt": b"1", "b.txt": b"2", "c.txt": b"3"}
    mtimes = {"a.txt": 100.0, "b.txt": 300.0, "c.txt": 200.0}
    async with Gateway(FakeBackend(tree, mtimes)) as gw:
        b = Browser(gw)
        await b.load("")
        assert [e.name for e in b.entries] == ["a.txt", "b.txt", "c.txt"]  # name
        b.move_to(2)  # select c.txt
        assert b.cycle_sort() == "mtime_desc"
        assert [e.name for e in b.entries] == ["b.txt", "c.txt", "a.txt"]  # newest first
        assert b.selected.name == "c.txt"  # selection preserved across re-sort
        assert b.cycle_sort() == "mtime_asc"
        assert [e.name for e in b.entries] == ["a.txt", "c.txt", "b.txt"]
        assert b.cycle_sort() == "name"  # wraps back


# --- UI: the 'o' key + rendered time -----------------------------------------
async def test_o_key_cycles_sort_and_shows_time(make_app):
    now_ish = __import__("time").time()
    tree = {"old.txt": b"1", "new.txt": b"2"}
    mtimes = {"old.txt": now_ish - 3 * 86400, "new.txt": now_ish - 60}  # 3d / 1m
    app = make_app(tree, mtimes=mtimes)
    async with app.run_test() as pilot:
        current = app.query_one("#current", Column)
        assert "3d" in current.rendered_text  # compact age is rendered
        assert app.browser.sort_mode == "name"

        await pilot.press("o")  # -> newest first
        assert app.browser.sort_mode == "mtime_desc"
        assert [e.name for e in app.browser.entries] == ["new.txt", "old.txt"]
        await pilot.press("q")
