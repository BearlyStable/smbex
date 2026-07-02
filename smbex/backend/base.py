"""The protocol-generic backend interface.

Every backend exposes the same small surface over a single POSIX-style path
("/" separated). For SMB the first path component is the share name; for SSH it
is an absolute filesystem path. ``roots()`` returns the top level to start from
(SMB shares, or the SSH start directory).

Backends are synchronous and blocking on purpose — the gateway runs them off the
event loop via ``asyncio.to_thread``. A single backend/connection instance is not
safe for concurrent use; the gateway serializes access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class DirEntry:
    name: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0


@runtime_checkable
class Backend(Protocol):
    def roots(self) -> list[DirEntry]:
        """Top-level entries: SMB shares, or the SSH start directory listing."""
        ...

    def list(self, path: str) -> list[DirEntry]:
        """List a directory. Empty path lists the roots."""
        ...

    def stat(self, path: str) -> DirEntry:
        """Metadata for a single path."""
        ...

    def open_read(self, path: str, offset: int = 0) -> Iterator[bytes]:
        """Yield the file's bytes from ``offset`` in chunks (for streaming reads)."""
        ...

    def close(self) -> None:
        """Tear down the connection."""
        ...
