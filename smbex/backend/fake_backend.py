"""In-memory backend for tests — no network required.

Tree format: a nested dict where a ``dict`` value is a directory and a ``bytes``
value is a file, e.g.::

    {"C$": {"readme.txt": b"hi", "sub": {"a.bin": b"..."}}}

Also records ``list()`` call order and supports optional per-path gates so tests
can deterministically exercise the gateway's priority scheduling.
"""

from __future__ import annotations

import threading
from typing import Iterator

from smbex.backend.base import DirEntry

Node = "dict | bytes"


class _FakeRemoteFile:
    def __init__(self, backend: "FakeBackend", path: str, data: bytes):
        self._backend = backend
        self._path = path
        self._data = data

    def read(self, offset: int, length: int) -> bytes:
        self._backend.events.append(f"read-start:{self._path}@{offset}")
        gate = self._backend.read_gates.get(self._path)
        if gate is not None:  # let a test hold a chunk to exercise preemption
            gate.wait()
        self._backend.events.append(f"read:{self._path}@{offset}")
        return self._data[offset : offset + length]

    def close(self) -> None:
        self._backend.events.append(f"close:{self._path}")


class FakeBackend:
    def __init__(self, tree: dict, mtimes: dict[str, float] | None = None):
        self._tree = tree
        self._mtimes = mtimes or {}  # full slash-path -> mtime epoch (default 0)
        # Test instrumentation:
        self.list_calls: list[str] = []
        self.exec_order: list[str] = []
        self.gates: dict[str, threading.Event] = {}
        # Cross-operation event log + per-path read gates, for the throttle test.
        self.events: list[str] = []
        self.read_gates: dict[str, threading.Event] = {}
        self.closed = False
        # Reconnect simulation: drop_next ops raise ConnectionError; reconnect_fails
        # reconnect attempts raise; reconnects counts successful reconnects.
        self.drop_next = 0
        self.reconnect_fails = 0
        self.reconnects = 0

    def _maybe_drop(self, where: str) -> None:
        if self.drop_next > 0:
            self.drop_next -= 1
            self.events.append(f"drop:{where}")
            raise ConnectionError(f"simulated connection drop during {where}")

    def is_connection_error(self, exc: BaseException) -> bool:
        return isinstance(exc, ConnectionError)

    def reconnect(self) -> None:
        if self.reconnect_fails > 0:
            self.reconnect_fails -= 1
            raise ConnectionError("simulated reconnect failure")
        self.reconnects += 1
        self.events.append("reconnect")

    @staticmethod
    def _parts(path: str) -> list[str]:
        return [p for p in path.replace("\\", "/").split("/") if p]

    def _resolve(self, path: str):
        node = self._tree
        for part in self._parts(path):
            if not isinstance(node, dict) or part not in node:
                raise FileNotFoundError(path)
            node = node[part]
        return node

    def _entry(self, name: str, node, full: str) -> DirEntry:
        is_dir = isinstance(node, dict)
        return DirEntry(
            name=name,
            is_dir=is_dir,
            size=0 if is_dir else len(node),
            mtime=self._mtimes.get(full, 0.0),
        )

    def roots(self) -> list[DirEntry]:
        return [self._entry(name, node, name) for name, node in self._tree.items()]

    def list(self, path: str) -> list[DirEntry]:
        self.list_calls.append(path)
        gate = self.gates.get(path)
        if gate is not None:  # block until the test releases us
            gate.wait()
        self._maybe_drop(f"list:{path}")
        self.exec_order.append(path)
        self.events.append(f"list:{path}")
        node = self._resolve(path)
        if not isinstance(node, dict):
            raise NotADirectoryError(path)
        return [
            self._entry(name, child, f"{path}/{name}" if path else name)
            for name, child in node.items()
        ]

    def stat(self, path: str) -> DirEntry:
        node = self._resolve(path)
        parts = self._parts(path)
        return self._entry(parts[-1] if parts else "", node, path)

    def open_read(self, path: str, offset: int = 0, chunk: int = 65536) -> Iterator[bytes]:
        node = self._resolve(path)
        if isinstance(node, dict):
            raise IsADirectoryError(path)
        self.events.append(f"read-start:{path}@{offset}")
        gate = self.read_gates.get(path)
        if gate is not None:  # block until the test releases this read
            gate.wait()
        self.events.append(f"read:{path}@{offset}")
        data = node[offset:]
        for i in range(0, len(data), chunk) or [0]:
            yield data[i : i + chunk]

    def open_file(self, path: str) -> "_FakeRemoteFile":
        node = self._resolve(path)
        if isinstance(node, dict):
            raise IsADirectoryError(path)
        self.events.append(f"open:{path}")
        return _FakeRemoteFile(self, path, node)

    def close(self) -> None:
        self.closed = True
