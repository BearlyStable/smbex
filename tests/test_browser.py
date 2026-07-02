"""Browser controller: sorting, ranger-style enter/leave, cursor memory, and
that the session cache prevents re-fetching already-seen folders."""

from __future__ import annotations

from smbex.backend.fake_backend import FakeBackend
from smbex.browser import Browser
from smbex.gateway import Gateway

TREE = {
    "other": {},
    "share": {
        "docs": {"a.txt": b"aaa", "b.txt": b"bb"},
        "pics": {"1.png": b"x"},
        "readme.txt": b"hello",
    },
}


async def test_initial_listing_sorted_dirs_first():
    async with Gateway(FakeBackend(TREE)) as gw:
        b = Browser(gw)
        await b.load("")
        assert [e.name for e in b.entries] == ["other", "share"]


async def test_enter_then_leave_restores_cursor():
    async with Gateway(FakeBackend(TREE)) as gw:
        b = Browser(gw)
        await b.load("")
        b.move(1)
        assert b.selected.name == "share"
        await b.enter()
        assert b.path == "share"
        assert [e.name for e in b.entries] == ["docs", "pics", "readme.txt"]
        await b.enter()  # into docs (cursor 0)
        assert b.path == "share/docs"
        await b.go_parent()
        assert b.path == "share"
        assert b.selected.name == "docs"  # cursor restored to where we descended


async def test_cache_hit_avoids_backend_refetch():
    backend = FakeBackend(TREE)
    async with Gateway(backend) as gw:
        b = Browser(gw)
        await b.load("")  # lists roots
        b.move(1)
        await b.enter()  # lists "share"
        await b.go_parent()  # back to roots (cache hit)
        b.move(1)
        await b.enter()  # into "share" again (cache hit)
    assert backend.list_calls.count("") == 1
    assert backend.list_calls.count("share") == 1


async def test_preview_of_selected_directory():
    async with Gateway(FakeBackend(TREE)) as gw:
        b = Browser(gw)
        await b.load("share")  # cursor 0 -> "docs"
        preview = await b.preview_entries()
        assert preview is not None
        assert {e.name for e in preview} == {"a.txt", "b.txt"}


async def test_preview_is_none_for_file():
    async with Gateway(FakeBackend(TREE)) as gw:
        b = Browser(gw)
        await b.load("share")
        b.move_to(2)  # "readme.txt"
        assert b.selected.name == "readme.txt"
        assert await b.preview_entries() is None
