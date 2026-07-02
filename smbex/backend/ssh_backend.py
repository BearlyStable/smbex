"""SSH backend built on paramiko's SFTP client.

Paths are unified POSIX-style and rooted at the server's filesystem root: unified
"" is server "/", unified "etc/passwd" is "/etc/passwd". The connect flow resolves
the requested start directory (default: the login home) and exposes it as
``start_rel`` so the UI can open there while still allowing navigation up to "/".

paramiko is blocking — always drive this through the gateway. SFTP (not raw scp)
is used because it supports listing, stat, and seekable reads, which the browser
and resumable downloads need; plain file transfer (scp semantics) is a subset.
"""

from __future__ import annotations

import stat as statmod
from typing import Iterator

from smbex.auth import SshAuth
from smbex.backend.base import DirEntry


class _SftpFile:
    """An open SFTP file: one OPEN, then many seek+read ranges over the channel."""

    def __init__(self, handle):
        self._handle = handle

    def read(self, offset: int, length: int) -> bytes:
        self._handle.seek(offset)
        return self._handle.read(length) or b""

    def close(self) -> None:
        self._handle.close()


class SshBackend:
    def __init__(self, sftp, client=None, start_rel: str = ""):
        self._sftp = sftp
        self._client = client
        self.start_rel = start_rel

    @classmethod
    def connect(cls, auth: SshAuth) -> "SshBackend":
        import getpass

        import paramiko

        client = paramiko.SSHClient()
        policy = auth.known_hosts_policy
        if policy == "strict":
            client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        elif policy == "ignore":
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:  # "auto" (TOFU): verify known hosts, accept & remember unknowns in-memory
            try:
                client.load_system_host_keys()
            except Exception:
                pass
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kwargs = dict(
            hostname=auth.host,
            port=auth.port,
            username=auth.username or getpass.getuser(),
            allow_agent=auth.use_agent,
            look_for_keys=auth.use_agent,
        )
        if auth.password:
            kwargs["password"] = auth.password
        if auth.key_filename:
            kwargs["key_filename"] = auth.key_filename

        client.connect(**kwargs)
        sftp = client.open_sftp()
        start = sftp.normalize(auth.start_path or ".")
        return cls(sftp, client, start_rel=start.lstrip("/"))

    @staticmethod
    def _server_path(path: str) -> str:
        return "/" + path.replace("\\", "/").strip("/")  # "" -> "/"

    def _to_entry(self, name: str, attr, parent_server_path: str) -> DirEntry:
        mode = attr.st_mode or 0
        is_dir = statmod.S_ISDIR(mode)
        if statmod.S_ISLNK(mode):  # resolve symlinks so dir-links are browsable
            try:
                target = self._sftp.stat(parent_server_path.rstrip("/") + "/" + name)
                is_dir = statmod.S_ISDIR(target.st_mode or 0)
            except OSError:
                is_dir = False
        return DirEntry(
            name=name,
            is_dir=is_dir,
            size=attr.st_size or 0,
            mtime=float(attr.st_mtime or 0),
        )

    def roots(self) -> list[DirEntry]:
        return self.list("")

    def list(self, path: str) -> list[DirEntry]:
        server_path = self._server_path(path)
        return [
            self._to_entry(attr.filename, attr, server_path)
            for attr in self._sftp.listdir_attr(server_path)
        ]

    def stat(self, path: str) -> DirEntry:
        server_path = self._server_path(path)
        attr = self._sftp.stat(server_path)
        trimmed = path.strip("/")
        name = trimmed.rsplit("/", 1)[-1] if trimmed else "/"
        return DirEntry(
            name=name,
            is_dir=statmod.S_ISDIR(attr.st_mode or 0),
            size=attr.st_size or 0,
            mtime=float(attr.st_mtime or 0),
        )

    def open_read(self, path: str, offset: int = 0, chunk: int = 65536) -> Iterator[bytes]:
        handle = self._sftp.open(self._server_path(path), "rb")
        try:
            if offset:
                handle.seek(offset)
            while True:
                data = handle.read(chunk)
                if not data:
                    break
                yield data
        finally:
            handle.close()

    def open_file(self, path: str) -> _SftpFile:
        return _SftpFile(self._sftp.open(self._server_path(path), "rb"))

    def close(self) -> None:
        for obj in (self._sftp, self._client):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
