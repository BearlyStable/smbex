"""Reconnect / error recovery.

Default is manual: a dropped link is reported (on_status 'disconnected') and the
error propagates — no silent reconnect, so a new login event is operator-driven.
`Gateway.reconnect()` (the 'r' key) brings it back. `--auto-reconnect` opts into
transparent heal-and-retry. Driven with FakeBackend's drop simulation."""

from __future__ import annotations

import pytest

from smbex.backend.fake_backend import FakeBackend
from smbex.gateway import Gateway


# --- default: manual -------------------------------------------------------
async def test_default_reports_drop_without_reconnecting():
    backend = FakeBackend({"share": {"a": b"1"}})
    backend.drop_next = 1
    states: list[str] = []
    async with Gateway(backend, on_status=states.append) as gw:
        with pytest.raises(ConnectionError):
            await gw.list("share")  # drops -> reported, propagated, NOT reconnected
        assert backend.reconnects == 0
        assert states == ["disconnected"]
        assert gw.connection_lost is True


async def test_while_down_jobs_fail_fast_without_touching_backend():
    backend = FakeBackend({"share": {"a": b"1"}})
    backend.drop_next = 1
    async with Gateway(backend, on_status=lambda s: None) as gw:
        with pytest.raises(ConnectionError):
            await gw.list("share")  # first drop
        calls_after_drop = len(backend.list_calls)
        with pytest.raises(ConnectionError):
            await gw.list("share")  # known-down: must not hit the backend again
        assert len(backend.list_calls) == calls_after_drop


async def test_manual_reconnect_restores_service():
    backend = FakeBackend({"share": {"a": b"1"}})
    backend.drop_next = 1
    states: list[str] = []
    async with Gateway(backend, on_status=states.append) as gw:
        with pytest.raises(ConnectionError):
            await gw.list("share")
        assert await gw.reconnect() is True  # the 'r' path
        assert backend.reconnects == 1
        assert gw.connection_lost is False
        entries = await gw.list("share")  # works again
        assert [e.name for e in entries] == ["a"]
    assert states == ["disconnected", "reconnecting", "connected"]


async def test_manual_reconnect_can_fail():
    backend = FakeBackend({"share": {}})
    backend.reconnect_fails = 1
    states: list[str] = []
    async with Gateway(backend, on_status=states.append) as gw:
        assert await gw.reconnect() is False
        assert gw.connection_lost is True
    assert states == ["reconnecting", "disconnected"]


# --- opt-in: --auto-reconnect ----------------------------------------------
async def test_auto_reconnect_heals_and_retries():
    backend = FakeBackend({"share": {"a": b"1"}})
    backend.drop_next = 1
    states: list[str] = []
    async with Gateway(
        backend, auto_reconnect=True, on_status=states.append, reconnect_delay=0
    ) as gw:
        entries = await gw.list("share")  # drops -> reconnect -> retry, transparently
    assert [e.name for e in entries] == ["a"]
    assert backend.reconnects == 1
    assert states == ["reconnecting", "connected"]


async def test_auto_reconnect_gives_up_after_attempts():
    backend = FakeBackend({"share": {}})
    backend.drop_next = 1
    backend.reconnect_fails = 9
    states: list[str] = []
    async with Gateway(
        backend, auto_reconnect=True, on_status=states.append,
        reconnect_attempts=2, reconnect_delay=0,
    ) as gw:
        with pytest.raises(ConnectionError):
            await gw.list("share")
    assert backend.reconnects == 0
    assert states == ["reconnecting", "disconnected"]


async def test_operational_error_never_triggers_recovery():
    backend = FakeBackend({"share": {}})
    states: list[str] = []
    async with Gateway(backend, auto_reconnect=True, on_status=states.append) as gw:
        with pytest.raises(FileNotFoundError):
            await gw.list("nope")  # a normal error, not a dropped link
    assert backend.reconnects == 0
    assert states == []


# --- UI: default manual flow -----------------------------------------------
async def test_app_reports_drop_then_r_reconnects(make_app):
    app = make_app()  # default gateway: auto_reconnect off
    async with app.run_test() as pilot:
        backend = app._gateway._backend
        # Roots are loaded with "other" selected (and previewed/cached). Moving to
        # "share" previews it — an uncached fetch — which we drop.
        backend.drop_next = 1
        await pilot.press("j")  # cursor -> "share"; preview fetch drops
        assert app._conn_state == "disconnected"
        assert backend.reconnects == 0
        assert app.browser.selected.name == "share"  # cursor still moved (in-memory)

        await pilot.press("r")  # operator reconnects
        assert app._conn_state == "connected"
        assert backend.reconnects == 1

        await pilot.press("l")  # entering works again
        assert app.browser.path == "share"
        assert [e.name for e in app.browser.entries] == ["docs", "pics", "readme.txt"]
        await pilot.press("q")
