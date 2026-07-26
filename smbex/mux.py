"""Ride an existing OpenSSH ControlMaster socket instead of authenticating.

paramiko cannot speak to a control socket — that socket carries OpenSSH's private
*multiplexing* protocol (``mux.c``), not SSH2 — so this backend drives the system
``ssh`` client instead: ``ssh -s sftp`` over the ControlPath opens the SFTP subsystem
on a multiplexed session, and paramiko's ``SFTPClient`` speaks SFTP to that
subprocess over its stdio pipes (:class:`PipeChannel`). Everything above SFTP — the
whole :class:`~smbex.backend.ssh_backend.SshBackend` list/stat/read surface — then
works unchanged, which is why :class:`MuxBackend` subclasses it.

Consequences of holding no credentials (only the socket path):
  * We never re-authenticate. To guarantee we *ride* the session and never silently
    open a fresh login, every slave is gated on ``ssh -O check`` (the master must be
    alive) and started with no key material + ``BatchMode=yes`` + ``-F /dev/null`` so
    OpenSSH's direct-connect fallback cannot authenticate on its own.
  * ``reconnect`` can only succeed while a live master remains — or is re-established
    by the operator — at the *same* socket path. That is the whole story behind the
    'r' key here: re-check, re-spawn.

Discovery (``discover_masters``) scans the conventional control-socket directories,
keeps only sockets we own, and probes each with ``-O check`` — concurrently and with
a short timeout, because ``-O check`` against a non-mux socket blocks until it times
out. Connection info is best-effort: the ControlPath filename if it embeds
``user@host`` (``%r@%h:%p``), else the master's own argv via ``/proc/<pid>/cmdline``
(Linux), else just the socket path.
"""

from __future__ import annotations

import os
import re
import stat as statmod
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from smbex.backend.ssh_backend import SshBackend

# A placeholder destination for the ssh command line. When the ControlPath points at
# a live master these connection args are vestigial (the master already holds the
# connection); a bogus host is accepted. Verified against a live master.
_DUMMY_HOST = "smbex-mux"

# ssh options every mux invocation shares: ignore the user's ssh_config (so a stray
# Host block can't redirect us), never prompt, don't try to become a master, and
# ProxyCommand=false so that if we ever reach OpenSSH's direct-connect fallback (no
# live master) it aborts *locally* instead of dialing the remote — no new login, not
# even a DNS lookup, leaves the box. Inert while a master is alive: multiplexing skips
# connection setup, so ProxyCommand is never consulted.
_SSH_COMMON = [
    "-F", "/dev/null",
    "-o", "BatchMode=yes",
    "-o", "ControlMaster=no",
    "-o", "ProxyCommand=false",
]


# --- pipe -> paramiko "channel" adapter -------------------------------------
class PipeChannel:
    """Adapts a ``ssh -s sftp`` subprocess's stdio to the tiny surface paramiko's
    ``SFTPClient`` uses on its socket: ``send`` / ``recv`` / ``get_name`` / ``close``.

    Close via the ``Popen`` file objects, never ``os.close`` on their fds, so we
    don't double-close and clobber a later process's recycled fd numbers.
    """

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc

    def send(self, data: bytes) -> int:
        n = self.proc.stdin.write(data)  # bufsize=0 -> raw FileIO; may write < len
        return n if n is not None else len(data)

    def recv(self, n: int) -> bytes:
        return self.proc.stdout.read(n) or b""  # one syscall, <= n; b"" at EOF

    def get_name(self) -> str:  # paramiko logs this even for a non-Channel sock
        return "mux"

    def settimeout(self, _timeout) -> None:  # paramiko may call it; blocking pipes
        pass

    def close(self) -> None:
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                f.close()
            except Exception:
                pass
        try:
            self.proc.terminate()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=2)
        except Exception:
            pass


# --- master liveness + connection info --------------------------------------
def master_check(socket_path: str, timeout: float = 5.0) -> tuple[bool, int | None, str]:
    """``ssh -O check`` the socket. Returns ``(alive, master_pid, raw_output)``.

    A live master answers instantly; a non-mux socket blocks, so ``timeout`` caps the
    wait (``subprocess.run`` kills the probe on timeout). A missing ``ssh`` binary or
    a stale/dead socket returns ``alive=False`` rather than raising.
    """
    try:
        r = subprocess.run(
            ["ssh", *_SSH_COMMON, "-O", "check", "-S", socket_path, _DUMMY_HOST],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, None, "check timed out (not an SSH master socket?)"
    except OSError as exc:
        return False, None, f"ssh unavailable: {exc}"
    out = (r.stderr + r.stdout).strip()
    m = re.search(r"pid=(\d+)", out)
    return r.returncode == 0, (int(m.group(1)) if m else None), out


# ssh options that take a following argument (so we skip that argument when scanning
# a master's argv for the destination). Boolean flags (-M -N -f -q -v -A …) are the rest.
_OPTS_WITH_ARG = set("bcDEeFIiJLlmOopQRSWw")


def _ssh_destination(argv: list[str]) -> str | None:
    """Best-effort: pull the ``[user@]host`` destination out of an ``ssh`` argv."""
    it = iter(argv[1:])  # skip argv[0] == "ssh"
    for tok in it:
        if tok.startswith("--"):
            continue
        if tok.startswith("-") and len(tok) >= 2:
            if tok[1] in _OPTS_WITH_ARG and len(tok) == 2:
                next(it, None)  # consume its separate argument
            continue  # bundled/attached or boolean flag
        return tok[len("ssh://"):] if tok.startswith("ssh://") else tok
    return None


def _dest_from_pid(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None  # not Linux, or the master is gone
    argv = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
    return _ssh_destination(argv) if argv else None


def describe_master(socket_path: str, pid: int | None) -> str:
    """A human label for the picker: the master's own destination via ``/proc`` argv
    (Linux, most accurate) -> a ``user@host`` embedded in the ControlPath filename
    (``%r@%h:%p``) -> the socket's basename."""
    if pid is not None:
        dest = _dest_from_pid(pid)
        if dest:
            return dest
    base = os.path.basename(socket_path.rstrip("/")) or socket_path
    m = re.search(r"[A-Za-z0-9._-]+@[A-Za-z0-9.:_-]+", base)  # ControlPath %r@%h:%p
    return m.group(0) if m else base


@dataclass(frozen=True)
class MasterInfo:
    """A live ControlMaster socket found by :func:`discover_masters`."""

    path: str
    pid: int | None
    label: str

    @property
    def host(self) -> str:
        """Best-effort hostname (for the download mirror dir); '' if unknown."""
        return self.label.split("@")[-1].split(":")[0]


def default_socket_dirs() -> list[Path]:
    """Conventional ControlPath directories. Deliberately SSH-purpose dirs only —
    scanning e.g. ``$XDG_RUNTIME_DIR`` risks probing many non-mux sockets, each of
    which makes ``-O check`` block; point ``--mux`` there explicitly if you need to."""
    home = Path.home()
    return [
        home / ".ssh",
        home / ".ssh" / "sockets",
        home / ".ssh" / "controlmasters",
        home / ".ansible" / "cp",
    ]


def _is_own_socket(p: Path) -> bool:
    try:
        st = p.lstat()
    except OSError:
        return False
    return statmod.S_ISSOCK(st.st_mode) and st.st_uid == os.getuid()


def discover_masters(
    dirs: list[Path] | None = None, timeout: float = 2.0
) -> list[MasterInfo]:
    """Scan ``dirs`` (default :func:`default_socket_dirs`) for our own sockets and
    return those that are live SSH masters. Probes run concurrently with a short
    timeout so a directory full of foreign sockets can't stall discovery."""
    candidates: list[str] = []
    seen: set[str] = set()
    for d in dirs if dirs is not None else default_socket_dirs():
        try:
            entries = sorted(Path(d).iterdir())
        except OSError:
            continue  # missing/unreadable dir
        for p in entries:
            rp = str(p)
            if rp not in seen and _is_own_socket(p):
                seen.add(rp)
                candidates.append(rp)
    if not candidates:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as ex:
        checked = list(ex.map(lambda c: (c, master_check(c, timeout)), candidates))
    return [
        MasterInfo(rp, pid, describe_master(rp, pid))
        for rp, (alive, pid, _out) in checked
        if alive
    ]


def resolve_socket_or_dirs(arg: str) -> tuple[str | None, list[Path]]:
    """Interpret the ``--mux`` value.

    * empty -> ``(None, default dirs)``: scan the conventional dirs, then pick.
    * a socket file -> ``(path, [])``: connect directly, no picker.
    * a directory -> ``(None, [dir])``: scan just that dir, then pick.
    * anything else -> ``ValueError``.
    """
    if not arg:
        return None, default_socket_dirs()
    p = Path(arg).expanduser()
    try:
        if statmod.S_ISSOCK(p.lstat().st_mode):
            return str(p), []
    except OSError:
        pass
    if p.is_dir():
        return None, [p]
    raise ValueError(f"{arg!r} is neither a directory nor a socket file")


# --- the backend ------------------------------------------------------------
def _open_mux_sftp(socket_path: str, destination: str = _DUMMY_HOST):
    """Gate on a live master, then open SFTP over ``ssh -s sftp`` on the control
    socket. Returns ``(SFTPClient, Popen)``. Raises ``ConnectionError`` if no master."""
    import paramiko

    alive, _pid, info = master_check(socket_path)
    if not alive:
        raise ConnectionError(f"no live SSH master at {socket_path}: {info}")
    proc = subprocess.Popen(
        ["ssh", *_SSH_COMMON, "-o", f"ControlPath={socket_path}", destination, "-s", "sftp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,  # ssh diagnostics (host-key/PQ warnings) are noise
        bufsize=0,
    )
    try:
        return paramiko.SFTPClient(PipeChannel(proc)), proc
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        raise


class MuxBackend(SshBackend):
    """An :class:`SshBackend` whose SFTP transport rides an existing ControlMaster
    socket (see the module docstring). Holds no credentials; reconnect re-checks and
    re-spawns the slave against the same socket path."""

    def __init__(
        self,
        sftp,
        proc: subprocess.Popen,
        socket_path: str,
        start_rel: str = "",
        destination: str = _DUMMY_HOST,
    ):
        super().__init__(sftp, client=None, start_rel=start_rel, auth=None)
        self._proc = proc
        self._socket = socket_path
        self._destination = destination
        self.label = socket_path
        self.host = ""

    @classmethod
    def connect_socket(
        cls, socket_path: str, start_path: str = "", destination: str = _DUMMY_HOST
    ) -> "MuxBackend":
        sftp, proc = _open_mux_sftp(socket_path, destination)
        start = sftp.normalize(start_path or ".")  # resolve the login dir like SSH
        be = cls(sftp, proc, socket_path, start_rel=start.lstrip("/"), destination=destination)
        _alive, pid, _ = master_check(socket_path)
        be.label = describe_master(socket_path, pid)
        be.host = be.label.split("@")[-1].split(":")[0] or "mux"
        return be

    def reconnect(self) -> None:
        try:
            self._sftp.close()  # closes the pipe -> terminates the old slave too
        except Exception:
            pass
        self._sftp, self._proc = _open_mux_sftp(self._socket, self._destination)

    def close(self) -> None:
        try:
            self._sftp.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
        except Exception:
            pass
