"""End-to-end: the mux backend riding a real OpenSSH ControlMaster socket.

The master is a real ``ssh`` client multiplexing in front of the in-process paramiko
SFTP server (the ``sftp_server`` fixture) — so no ``sshd`` is required, only the ``ssh``
*client* (which the feature shells out to anyway). ``@integration``; skips cleanly if
the OpenSSH client binaries are missing or a master can't be established."""

from __future__ import annotations

import shutil
import subprocess

import pytest

pytestmark = pytest.mark.integration

_COMMON = [
    "-F", "/dev/null",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "BatchMode=yes",
    "-o", "PreferredAuthentications=publickey",
]


def _exit_master(sock: str) -> None:
    subprocess.run(
        ["ssh", "-F", "/dev/null", "-o", "ControlMaster=no", "-O", "exit", "-S", sock, "smbex-mux"],
        capture_output=True, text=True, timeout=10,
    )


@pytest.fixture
def mux_master(sftp_server, tmp_path):
    if shutil.which("ssh") is None or shutil.which("ssh-keygen") is None:
        pytest.skip("no OpenSSH client binaries")
    from smbex.mux import master_check

    key = tmp_path / "id_ed25519"
    sock = tmp_path / "cm.sock"
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", str(key), "-N", "", "-q"], check=True)
    dest = f"tester@{sftp_server['host']}"
    port = str(sftp_server["port"])

    def start_master() -> None:
        subprocess.run(
            ["ssh", "-M", "-S", str(sock), "-N", "-f", *_COMMON, "-i", str(key), "-p", port, dest],
            timeout=20, check=False,
        )

    start_master()
    alive, _pid, info = master_check(str(sock))
    if not alive:
        pytest.skip(f"could not establish an SSH master: {info}")
    try:
        yield {"sock": str(sock), "start_master": start_master}
    finally:
        _exit_master(str(sock))


def test_slave_never_dials_out_when_master_absent():
    """The ProxyCommand=false hardening: with no live master, a slave built from the
    real mux options must fail *locally* — never opening a TCP connection to the
    remote (here a canary listener standing in for the target)."""
    import socket
    import threading

    from smbex import mux

    if shutil.which("ssh") is None:
        pytest.skip("no ssh client binary")

    canary = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    canary.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    canary.bind(("127.0.0.1", 0))
    canary.listen(1)
    canary.settimeout(3.0)
    port = canary.getsockname()[1]
    got: list = []

    def accept_one():
        try:
            conn, _ = canary.accept()
            got.append(conn)
            conn.close()
        except OSError:
            pass

    t = threading.Thread(target=accept_one, daemon=True)
    t.start()
    try:
        # Real mux options, no live master, pointed straight at the canary: the
        # ControlMaster=no fallback must be intercepted by ProxyCommand=false.
        r = subprocess.run(
            ["ssh", *mux._SSH_COMMON, "-o", "ControlPath=/nonexistent/smbex-mux.sock",
             "-p", str(port), "tester@127.0.0.1", "-s", "sftp"],
            capture_output=True, timeout=15,
        )
    finally:
        t.join(timeout=4)
        canary.close()

    assert r.returncode != 0    # fell through to the fallback, which aborted locally
    assert got == []            # ...without ever connecting to the remote


def test_mux_backend_lists_and_reads(mux_master):
    from smbex.mux import MuxBackend

    be = MuxBackend.connect_socket(mux_master["sock"])
    try:
        names = sorted(e.name for e in be.list(""))   # "" -> server "/"
        assert "readme.txt" in names and "logs" in names
        assert b"".join(be.open_read("readme.txt")) == b"hello ssh"
        assert b"".join(be.open_read("readme.txt", offset=6)) == b"ssh"   # seekable
        assert [e.name for e in be.list("logs")] == ["app.log"]
    finally:
        be.close()


def test_mux_open_file_ranged_read(mux_master):
    """The download path: one open_file, then many ranged reads over the mux channel."""
    from smbex.mux import MuxBackend

    be = MuxBackend.connect_socket(mux_master["sock"])
    try:
        f = be.open_file("readme.txt")
        try:
            assert f.read(0, 5) == b"hello"
            assert f.read(6, 3) == b"ssh"
        finally:
            f.close()
    finally:
        be.close()


async def test_mux_reconnect_same_socket(mux_master):
    """The 'r' story: kill the master -> ops fail + disconnected; re-establish the
    master at the SAME socket path -> reconnect() heals. No credentials involved."""
    from smbex.gateway import Gateway
    from smbex.mux import MuxBackend

    be = MuxBackend.connect_socket(mux_master["sock"])
    states: list[str] = []
    async with Gateway(be, on_status=states.append) as gw:
        assert "readme.txt" in [e.name for e in await gw.list("")]

        _exit_master(mux_master["sock"])            # the operator's session drops
        with pytest.raises(Exception):
            await gw.list("")
        assert gw.connection_lost is True
        assert "disconnected" in states
        assert await gw.reconnect() is False         # can't heal while the master is gone

        mux_master["start_master"]()                 # re-establish, same socket name
        assert await gw.reconnect() is True
        assert "readme.txt" in [e.name for e in await gw.list("")]


def test_cli_run_mux_direct_socket(mux_master, monkeypatch):
    """cli._run_mux with a socket-file arg: no picker, connect directly, launch."""
    from smbex.cli import _run_mux, build_parser
    from smbex.ui.app import SmbexApp

    launched: dict = {}
    monkeypatch.setattr(SmbexApp, "run", lambda self: launched.setdefault("app", self))

    args = build_parser().parse_args(["--mux", mux_master["sock"]])
    assert _run_mux(args, None) == 0
    app = launched["app"]  # the constructed-but-not-run app
    assert app._gateway._backend.__class__.__name__ == "MuxBackend"
    app._gateway._backend.close()  # tidy the slave we opened


async def test_mux_browse_through_the_tui(mux_master):
    from smbex.gateway import Gateway
    from smbex.mux import MuxBackend
    from smbex.ui.app import SmbexApp

    be = MuxBackend.connect_socket(mux_master["sock"])
    app = SmbexApp(Gateway(be), start_path=be.start_rel, label="mux")
    async with app.run_test() as pilot:
        names = [e.name for e in app.browser.entries]
        assert "readme.txt" in names and "logs" in names
        app.browser.move_to(names.index("logs"))
        await pilot.press("l")
        assert app.browser.path.endswith("logs")
        assert "app.log" in [e.name for e in app.browser.entries]
        await pilot.press("q")
