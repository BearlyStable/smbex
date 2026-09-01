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
