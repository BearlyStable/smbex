"""SMB backend built on impacket's ``SMBConnection``.

Paths are unified POSIX-style; the first component is the share name. impacket
speaks backslash paths with a ``*`` wildcard for listings, so we translate at the
boundary. impacket is blocking — always drive this through the gateway.
"""

from __future__ import annotations

from typing import Iterator

from smbex.auth import SmbAuth, smb_kerberos_kwargs, smb_login_kwargs
from smbex.backend.base import DirEntry


class _SmbFile:
    """An open SMB file: one tree-connect + open, then many ranged reads.

    This keeps a download's wire/audit footprint close to a normal SMB client
    (one CREATE/CLOSE and one TREE_CONNECT/DISCONNECT per file), instead of one
    per chunk."""

    def __init__(self, conn, tid, fid):
        self._conn = conn
        self._tid = tid
        self._fid = fid

    def read(self, offset: int, length: int) -> bytes:
        return self._conn.readFile(self._tid, self._fid, offset, length, singleCall=False) or b""

    def close(self) -> None:
        try:
            self._conn.closeFile(self._tid, self._fid)
        finally:
            self._conn.disconnectTree(self._tid)


class ImpacketBackend:
    def __init__(self, conn):
        self._conn = conn

    @classmethod
    def connect(cls, auth: SmbAuth) -> "ImpacketBackend":
        from impacket.smbconnection import SMBConnection

        conn = SMBConnection(auth.remote_name, auth.remote_host, sess_port=auth.port)
        if auth.use_kerberos:
            conn.kerberosLogin(**smb_kerberos_kwargs(auth))
        else:
            conn.login(**smb_login_kwargs(auth))
        return cls(conn)

    @staticmethod
    def _split(path: str) -> tuple[str, str]:
        """Return ``(share, subpath)`` from a unified path; subpath is '/'-joined."""
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        if not parts:
            return "", ""
        return parts[0], "/".join(parts[1:])

    def roots(self) -> list[DirEntry]:
        out = []
        for share in self._conn.listShares():
            name = share["shi1_netname"].rstrip("\x00")
            out.append(DirEntry(name=name, is_dir=True))
        return out

    def list(self, path: str) -> list[DirEntry]:
        share, sub = self._split(path)
        if not share:
            return self.roots()
        glob = (sub.replace("/", "\\") + "\\*") if sub else "*"
        out = []
        for f in self._conn.listPath(share, glob):
            name = f.get_longname()
            if name in (".", ".."):
                continue
            out.append(
                DirEntry(
                    name=name,
                    is_dir=f.is_directory() != 0,
                    size=f.get_filesize(),
                    mtime=float(f.get_mtime_epoch() or 0),
                )
            )
        return out

    def stat(self, path: str) -> DirEntry:
        share, sub = self._split(path)
        if not sub:
            return DirEntry(name=share, is_dir=True)
        f = self._conn.listPath(share, sub.replace("/", "\\"))[0]
        return DirEntry(
            name=f.get_longname(),
            is_dir=f.is_directory() != 0,
            size=f.get_filesize(),
            mtime=float(f.get_mtime_epoch() or 0),
        )

    def open_read(self, path: str, offset: int = 0, chunk: int = 65536) -> Iterator[bytes]:
        share, sub = self._split(path)
        smb_path = sub.replace("/", "\\")
        tid = self._conn.connectTree(share)
        fid = self._conn.openFile(tid, smb_path)
        try:
            pos = offset
            while True:
                data = self._conn.readFile(tid, fid, pos, chunk, singleCall=True)
                if not data:
                    break
                yield data
                pos += len(data)
        finally:
            self._conn.closeFile(tid, fid)
            self._conn.disconnectTree(tid)

    def open_file(self, path: str) -> _SmbFile:
        share, sub = self._split(path)
        tid = self._conn.connectTree(share)
        fid = self._conn.openFile(tid, sub.replace("/", "\\"))
        return _SmbFile(self._conn, tid, fid)

    def close(self) -> None:
        for teardown in (self._conn.logoff, self._conn.close):
            try:
                teardown()
            except Exception:
                pass
