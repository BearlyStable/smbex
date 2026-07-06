"""Reconnect / error recovery: the gateway reconnects and retries on a lost link,
leaves operational errors alone, and surfaces link state. Driven with FakeBackend's
drop simulation (drop_next / reconnect_fails)."""

from __future__ import annotations

import pytest

from smbex.backend.fake_backend import FakeBackend
from smbex.gateway import Gateway


async def test_reconnects_and_retries_browse_after_drop():
    backend = FakeBackend({"share": {"a": b"1"}})
    backend.drop_next = 1  # the first list() raises ConnectionError
    states: list[str] = []
    async with Gateway(backend, on_status=states.append, reconnect_delay=0) as gw:
        entries = await gw.list("share")  # drops -> reconnect -> retry succeeds
    assert [e.name for e in entries] == ["a"]
    assert backend.reconnects == 1
    assert states == ["reconnecting", "connected"]


async def test_operational_error_propagates_without_reconnect():
    backend = FakeBackend({"share": {}})
    states: list[str] = []
    async with Gateway(backend, on_status=states.append, reconnect_delay=0) as gw:
        with pytest.raises(FileNotFoundError):
            await gw.list("nope")  # a normal error, not a dropped link
    assert backend.reconnects == 0
    assert states == []  # never entered recovery


async def test_gives_up_after_failed_reconnects():
    backend = FakeBackend({"share": {}})
    backend.drop_next = 1
    backend.reconnect_fails = 9  # every reconnect attempt fails
    states: list[str] = []
    async with Gateway(
        backend, on_status=states.append, reconnect_attempts=2, reconnect_delay=0
    ) as gw:
        with pytest.raises(ConnectionError):
            await gw.list("share")
    assert backend.reconnects == 0  # none succeeded
    assert states == ["reconnecting", "disconnected"]


async def test_retry_is_only_once_if_link_still_flaps():
    backend = FakeBackend({"share": {}})
    backend.drop_next = 2  # drop on the original call and again on the retry
    states: list[str] = []
    async with Gateway(backend, on_status=states.append, reconnect_delay=0) as gw:
        with pytest.raises(ConnectionError):
            await gw.list("share")
    assert backend.reconnects == 1  # reconnected once, retried once, then gave up
    assert states == ["reconnecting", "connected"]


async def test_app_recovers_browsing_after_a_drop(make_app):
    app = make_app()
    async with app.run_test() as pilot:
        backend = app._gateway._backend
        await pilot.press("j")  # select "share"
        backend.drop_next = 1  # entering it will drop the listing once
        await pilot.press("l")  # enter -> list drops -> reconnect -> retry ok
        assert app.browser.path == "share"
        assert [e.name for e in app.browser.entries] == ["docs", "pics", "readme.txt"]
        assert backend.reconnects == 1
        assert app._conn_state == "connected"  # recovered, banner cleared
        await pilot.press("q")
