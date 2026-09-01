"""Shared fixtures. The SMB fixture stands up a real local impacket server so the
impacket backend is exercised end-to-end without any external infrastructure."""

from __future__ import annotations

import logging
import socket
import threading
import time

import pytest

# impacket's server logs verbosely; keep test output readable.
logging.getLogger("impacket").setLevel(logging.CRITICAL)

SMB_HOST = "127.0.0.1"
SMB_PORT = 4455
SMB_SHARE = "TESTSHARE"


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def smb_server(tmp_path_factory):
    """A local SimpleSMBServer sharing a small populated tree. Skips if it can't run.

    Session-scoped: the server is a daemon thread with no portable clean-stop, so
    binding it once and sharing it avoids a port clash between test modules.
    """
    try:
        from impacket.smbserver import SimpleSMBServer
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"impacket smbserver unavailable: {exc}")

    share_dir = tmp_path_factory.mktemp("smbshare")
    (share_dir / "hello.txt").write_bytes(b"hello smb")
    sub = share_dir / "sub"
    sub.mkdir()
    (sub / "inner.bin").write_bytes(b"\x00\x01\x02inner")

    try:
        server = SimpleSMBServer(listenAddress=SMB_HOST, listenPort=SMB_PORT)
    except OSError as exc:
        pytest.skip(f"cannot bind SMB test server on {SMB_HOST}:{SMB_PORT}: {exc}")

    server.addShare(SMB_SHARE, str(share_dir), "smbex integration test share")
    server.setSMB2Support(True)

    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()

    deadline = time.time() + 5
    while time.time() < deadline and not _port_open(SMB_HOST, SMB_PORT):
        time.sleep(0.05)
    if not _port_open(SMB_HOST, SMB_PORT):
        pytest.skip("SMB test server did not come up")

    yield {"host": SMB_HOST, "port": SMB_PORT, "share": SMB_SHARE, "dir": share_dir}
    # Daemon thread; impacket's SimpleSMBServer has no portable clean-stop API.


# --- UI / browser fixtures (offline, fake backend) ---------------------------

# A small tree used by the browser and UI tests. Roots are two dirs; "share"
# holds two subdirs and a file. Sorted view: dirs first, then case-insensitive.
UI_TREE = {
    "other": {},
    "share": {
        "docs": {"a.txt": b"aaa", "b.txt": b"bb"},
        "pics": {"1.png": b"x"},
        "readme.txt": b"hello",
    },
}


@pytest.fixture
def make_app():
    """Factory building a SmbexApp over a fresh FakeBackend for Pilot tests."""
    from smbex.backend.fake_backend import FakeBackend
    from smbex.gateway import Gateway
    from smbex.ui.app import SmbexApp

    def _make(tree: dict | None = None, mtimes: dict | None = None, **kwargs):
        backend = FakeBackend(tree if tree is not None else dict(UI_TREE), mtimes=mtimes)
        return SmbexApp(Gateway(backend), **kwargs)

    return _make


@pytest.fixture
def settle():
    """Await an app's deferred parent/preview fetch, then let the UI repaint.

    Cursor moves render from memory and schedule the side-column listings in the
    background (SmbexApp._schedule_side_refresh), so a test that asserts on those
    columns has to wait for them the way a user does.
    """

    async def _settle(app, pilot):
        await pilot.pause()
        await app.wait_for_side_refresh()
        await pilot.pause()

    return _settle


class ChunkGate:
    """A ``FakeBackend.read_gates`` entry that holds a transfer mid-file.

    Lets ``allow`` chunk reads through, then blocks in the backend thread until
    released — so a test can interrupt a download at a known chunk boundary instead
    of racing it to completion.
    """

    def __init__(self, allow: int = 1):
        self.allow = allow
        self.held = threading.Event()  # a read is being held right now
        self.go = threading.Event()  # released

    def wait(self) -> None:  # called on the gateway's worker thread
        if self.allow > 0:
            self.allow -= 1
            return
        self.held.set()
        self.go.wait()

    def release(self) -> None:
        self.go.set()

    async def wait_until_held(self, timeout: float = 5.0) -> None:
        """Await the moment a chunk read is actually being held."""
        import asyncio

        deadline = asyncio.get_running_loop().time() + timeout
        while not self.held.is_set():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("no chunk read was held")
            await asyncio.sleep(0.01)


@pytest.fixture
def chunk_gate():
    """Factory: ``chunk_gate(allow=1)`` — holds a download at a chunk boundary."""
    return ChunkGate


class FakeTranslator:
    """Deterministic stand-in satisfying smbex.translate.Translator, offline.

    Lives here (not in a test module) so tests share it via the ``fake_translator``
    fixture instead of importing across modules — robust even when a dependency
    pollutes site-packages with its own top-level ``tests`` package.
    """

    from_code = "de"
    to_code = "en"

    def __init__(self, table: dict[str, str], available: bool = True):
        self.table = table
        self._available = available
        self.calls: list[str] = []

    @property
    def available(self) -> bool:
        return self._available

    def translate(self, text: str) -> str:
        self.calls.append(text)
        return self.table.get(text, text)


@pytest.fixture
def fake_translator():
    """Factory: ``fake_translator({'wort': 'word'}, available=True)``."""
    return FakeTranslator


@pytest.fixture(scope="session")
def sftp_server(tmp_path_factory):
    """An in-process paramiko SFTP server on localhost serving a temp tree.

    Read-only: enough for the SSH backend's browse + download paths. Accepts any
    credentials. Skips cleanly if paramiko can't stand it up."""
    try:
        import os
        import socket
        import threading

        import paramiko
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"paramiko unavailable: {exc}")

    root = tmp_path_factory.mktemp("sftproot")
    (root / "readme.txt").write_bytes(b"hello ssh")
    logs = root / "logs"
    logs.mkdir()
    (logs / "app.log").write_bytes(b"line1\nline2\n")

    host_key = paramiko.RSAKey.generate(2048)

    class _Server(paramiko.ServerInterface):
        def check_auth_password(self, username, password):
            return paramiko.AUTH_SUCCESSFUL

        def check_auth_publickey(self, username, key):
            return paramiko.AUTH_SUCCESSFUL

        def get_allowed_auths(self, username):
            return "password,publickey"

        def check_channel_request(self, kind, chanid):
            if kind == "session":
                return paramiko.OPEN_SUCCEEDED
            return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    class _FS(paramiko.SFTPServerInterface):
        def __init__(self, server, *largs, **kwargs):
            super().__init__(server)

        def _real(self, path):
            return os.path.join(str(root), path.lstrip("/"))

        def list_folder(self, path):
            out = []
            for name in os.listdir(self._real(path)):
                attr = paramiko.SFTPAttributes.from_stat(os.stat(os.path.join(self._real(path), name)))
                attr.filename = name
                out.append(attr)
            return out

        def stat(self, path):
            try:
                return paramiko.SFTPAttributes.from_stat(os.stat(self._real(path)))
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)

        def lstat(self, path):
            try:
                return paramiko.SFTPAttributes.from_stat(os.lstat(self._real(path)))
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)

        def open(self, path, flags, attr):
            try:
                fileobj = open(self._real(path), "rb")
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)
            handle = paramiko.SFTPHandle(flags)
            handle.readfile = fileobj
            return handle

    listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen.bind(("127.0.0.1", 0))
    listen.listen(5)
    port = listen.getsockname()[1]

    def serve():
        while True:
            try:
                conn, _ = listen.accept()
            except OSError:
                break
            transport = paramiko.Transport(conn)
            transport.add_server_key(host_key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, _FS)
            try:
                transport.start_server(server=_Server())
            except Exception:
                pass

    threading.Thread(target=serve, daemon=True).start()
    yield {"host": "127.0.0.1", "port": port, "root": root}
    listen.close()


@pytest.fixture(scope="session")
def ftp_server(tmp_path_factory):
    """An in-process pyftpdlib FTP server on an ephemeral localhost port.

    User ``tester``/``secret`` over a temp tree (incl. a >64 KB file to exercise
    chunked/partial reads). Skips cleanly if pyftpdlib is unavailable."""
    try:
        import threading

        from pyftpdlib.authorizers import DummyAuthorizer
        from pyftpdlib.handlers import FTPHandler
        from pyftpdlib.servers import FTPServer
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"pyftpdlib unavailable: {exc}")

    root = tmp_path_factory.mktemp("ftproot")
    (root / "readme.txt").write_bytes(b"hello ftp")
    (root / "big.bin").write_bytes(b"x" * 200_000)  # multi-recv / partial-read cases
    logs = root / "logs"
    logs.mkdir()
    (logs / "app.log").write_bytes(b"line1\nline2\n")

    authorizer = DummyAuthorizer()
    authorizer.add_user("tester", "secret", str(root), perm="elr")  # list + retrieve

    class _Handler(FTPHandler):
        authorizer = None  # set below

    _Handler.authorizer = authorizer
    _Handler.log_prefix = ""  # keep test output quiet

    logging.getLogger("pyftpdlib").setLevel(logging.CRITICAL)
    server = FTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"host": host, "port": port, "root": root}
    server.close_all()
