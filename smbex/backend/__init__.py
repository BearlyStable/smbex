"""Protocol backends (SMB, SSH) behind a common interface."""

from smbex.backend.base import Backend, DirEntry

__all__ = ["Backend", "DirEntry"]
