"""Phase 6 preloader: neighbour selection, PRELOAD-priority prefetch, cache
warming, the toggle, and de-duplication of in-flight/cached paths.

Driven with FakeBackend so it stays deterministic and offline."""

from __future__ import annotations

from smbex.backend.fake_backend import FakeBackend
from smbex.browser import Browser
from smbex.gateway import Gateway, Priority

TREE = {
    "share": {
        "docs": {"a.txt": b"a", "sub": {"deep.txt": b"d"}},
        "music": {"song.mp3": b"m"},
        "pics": {"1.png": b"x"},
        "readme.txt": b"hi",
    },
}


def _spy_list(gw):
    """Wrap ``gw.list`` to record ``(path, priority)`` of every call."""
    seen: list[tuple[str, int]] = []
    orig = gw.list

    async def spy(path, priority=Priority.BROWSE):
        seen.append((path, int(priority)))
        return await orig(path, priority=priority)

    gw.list = spy
    return seen


async def test_neighbors_selected_first_then_siblings_then_parent():
    async with Gateway(FakeBackend(TREE)) as gw:
        b = Browser(gw, preload=True)
        await b.load("share")  # sorted: docs, music, pics, readme.txt; cursor 0 -> docs
        n = b.preloader.neighbors(b.path, b.entries, b.selected)
        assert n[0] == "share/docs"  # selected subdirectory prefetched first
        assert n[-1] == ""  # parent (roots) last
        assert set(n) == {"share/docs", "share/music", "share/pics", ""}
        assert "share/readme.txt" not in n  # files are never preload targets
        await b.preloader.wait()


async def test_preload_warms_siblings_at_preload_priority():
    backend = FakeBackend(TREE)
    async with Gateway(backend) as gw:
        seen = _spy_list(gw)
        b = Browser(gw, preload=True)
        await b.load("share")  # current dir at BROWSE; neighbours at PRELOAD
        await b.preloader.wait()

    preloaded = {p for p, pr in seen if pr == int(Priority.PRELOAD)}
    assert {"share/docs", "share/music", "share/pics"} <= preloaded
    # The current directory itself is a normal browse, not a preload.
    assert ("share", int(Priority.BROWSE)) in seen
    assert ("share", int(Priority.PRELOAD)) not in seen
    # Neighbours are now in the session cache, ready for instant navigation.
    assert "share/pics" in b.cache
    assert "share/music" in b.cache


async def test_preloaded_sibling_entered_without_refetch():
    backend = FakeBackend(TREE)
    async with Gateway(backend) as gw:
        b = Browser(gw, preload=True)
        await b.load("share")
        await b.preloader.wait()
        assert backend.list_calls.count("share/pics") == 1  # preloaded exactly once

        b.move_to([e.name for e in b.entries].index("pics"))
        await b.enter()  # served from the preload-warmed cache
        await b.preloader.wait()
        assert b.path == "share/pics"
        assert backend.list_calls.count("share/pics") == 1  # no second listing


async def test_preload_disabled_does_not_prefetch():
    backend = FakeBackend(TREE)
    async with Gateway(backend) as gw:
        b = Browser(gw, preload=False)
        await b.load("share")
        await b.preloader.wait()  # nothing was queued
    assert backend.list_calls == ["share"]  # only the current directory was listed
    assert "share/pics" not in b.cache


async def test_preload_skips_already_cached_paths():
    backend = FakeBackend(TREE)
    async with Gateway(backend) as gw:
        b = Browser(gw, preload=True)
        await b.load("share")
        await b.preloader.wait()
        before = list(backend.list_calls)
        # Re-running preload for the same view must issue no new backend listings.
        b.preload_surroundings()
        await b.preloader.wait()
    assert backend.list_calls == before


async def test_preload_survives_unreadable_neighbour():
    # "share/music" fails to list; preloading must not raise and must still warm
    # the readable neighbours.
    backend = FakeBackend(TREE)
    real_list = backend.list

    def flaky_list(path):
        if path == "share/music":
            raise PermissionError(path)
        return real_list(path)

    backend.list = flaky_list
    async with Gateway(backend) as gw:
        b = Browser(gw, preload=True)
        await b.load("share")
        await b.preloader.wait()
    assert "share/music" not in b.cache
    assert "share/pics" in b.cache  # a sibling that lists fine is still warmed


# --- UI wiring -------------------------------------------------------------


async def test_toggle_p_warms_current_neighbourhood(make_app):
    app = make_app({"share": dict(TREE["share"])}, preload=False)
    async with app.run_test() as pilot:
        await pilot.press("l")  # enter the sole root "share"
        assert app.browser.path == "share"
        assert "share/pics" not in app.browser.cache  # preload off: not warmed

        await pilot.press("p")  # enable preloading -> warm the neighbourhood now
        assert app.browser.preload_enabled is True
        await app.browser.preloader.wait()
        assert "share/pics" in app.browser.cache
        assert "share/music" in app.browser.cache
        await pilot.press("q")
