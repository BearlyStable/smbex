"""SshBackend logic (path mapping, listing, seekable reads) against a fake SFTP
client — fast and offline. Real paramiko wire is covered by the integration test."""

from __future__ import annotations

import io
import stat as st

from smbex.backend.ssh_backend import SshBackend

TREE = {
    "home": {"user": {"a.txt": b"hi", "sub": {"b.bin": b"xx"}}},
    "etc": {"passwd": b"root:x:0:0"},
}


class _Attr:
    def __init__(self, mode: int, size: int, mtime: int, filename: str = ""):
        self.st_mode = mode
        self.st_size = size
        self.st_mtime = mtime
        self.filename = filename


class FakeSFTP:
    """Minimal stand-in for paramiko.SFTPClient over an in-memory tree."""

    def __init__(self, tree: dict):
        self.tree = tree

    def _resolve(self, server_path: str):
        node = self.tree
        for part in [p for p in server_path.strip("/").split("/") if p]:
            node = node[part]
        return node

    def listdir_attr(self, server_path: str):
        node = self._resolve(server_path)
        out = []
        for name, child in node.items():
            if isinstance(child, dict):
                out.append(_Attr(st.S_IFDIR | 0o755, 0, 0, name))
            else:
                out.append(_Attr(st.S_IFREG | 0o644, len(child), 0, name))
        return out

    def stat(self, server_path: str):
        node = self._resolve(server_path)
        if isinstance(node, dict):
            return _Attr(st.S_IFDIR | 0o755, 0, 0)
        return _Attr(st.S_IFREG | 0o644, len(node), 0)

    def open(self, server_path: str, mode: str = "rb"):
        return io.BytesIO(self._resolve(server_path))


def test_list_root_maps_to_filesystem_root():
    be = SshBackend(FakeSFTP(TREE))
    assert sorted(e.name for e in be.list("")) == ["etc", "home"]


def test_list_subdir_types_and_sizes():
    be = SshBackend(FakeSFTP(TREE))
    entries = {e.name: e for e in be.list("home/user")}
    assert entries["a.txt"].is_dir is False and entries["a.txt"].size == 2
    assert entries["sub"].is_dir is True


def test_read_with_offset():
    be = SshBackend(FakeSFTP(TREE))
    assert b"".join(be.open_read("etc/passwd")) == b"root:x:0:0"
    assert b"".join(be.open_read("etc/passwd", offset=5)) == b"x:0:0"


def test_stat_file_and_dir():
    be = SshBackend(FakeSFTP(TREE))
    assert be.stat("home/user/a.txt").size == 2
    assert be.stat("home").is_dir
