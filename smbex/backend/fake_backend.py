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


class FakeBackend:
    def __init__(self, tree: dict):
        self._tree = tree
        # Test instrumentation:
        self.list_calls: list[str] = []
        self.exec_order: list[str] = []
        self.gates: dict[str, threading.Event] = {}
        self.closed = False

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

    @staticmethod
    def _entry(name: str, node) -> DirEntry:
        is_dir = isinstance(node, dict)
        return DirEntry(name=name, is_dir=is_dir, size=0 if is_dir else len(node))

    def roots(self) -> list[DirEntry]:
        return [self._entry(name, node) for name, node in self._tree.items()]

    def list(self, path: str) -> list[DirEntry]:
        self.list_calls.append(path)
        gate = self.gates.get(path)
        if gate is not None:  # block until the test releases us
            gate.wait()
        self.exec_order.append(path)
        node = self._resolve(path)
        if not isinstance(node, dict):
            raise NotADirectoryError(path)
        return [self._entry(name, child) for name, child in node.items()]

    def stat(self, path: str) -> DirEntry:
        node = self._resolve(path)
        parts = self._parts(path)
        return self._entry(parts[-1] if parts else "", node)

    def open_read(self, path: str, offset: int = 0, chunk: int = 65536) -> Iterator[bytes]:
        node = self._resolve(path)
        if isinstance(node, dict):
            raise IsADirectoryError(path)
        data = node[offset:]
        for i in range(0, len(data), chunk) or [0]:
            yield data[i : i + chunk]

    def close(self) -> None:
        self.closed = True
