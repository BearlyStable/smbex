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
class RemoteFile(Protocol):
    """An opened remote file for repeated ranged reads (used by downloads)."""

    def read(self, offset: int, length: int) -> bytes:
        ...

    def close(self) -> None:
        ...


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

    def open_file(self, path: str) -> "RemoteFile":
        """Open a remote file once for many ranged reads. Downloads keep this open
        across chunks instead of reopening per chunk (fewer PDUs / audit events)."""
        ...

    def reconnect(self) -> None:
        """Re-establish the connection from stored credentials. Raises on failure.

        The gateway calls this when a job fails with a connection-class error, then
        retries the job once on the fresh connection. Open file handles from the old
        connection do not survive."""
        ...

    def is_connection_error(self, exc: BaseException) -> bool:
        """True if ``exc`` means the link is lost (so ``reconnect`` may help), vs a
        normal operational error like "not found" (which should just propagate)."""
        ...

    def close(self) -> None:
        """Tear down the connection."""
        ...
