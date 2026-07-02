"""Auth/target parsing — the impacket-smbclient login surface plus SSH URLs."""

from __future__ import annotations

import pytest

from smbex.auth import (
    Proto,
    build_smb_auth,
    build_ssh_auth,
    make_conn_spec,
    parse_smb_target,
    smb_kerberos_kwargs,
    smb_login_kwargs,
)

NT = "31d6cfe0d16ae931b73c59d7e0c089c0"
LM = "aad3b435b51404eeaad3b435b51404ee"


@pytest.mark.parametrize(
    "target, expected",
    [
        ("host", ("", "", "", "host")),
        ("user@host", ("", "user", "", "host")),
        ("DOMAIN/user@host", ("DOMAIN", "user", "", "host")),
        ("user:pass@host", ("", "user", "pass", "host")),
        ("DOMAIN/user:pass@host", ("DOMAIN", "user", "pass", "host")),
        ("user:p@ss@host", ("", "user", "p@ss", "host")),  # '@' in password
        ("10.0.0.1", ("", "", "", "10.0.0.1")),
    ],
)
def test_parse_smb_target(target, expected):
    assert parse_smb_target(target) == expected


def test_smb_password_login_kwargs():
    a = build_smb_auth("DOMAIN/user:pass@host")
    assert (a.domain, a.username, a.password, a.host) == ("DOMAIN", "user", "pass", "host")
    assert a.port == 445 and not a.use_kerberos
    assert smb_login_kwargs(a) == dict(
        user="user", password="pass", domain="DOMAIN", lmhash="", nthash=""
    )


def test_smb_pass_the_hash():
    a = build_smb_auth("user@host", hashes=f"{LM}:{NT}", no_pass=True)
    assert a.lmhash == LM and a.nthash == NT
    assert a.password == ""  # no_pass drops any password
    assert a.has_creds


def test_smb_nt_hash_only():
    a = build_smb_auth("user@host", hashes=f":{NT}")
    assert a.lmhash == "" and a.nthash == NT


def test_kerberos_ccache_uses_cache():
    a = build_smb_auth("user@host", kerberos=True, no_pass=True)
    assert a.use_kerberos
    kw = smb_kerberos_kwargs(a)
    assert kw["useCache"] is True and kw["aesKey"] == ""


def test_aeskey_implies_kerberos_without_cache():
    a = build_smb_auth("user@host", aes_key="DEADBEEF")
    assert a.use_kerberos  # implied by the AES key
    kw = smb_kerberos_kwargs(a)
    assert kw["aesKey"] == "DEADBEEF" and kw["useCache"] is False


def test_port_override():
    assert build_smb_auth("user@host", port=139).port == 139


def test_target_ip_and_dc_ip():
    a = build_smb_auth("user@HOSTNAME", target_ip="10.0.0.5", dc_ip="10.0.0.1")
    assert a.remote_name == "HOSTNAME"
    assert a.remote_host == "10.0.0.5"  # dial the explicit IP
    assert a.dc_ip == "10.0.0.1"


def test_ssh_full_url():
    s = build_ssh_auth("ssh://user:pass@host:2222/var/log")
    assert (s.host, s.username, s.password, s.port, s.start_path) == (
        "host",
        "user",
        "pass",
        2222,
        "/var/log",
    )


def test_ssh_defaults():
    s = build_ssh_auth("ssh://host")
    assert s.port == 22 and s.username == "" and s.start_path == "."


def test_ssh_identity_file():
    s = build_ssh_auth("ssh://user@host", identity="/home/me/.ssh/id_ed25519")
    assert s.key_filename == "/home/me/.ssh/id_ed25519"


def test_make_conn_spec_dispatch():
    smb = make_conn_spec("DOMAIN/user:pass@host")
    assert smb.proto is Proto.SMB and smb.smb is not None and smb.smb.username == "user"
    ssh = make_conn_spec("ssh://user@host")
    assert ssh.proto is Proto.SSH and ssh.ssh is not None and ssh.ssh.host == "host"
