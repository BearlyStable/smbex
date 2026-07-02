"""CLI argument parsing — target plus the impacket-style auth/port flags."""

from __future__ import annotations

from smbex.cli import build_parser


def test_parses_target_and_arbitrary_port():
    args = build_parser().parse_args(["DOMAIN/u:p@host", "--port", "4455"])
    assert args.target == "DOMAIN/u:p@host"
    assert args.port == 4455  # arbitrary ports allowed (tunneled/pivoted SMB)


def test_default_port_is_445():
    assert build_parser().parse_args(["host"]).port == 445


def test_auth_flags():
    args = build_parser().parse_args(["host", "-H", "LM:NT", "-k", "--no-pass"])
    assert args.hashes == "LM:NT"
    assert args.kerberos is True
    assert args.no_pass is True
