"""Offline unit tests for the SSH ControlMaster (``--mux``) feature.

No ``ssh`` binary or paramiko needed: the mux protocol chain is exercised in
``test_backend_mux_integration.py`` (``@integration``). Here we test the pure-Python
pieces — argv/label parsing, path resolution, socket discovery (with a faked
``master_check``), and the pipe adapter (against a plain subprocess)."""

from __future__ import annotations

import socket
import subprocess
import sys

import pytest

from smbex import mux


# --- ssh argv -> destination -------------------------------------------------
@pytest.mark.parametrize(
    "argv, expected",
    [
        (["ssh", "-M", "-S", "/x", "-p", "22", "-i", "k", "tester@host", "-N"], "tester@host"),
        (["ssh", "-o", "ControlPath=/x", "ssh://u@h:2222"], "u@h:2222"),
        (["ssh", "-Nf", "-S", "/x", "myalias"], "myalias"),      # bundled boolean flags
        (["ssh", "-p2222", "host"], "host"),                      # attached option arg
        (["ssh", "-i", "/k", "-l", "root", "10.0.0.5"], "10.0.0.5"),
        (["ssh", "-M", "-S", "/only/a/socket", "-N"], None),      # no destination present
    ],
)
def test_ssh_destination(argv, expected):
    assert mux._ssh_destination(argv) == expected


# --- describe_master ---------------------------------------------------------
def test_describe_from_filename_when_no_pid():
    assert mux.describe_master("/home/u/.ssh/alice@srv.example:22", None) == "alice@srv.example:22"


def test_describe_falls_back_to_basename_for_hashed_path():
    # A %C-style opaque ControlPath and no pid -> just the filename.
    assert mux.describe_master("/home/u/.ssh/cm-9f8e7d6c", None) == "cm-9f8e7d6c"


def test_describe_prefers_proc_over_filename(monkeypatch):
    monkeypatch.setattr(mux, "_dest_from_pid", lambda pid: "real@host")
    assert mux.describe_master("/home/u/.ssh/whatever@ignored", 4242) == "real@host"


# --- resolve_socket_or_dirs --------------------------------------------------
def test_resolve_empty_gives_default_dirs():
    sock, dirs = mux.resolve_socket_or_dirs("")
    assert sock is None
    assert dirs == mux.default_socket_dirs() and len(dirs) >= 1


def test_resolve_directory(tmp_path):
    sock, dirs = mux.resolve_socket_or_dirs(str(tmp_path))
    assert sock is None and dirs == [tmp_path]


def test_resolve_socket_file(tmp_path):
    p = tmp_path / "cm.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(p))
    try:
        sock, dirs = mux.resolve_socket_or_dirs(str(p))
        assert sock == str(p) and dirs == []
    finally:
        srv.close()


def test_resolve_bogus_path_raises(tmp_path):
    with pytest.raises(ValueError):
        mux.resolve_socket_or_dirs(str(tmp_path / "does-not-exist"))


# --- discover_masters (faked master_check) -----------------------------------
def test_discover_filters_to_live_masters(tmp_path, monkeypatch):
    (tmp_path / "regular_file").write_text("not a socket")
    live = tmp_path / "live.sock"
    dead = tmp_path / "dead.sock"
    for p in (live, dead):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(p))
        # keep the socket object alive for the duration via an attribute on the test
        _KEEP.append(s)

    def fake_check(path, timeout=2.0):
        if path.endswith("live.sock"):
            return True, 4321, "Master running (pid=4321)"
        return False, None, "not a master"

    monkeypatch.setattr(mux, "master_check", fake_check)
    found = mux.discover_masters([tmp_path])
    assert [m.path for m in found] == [str(live)]      # regular file + dead sock excluded
    assert found[0].pid == 4321


def test_discover_skips_missing_dirs(tmp_path):
    # A non-existent directory is silently skipped, not an error.
    assert mux.discover_masters([tmp_path / "nope"]) == []


_KEEP: list = []  # hold AF_UNIX sockets open so their files keep S_ISSOCK


# --- PipeChannel (against a plain echo subprocess, no ssh) --------------------
def _echo_proc():
    """A subprocess that copies stdin->stdout, like ssh's sftp pipe but trivial."""
    return subprocess.Popen(
        [sys.executable, "-c",
         "import os,sys\n"
         "while True:\n"
         " d=os.read(0,4096)\n"
         " if not d: break\n"
         " os.write(1,d)\n"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0,
    )


def test_pipechannel_send_recv_close():
    ch = mux.PipeChannel(_echo_proc())
    try:
        assert ch.send(b"hello") == 5
        assert ch.recv(5) == b"hello"
        assert ch.get_name() == "mux"
    finally:
        ch.close()
    assert ch.proc.poll() is not None  # closing terminates the subprocess


def test_pipechannel_close_does_not_clobber_next_process():
    """Regression: close() must not os.close() fds the Popen also owns, or the next
    process's recycled fd numbers get clobbered (seen as EBADF)."""
    a = mux.PipeChannel(_echo_proc())
    a.send(b"x")
    assert a.recv(1) == b"x"
    a.close()

    b = mux.PipeChannel(_echo_proc())
    try:
        b.send(b"y")
        assert b.recv(1) == b"y"  # would raise/EBADF if close() double-closed fds
    finally:
        b.close()
