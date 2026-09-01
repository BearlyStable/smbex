"""Cursor movement must never wait on the wire.

Moving the cursor renders from memory and only *schedules* the parent/preview
listings; the fetch is debounced and runs in a background worker. These tests pin
the two properties that make scrolling fluid on a slow link:

* a keypress lands even while a listing is stuck in flight, and
* a burst of keypresses costs one listing (where you stopped), not one per row.

Plus the render-side half: a long listing renders only the visible window, scrolled
to keep the cursor on screen.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from smbex.backend.base import DirEntry
from smbex.download import DownloadItem
from smbex.ui.columns import Column

# 30 folders, each with a file — enough that the cursor outruns any fetch.
BIG_TREE = {f"d{i:02d}": {"f.txt": b"x"} for i in range(30)}


async def test_cursor_moves_while_a_listing_is_stuck(make_app):
    """A hung preview fetch must not hold up j/k (the slow-link lag regression)."""
    app = make_app(dict(BIG_TREE))
    app.SIDE_REFRESH_DELAY = 0  # fetch immediately, so one really is in flight
    backend = app._gateway._backend
    for name in BIG_TREE:  # every preview listing blocks forever
        backend.gates[name] = threading.Event()

    async with app.run_test() as pilot:
        await pilot.press("j")  # kicks off a preview fetch that will never return
        await pilot.pause()
        assert backend.list_calls, "the preview fetch should be in flight"

        for _ in range(5):  # ... and the cursor keeps moving regardless
            await pilot.press("j")
        assert app.browser.cursor == 6
        assert app.browser.selected.name == "d06"
        assert "d06" in app.query_one("#current", Column).rendered_text

        for gate in backend.gates.values():  # let the worker drain before teardown
            gate.set()
        await pilot.press("q")


async def test_key_repeat_costs_one_listing(make_app, settle):
    """Holding j over 5 folders fetches only the one you land on."""
    app = make_app(dict(BIG_TREE))
    async with app.run_test() as pilot:
        await settle(app, pilot)  # the initial preview of d00
        backend = app._gateway._backend
        backend.list_calls.clear()

        for _ in range(5):
            await pilot.press("j")
        await settle(app, pilot)

        assert backend.list_calls == ["d05"]  # not d01..d05
        assert app.browser.peek("d05") is not None  # and it did land in the cache
        await pilot.press("q")


async def test_side_columns_fill_in_after_the_move(make_app, settle):
    """An uncached preview shows a placeholder, never a stale neighbour's listing."""
    tree = {"a": {"in-a.txt": b"1"}, "b": {"in-b.txt": b"2"}}
    app = make_app(tree)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        preview = app.query_one("#preview", Column)
        assert "in-a.txt" in preview.rendered_text

        await pilot.press("j")  # onto "b": its listing isn't cached yet
        assert "in-a.txt" not in preview.rendered_text  # no stale listing shown

        await settle(app, pilot)
        assert "in-b.txt" in app.query_one("#preview", Column).rendered_text
        await pilot.press("q")


async def test_long_listing_renders_only_the_visible_window(make_app):
    """A 200-entry folder renders a screenful, scrolled to keep the cursor visible."""
    app = make_app({f"f{i:03d}.txt": b"x" for i in range(200)})
    async with app.run_test(size=(80, 24)) as pilot:
        current = app.query_one("#current", Column)
        rows = current.rendered_text.count("\n") + 1
        assert rows <= current.size.height  # not all 200
        assert "f000.txt" in current.rendered_text

        await pilot.press("G")  # jump to the bottom -> the window follows
        assert "f199.txt" in app.query_one("#current", Column).rendered_text
        assert "f000.txt" not in app.query_one("#current", Column).rendered_text

        await pilot.press("g")  # ... and back
        assert "f000.txt" in app.query_one("#current", Column).rendered_text
        await pilot.press("q")


async def test_window_keeps_the_cursor_visible_while_scrolling(make_app):
    app = make_app({f"f{i:03d}.txt": b"x" for i in range(200)})
    async with app.run_test(size=(80, 24)) as pilot:
        current = app.query_one("#current", Column)
        for step in range(1, 40):
            await pilot.press("j")
            name = f"f{step:03d}.txt"
            assert name in current.rendered_text, f"{name} scrolled out of view"
        await pilot.press("q")


# --- cost of a keystroke ----------------------------------------------------
# These are regression guards for the lag, not benchmarks: the budgets are ~40x the
# measured cost so they hold on a slow machine, but every mistake that caused the
# original lag (a fetch inline in the key handler, rendering the whole listing, or
# scanning every download per entry) blows through them by orders of magnitude.

HUGE = 20_000  # entries in one folder — big, but shares like this exist
BUDGET = 0.025  # seconds of app work per cursor move, worst case


def _entries(count: int) -> list[DirEntry]:
    return [DirEntry(name=f"f{i:06d}.txt", is_dir=False, size=i) for i in range(count)]


async def test_a_cursor_move_makes_no_backend_call(make_app, settle):
    """The key handler itself must never talk to the backend — that was the lag."""
    app = make_app(dict(BIG_TREE))
    async with app.run_test() as pilot:
        await settle(app, pilot)
        backend = app._gateway._backend
        backend.list_calls.clear()

        app.browser.move(1)
        await app._refresh()  # everything a keypress does, minus the deferred fetch

        assert backend.list_calls == []
        await pilot.press("q")


async def test_render_cost_does_not_grow_with_folder_size(make_app):
    """Rendering is O(screen), not O(entries): 20k rows must cost like 100."""
    app = make_app()
    async with app.run_test(size=(100, 30)) as pilot:

        def render_time(count: int) -> float:
            app.browser.entries = _entries(count)
            app.browser.cursor = count // 2
            app._render_current()  # warm
            start = time.perf_counter()
            for _ in range(20):
                app._render_current()
            return time.perf_counter() - start

        small = render_time(100)
        huge = render_time(HUGE)
        assert huge < max(small * 3, 0.2), f"{HUGE} rows cost {huge:.3f}s vs {small:.3f}s"
        await pilot.press("q")


async def test_cursor_move_stays_within_budget_in_a_huge_folder(make_app):
    """A full repaint per keystroke, with a big folder *and* a big download queue.

    The status gutter used to scan every download item for every entry, so this is
    also the guard against that O(entries x downloads) repaint.
    """
    app = make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        app.browser.entries = _entries(HUGE)
        app._downloads.items.extend(
            DownloadItem(f"share/f{i:06d}.txt", Path("x"), status="done")
            for i in range(2000)
        )
        await app._refresh()  # warm

        start = time.perf_counter()
        for _ in range(20):
            app.browser.move(1)
            await app._refresh()
        per_move = (time.perf_counter() - start) / 20
        assert per_move < BUDGET, f"{per_move * 1000:.1f} ms per cursor move"
        await pilot.press("q")


async def test_cursor_stays_fast_while_a_fetch_is_stuck(make_app):
    """The end-to-end promise: a hung listing can't slow the cursor down."""
    app = make_app(dict(BIG_TREE))
    app.SIDE_REFRESH_DELAY = 0
    backend = app._gateway._backend
    for name in BIG_TREE:
        backend.gates[name] = threading.Event()  # every preview listing hangs

    async with app.run_test() as pilot:
        await app.action_cursor_down()  # puts a fetch in flight
        await pilot.pause()
        assert backend.list_calls

        start = time.perf_counter()
        for _ in range(20):
            await app.action_cursor_down()
        per_move = (time.perf_counter() - start) / 20
        assert app.browser.cursor == 21
        assert per_move < BUDGET, f"{per_move * 1000:.1f} ms per cursor move while blocked"

        for gate in backend.gates.values():
            gate.set()
        await pilot.press("q")


async def test_download_progress_repaints_are_coalesced(make_app):
    """A fast transfer reports every 256 KB; repainting the browser each time made
    browsing stutter during a download. Progress is rate-limited, state is not."""
    app = make_app()
    item = DownloadItem("share/big.bin", Path("x"), size=1000, status="running")
    app._downloads.items.append(item)

    assert app._should_paint_downloads() is True  # first paint
    item.downloaded = 256
    assert app._should_paint_downloads() is False  # progress only: coalesced
    item.status = "done"
    assert app._should_paint_downloads() is True  # a state change always paints
