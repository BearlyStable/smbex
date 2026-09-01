"""Background download manager.

Downloads run on an asyncio worker, one file at a time, streaming each file in
chunks through the gateway at DOWNLOAD priority. Because every chunk is a separate
low-priority gateway job, a queued browse request is served between chunks —
browsing stays responsive and downloads take only the leftover bandwidth.

Two local layouts (``flat=False|True``): the remote tree is **mirrored** under
``root`` (``root / share / dir / file``), or **flattened** so every file lands
directly in ``root`` with its remote path folded into the name
(``share/2024/report.pdf`` -> ``share_2024_report.pdf``) — one folder per host, no
digging, and the origin still readable. Existing-file policy (default ``resume``): continue a partial file from where it
stopped, and skip files already fully present; ``overwrite`` re-fetches; ``skip``
never touches an existing path.

Resume is also what makes a transfer **interruptible**: because every chunk is a
separate job and the partial file is on disk, a running download can be cancelled
(:meth:`cancel`) or pushed down the queue (:meth:`reorder`) *between chunks* — it
stops, lets a smaller file behind it have the wire, and picks up from the bytes
already written when its turn comes round again.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from smbex.gateway import Gateway, Priority

CHUNK = 256 * 1024

#: Joins the remote path's components in a flat filename.
FLAT_SEP = "_"
#: Marks a de-duplication counter — deliberately *not* FLAT_SEP, so "a~2.txt" can
#: never be confused with a real remote component named "2".
DEDUPE_SEP = "~"
#: Path separators, control characters and the chars that aren't portable in a
#: filename (Windows/SMB/exFAT); everything else, incl. non-ASCII, is kept as-is.
_UNSAFE = re.compile(r'[\x00-\x1f<>:"|?*\\/]')


def _fit(name: str, max_bytes: int) -> str:
    """Truncate to ``max_bytes`` *bytes* (not characters — a filesystem limit is in
    bytes and CJK names are 3 bytes a character), keeping the extension."""
    if len(name.encode()) <= max_bytes:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or len(ext) > 16:  # no usable extension: truncate the whole name
        stem, dot, ext = name, "", ""
    keep = max(1, max_bytes - len(f"{dot}{ext}".encode()))
    stem = stem.encode()[:keep].decode(errors="ignore")  # drop a split character
    return f"{stem}{dot}{ext}"


def flat_name(remote_path: str, *, max_bytes: int = 200) -> str:
    """The single filename encoding a whole remote path, for the flat layout.

    ``share/2024/report.pdf`` -> ``share_2024_report.pdf``. Left under a 255-byte
    filesystem limit with room for a ``~N`` de-duplication suffix.
    """
    parts = [p for p in remote_path.replace("\\", "/").split("/") if p and p != ".."]
    name = FLAT_SEP.join(_UNSAFE.sub("_", part) for part in parts)
    return _fit(name or "download", max_bytes)


@dataclass
class DownloadItem:
    remote_path: str
    local_path: Path
    size: int = 0
    downloaded: int = 0
    status: str = "queued"  # queued | running | done | skipped | error | cancelled
    error: str = ""
    #: Set by cancel()/reorder() on a *running* transfer; the chunk loop sees it
    #: between chunks and stops there ("cancel" -> give up, "yield" -> requeue).
    control: str = ""

    @property
    def progress(self) -> float:
        if self.status in ("done", "skipped"):
            return 1.0
        if self.size <= 0:
            return 0.0
        return min(self.downloaded / self.size, 1.0)


class DownloadManager:
    def __init__(
        self,
        gateway: Gateway,
        root: Path | str,
        *,
        exists_policy: str = "resume",
        flat: bool = False,
        on_change: Callable[[], None] | None = None,
    ):
        self.gateway = gateway
        self.root = Path(root)
        self.exists_policy = exists_policy
        self.flat = flat
        self.on_change = on_change
        # Flat layout only: local path <-> remote path, so a name is assigned once
        # (stable for resume and for the preview's "is this downloaded?" lookup) and
        # two different remote paths can never land on the same file.
        self._assigned: dict[str, Path] = {}
        self._claimed: dict[Path, str] = {}
        self.items: list[DownloadItem] = []
        # The queue *is* ``items`` — the worker takes the first still-queued entry, so
        # reordering the list reprioritizes the transfers (see :meth:`reorder`).
        self._wake = asyncio.Event()  # set when work arrives
        self._idle = asyncio.Event()  # set while nothing is queued or running
        self._idle.set()
        self._worker: asyncio.Task | None = None

    # --- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    async def join(self) -> None:
        """Wait until every queued download has been processed."""
        while self.pending:
            self._idle.clear()
            await self._idle.wait()

    # --- queue state ----------------------------------------------------------
    @property
    def pending(self) -> list[DownloadItem]:
        """Transfers still to do, in queue order: the running one first (unless it has
        just been displaced and is about to yield), then the queued ones."""
        return [it for it in self.items if it.status in ("running", "queued")]

    def _next_queued(self) -> DownloadItem | None:
        for item in self.items:
            if item.status == "queued":
                return item
        return None

    @property
    def can_interrupt(self) -> bool:
        """Whether a *running* transfer can be stopped early to any benefit.

        False on FTP, where the rest of the data connection has to be drained anyway
        (see ``Backend.interruptible``): stopping would cost the same as finishing,
        minus the file. Queued transfers can always be cancelled or reordered — those
        never touched the wire.
        """
        return self.gateway is None or self.gateway.interruptible

    def reorder(self, item: DownloadItem, delta: int) -> str:
        """Move a pending transfer one place up (-1) or down (+1) the queue.

        The worker takes the first entry still ``queued``, so swapping two pending
        entries is what changes priority. **Crossing the running transfer preempts
        it**: the wire belongs to the first pending entry, so a running one that ends
        up behind another is asked to yield — it stops at the next chunk boundary,
        goes back to ``queued`` in its new place, and resumes from the bytes already
        on disk when it comes round again. That is the "a 10 MB file is hogging the
        link but I want the 5 KB text file now" case, from either side: push the big
        one down, or pull the small one up.

        Finished entries are skipped over rather than swapped with. Returns what
        happened: "moved", "preempted" (the running transfer was asked to yield),
        "blocked" (nothing to swap with) or "uninterruptible" (this protocol can't
        stop a running transfer to any benefit — see :attr:`can_interrupt`).
        """
        if item.status not in ("running", "queued") or not delta:
            return "blocked"
        pending = [i for i, it in enumerate(self.items) if it.status in ("running", "queued")]
        try:
            pos = pending.index(self.items.index(item))
        except ValueError:
            return "blocked"
        target = pos + (1 if delta > 0 else -1)
        if not 0 <= target < len(pending):
            return "blocked"
        here, there = pending[pos], pending[target]
        if not self.can_interrupt and "running" in (item.status, self.items[there].status):
            return "uninterruptible"  # the swap would displace a transfer we can't stop
        already_yielding = {id(it) for it in self.items if it.control == "yield"}
        self.items[here], self.items[there] = self.items[there], self.items[here]
        self._preempt_if_displaced()
        self._notify()
        preempted = any(
            it.control == "yield" and id(it) not in already_yielding for it in self.items
        )
        return "preempted" if preempted else "moved"

    def _preempt_if_displaced(self) -> None:
        """The wire belongs to the first pending entry: if the running transfer is no
        longer it, ask it to yield (stop between chunks and requeue where it now is)."""
        pending = self.pending
        if not pending or pending[0].status == "running":
            return
        for item in pending[1:]:
            if item.status == "running":
                item.control = "yield"

    def cancel(self, item: DownloadItem) -> str:
        """Cancel a transfer, or clear an entry that has already finished.

        Returns what happened: "cancelled" (a queued one, dropped before it starts),
        "stopping" (a running one — it stops at the next chunk boundary), "cleared"
        (a finished/errored/cancelled entry, removed from the list) or
        "uninterruptible" (a running one on a protocol that can't stop early — see
        :attr:`can_interrupt`; the bytes arrive regardless, so cancelling would only
        lose the file). A cancelled transfer keeps whatever it has already written,
        so re-grabbing it resumes rather than starting over.
        """
        if item.status == "queued":
            item.status = "cancelled"
            self._notify()
            return "cancelled"
        if item.status == "running":
            if not self.can_interrupt:
                return "uninterruptible"
            item.control = "cancel"
            self._notify()
            return "stopping"
        if item in self.items:
            self.items.remove(item)
            self._notify()
        return "cleared"

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change()

    # --- enqueueing -----------------------------------------------------------
    def _local_for(self, remote_path: str) -> Path:
        """Where ``remote_path`` is stored locally — mirrored, or flattened into one
        per-host folder. Deterministic per remote path, so re-grabbing a file resumes
        it instead of writing a second copy."""
        if not self.flat:
            parts = [p for p in remote_path.replace("\\", "/").split("/") if p]
            return self.root.joinpath(*parts)
        local = self._assigned.get(remote_path)
        if local is None:
            local = self._claim(self.root / flat_name(remote_path), remote_path)
            self._assigned[remote_path] = local
        return local

    def _claim(self, candidate: Path, remote_path: str) -> Path:
        """Reserve ``candidate`` for ``remote_path``, numbering on a clash.

        Only a clash with *another remote path* is numbered — an existing file on
        disk for the same remote path is the whole point of resume, so it is kept.
        Flattening makes clashes rare but possible ('a/b_c' and 'a_b/c' fold alike).
        """
        owner = self._claimed.get(candidate)
        if owner is None or owner == remote_path:
            self._claimed[candidate] = remote_path
            return candidate
        stem, dot, ext = candidate.name.rpartition(".")
        if not dot or len(ext) > 16:
            stem, dot, ext = candidate.name, "", ""
        for n in range(2, 10_000):
            numbered = candidate.with_name(f"{stem}{DEDUPE_SEP}{n}{dot}{ext}")
            if self._claimed.get(numbered) in (None, remote_path):
                self._claimed[numbered] = remote_path
                return numbered
        raise RuntimeError(f"cannot find a free local name for {remote_path}")

    async def add_file(self, remote_path: str, size: int = 0) -> DownloadItem:
        item = DownloadItem(remote_path, self._local_for(remote_path), size)
        self.items.append(item)
        self._idle.clear()
        self._wake.set()
        self._notify()
        return item

    async def add_files(self, files: Iterable[tuple[str, int]]) -> list[DownloadItem]:
        return [await self.add_file(rp, size) for rp, size in files]

    async def add_dir(self, remote_path: str, *, recursive: bool) -> list[DownloadItem]:
        return await self.add_files(await self._collect(remote_path, recursive))

    async def _collect(self, path: str, recursive: bool) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        try:
            entries = await self.gateway.list(path, priority=Priority.DOWNLOAD)
        except Exception:
            return out  # unreadable dir: skip rather than abort the whole job
        for entry in entries:
            child = f"{path}/{entry.name}" if path else entry.name
            if entry.is_dir:
                if recursive:
                    out.extend(await self._collect(child, recursive))
            else:
                out.append((child, entry.size))
        return out

    # --- worker ---------------------------------------------------------------
    async def _run(self) -> None:
        while True:
            item = self._next_queued()
            if item is None:  # drained: park until something is added
                self._idle.set()
                self._wake.clear()
                await self._wake.wait()
                continue
            try:
                await self._download(item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - record, keep the queue moving
                item.status, item.control = "error", ""
                item.error = str(exc)
            finally:
                self._notify()

    async def _download(self, item: DownloadItem) -> None:
        item.local_path.parent.mkdir(parents=True, exist_ok=True)
        existing = item.local_path.stat().st_size if item.local_path.exists() else 0

        if not item.size:
            try:
                item.size = (
                    await self.gateway.stat(item.remote_path, priority=Priority.DOWNLOAD)
                ).size
            except Exception:
                item.size = 0

        if existing:
            if self.exists_policy == "skip":
                item.downloaded, item.status = existing, "skipped"
                return
            if self.exists_policy == "overwrite":
                existing = 0
            elif self.exists_policy == "resume" and item.size and existing >= item.size:
                item.downloaded, item.status = existing, "skipped"
                return

        item.downloaded = existing
        item.status = "running"
        self._notify()

        mode = "ab" if existing else "wb"
        # Open the remote file once and read successive ranges — each range is its
        # own low-priority job (so browsing still preempts), but we don't reopen
        # per chunk. That keeps the wire/audit footprint like a normal client.
        remote = await self.gateway.open_file(item.remote_path, priority=Priority.DOWNLOAD)
        try:
            with open(item.local_path, mode) as local:
                offset = existing
                while True:
                    chunk = await self.gateway.read_file(
                        remote, offset, CHUNK, priority=Priority.DOWNLOAD
                    )
                    if not chunk:
                        break
                    local.write(chunk)
                    offset += len(chunk)
                    item.downloaded = offset
                    self._notify()
                    if item.size and offset >= item.size:
                        break
                    if item.control:  # cancelled or displaced: stop on this boundary
                        return self._interrupt(item)
        finally:
            # The handle is always closed, so the connection is never left mid-read.
            # (On FTP a handle closed before EOF drains the rest of the data
            # connection — see the backend — so an interrupted FTP transfer still
            # costs its remaining bytes. SMB/SFTP close immediately.)
            await self.gateway.close_file(remote, priority=Priority.DOWNLOAD)
        item.status, item.control = "done", ""  # a late cancel on a finished file is moot

    def _interrupt(self, item: DownloadItem) -> None:
        """Leave a running transfer at a chunk boundary — for good, or for now.

        Whatever has been written stays on disk, so "yield" resumes from there when
        the item's turn comes round again and "cancel" resumes if it is re-grabbed.
        """
        item.status = "cancelled" if item.control == "cancel" else "queued"
        item.control = ""
