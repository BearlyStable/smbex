"""FTP / FTPS backend on the standard library's ``ftplib`` (no extra runtime dep).

Paths are unified POSIX-style rooted at the server root ("/"), like the SSH
backend: unified "" is "/", the login directory is exposed as ``start_rel`` for the
UI to open at. Listings prefer ``MLSD`` (type/size/modify facts → ``DirEntry``) and
fall back to a best-effort Unix ``LIST`` parse for servers without it.

Ranged reads use ``REST``+``RETR`` (so downloads resume), but FTP has no persistent
per-file handle like SMB/SFTP: ``open_file`` keeps one data connection and reads it
sequentially, reopening only if the caller seeks. ftplib is blocking — always drive
this through the gateway.
"""

from __future__ import annotations

import datetime
from ftplib import FTP, FTP_TLS, error_perm, error_proto, error_temp
from typing import Iterator

from smbex.auth import FtpAuth
from smbex.backend.base import DirEntry

_MLSD_UNSUPPORTED = ("500", "501", "502", "504")


def _parse_mdtm(value: str | None) -> float:
    """MLSD ``modify`` fact (``YYYYMMDDHHMMSS``, UTC) -> epoch seconds; 0 if absent."""
    if not value:
        return 0.0
    try:
        dt = datetime.datetime.strptime(value[:14], "%Y%m%d%H%M%S")
        return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return 0.0


def _parse_unix_list_line(line: str) -> DirEntry | None:
    """Best-effort parse of one ``ls -l``-style LIST line (no mtime — LIST dates are
    lossy). Used only when the server lacks MLSD."""
    parts = line.split(None, 8)
    if len(parts) < 9 or parts[0][:1] not in "-dl":
        return None
    perms, name = parts[0], parts[8]
    is_dir = perms[0] == "d"
    if perms[0] == "l":  # "name -> target"; can't cheaply resolve, treat as file
        name = name.split(" -> ", 1)[0]
    try:
        size = int(parts[4])
    except ValueError:
        size = 0
    return DirEntry(name=name, is_dir=is_dir, size=0 if is_dir else size)


class _FtpFile:
    """One RETR data connection read sequentially; reopened on a non-sequential seek."""

    def __init__(self, backend: "FtpBackend", server_path: str):
        self._backend = backend
        self._path = server_path
        self._conn = None
        self._pos = 0
        self._eof = False

    def read(self, offset: int, length: int) -> bytes:
        if self._conn is None or offset != self._pos:
            self._start(offset)
        buf = bytearray()
        while len(buf) < length:
            data = self._conn.recv(min(65536, length - len(buf)))
            if not data:
                self._finish_eof()
                break
            buf += data
        self._pos += len(buf)
        return bytes(buf)

    def _start(self, offset: int) -> None:
        self.close()
        self._conn = self._backend._retr_socket(self._path, offset)
        self._pos = offset
        self._eof = False

    def _finish_eof(self) -> None:
        try:
            self._conn.close()
        finally:
            self._conn = None
            self._eof = True
            self._backend._read_final_response()

    def close(self) -> None:
        if self._conn is not None and not self._eof:  # stopped before EOF
            self._backend._drain_and_finish(self._conn)
        self._conn = None


class FtpBackend:
    def __init__(self, ftp: FTP, start_rel: str = "", auth: FtpAuth | None = None):
        self._ftp = ftp
        self.start_rel = start_rel
        self._auth = auth  # retained so the gateway can reconnect after a drop

    @staticmethod
    def _open(auth: FtpAuth) -> tuple[FTP, str]:
        ftp: FTP = FTP_TLS() if auth.use_tls else FTP()
        ftp.connect(auth.host, auth.port or 21, timeout=30)
        if auth.username:
            ftp.login(auth.username, auth.password)
        else:
            ftp.login()  # anonymous
        if auth.use_tls:
            ftp.prot_p()  # secure the data channel
        if auth.start_path and auth.start_path not in (".", "/"):
            start = auth.start_path
        else:
            start = ftp.pwd()  # the login directory
        return ftp, start.strip("/")

    @classmethod
    def connect(cls, auth: FtpAuth) -> "FtpBackend":
        ftp, start_rel = cls._open(auth)
        return cls(ftp, start_rel=start_rel, auth=auth)

    # --- paths & listing ------------------------------------------------------
    @staticmethod
    def _server_path(path: str) -> str:
        return "/" + path.replace("\\", "/").strip("/")  # "" -> "/"

    def _list_server(self, server_path: str) -> list[DirEntry]:
        try:
            out = []
            for name, facts in self._ftp.mlsd(server_path, facts=["type", "size", "modify"]):
                typ = facts.get("type", "")
                if typ in ("cdir", "pdir") or name in (".", ".."):
                    continue
                out.append(
                    DirEntry(
                        name=name,
                        is_dir=(typ == "dir"),
                        size=int(facts.get("size") or 0),
                        mtime=_parse_mdtm(facts.get("modify")),
                    )
                )
            return out
        except error_perm as exc:
            code = str(exc)[:3]
            if code in _MLSD_UNSUPPORTED:
                return self._list_via_list(server_path)
            if code == "550":
                raise FileNotFoundError(server_path) from exc
            raise

    def _list_via_list(self, server_path: str) -> list[DirEntry]:
        lines: list[str] = []
        self._ftp.retrlines("LIST " + server_path, lines.append)
        out = []
        for line in lines:
            entry = _parse_unix_list_line(line)
            if entry is not None and entry.name not in (".", ".."):
                out.append(entry)
        return out

    def roots(self) -> list[DirEntry]:
        return self._list_server("/")

    def list(self, path: str) -> list[DirEntry]:
        return self._list_server(self._server_path(path))

    def stat(self, path: str) -> DirEntry:
        server_path = self._server_path(path)
        if server_path == "/":
            return DirEntry(name="/", is_dir=True)
        name = server_path.rsplit("/", 1)[-1]
        parent = server_path.rsplit("/", 1)[0] or "/"
        for entry in self._list_server(parent):
            if entry.name == name:
                return entry
        raise FileNotFoundError(path)

    # --- reads ----------------------------------------------------------------
    def _retr_socket(self, server_path: str, offset: int):
        # Force binary: FTP defaults to ASCII (CRLF translation), and a prior MLSD/
        # LIST leaves the session in TYPE A.
        self._ftp.voidcmd("TYPE I")
        return self._ftp.transfercmd("RETR " + server_path, rest=offset or None)

    def _read_final_response(self) -> None:
        try:
            self._ftp.voidresp()  # consume the closing 226 after a full transfer
        except Exception:
            pass

    def _drain_and_finish(self, conn) -> None:
        # Reads that stop before EOF drain the rest of the data connection rather than
        # ABOR — aborting a RETR desyncs the control channel (leftover 426/226). The
        # drain is ~0 bytes in the normal case (a chunk boundary that lands on EOF).
        try:
            while conn.recv(65536):
                pass
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
            self._read_final_response()

    def open_read(self, path: str, offset: int = 0, chunk: int = 65536) -> Iterator[bytes]:
        conn = self._retr_socket(self._server_path(path), offset)
        reached_eof = False
        try:
            while True:
                data = conn.recv(chunk)
                if not data:
                    reached_eof = True
                    break
                yield data
        finally:
            if reached_eof:
                conn.close()
                self._read_final_response()
            else:
                self._drain_and_finish(conn)  # generator closed early

    def open_file(self, path: str) -> _FtpFile:
        return _FtpFile(self, self._server_path(path))

    # --- recovery / teardown --------------------------------------------------
    def reconnect(self) -> None:
        if self._auth is None:
            raise RuntimeError("no stored credentials to reconnect")
        try:
            self._ftp.close()  # drop the dead socket (local, fast)
        except Exception:
            pass
        self._ftp, _ = self._open(self._auth)  # keep the resolved start_rel

    def is_connection_error(self, exc: BaseException) -> bool:
        # FileNotFoundError is an OSError but here it is *operational* (a 550 we
        # mapped) — must not trigger a reconnect. Everything else socket/transport-y
        # (or a 4xx/protocol error from a wedged control channel) is a lost link.
        if isinstance(exc, FileNotFoundError):
            return False
        if isinstance(exc, (OSError, EOFError, TimeoutError, ConnectionError)):
            return True
        return isinstance(exc, (error_temp, error_proto))

    def close(self) -> None:
        try:
            self._ftp.close()
        except Exception:
            pass
