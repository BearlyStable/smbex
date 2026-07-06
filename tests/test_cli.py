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


def test_preload_defaults_off_and_is_opt_in():
    assert build_parser().parse_args(["host"]).preload is False
    assert build_parser().parse_args(["host", "--preload"]).preload is True


def test_help_has_quickstart_and_setup():
    text = build_parser().format_help()
    assert "quickstart" in text.lower()
    assert "--write-config" in text  # config setup
    assert "--install-lang" in text  # translation model install
    assert "argospm-index" in text  # the model index URL
    assert "ftp://" in text and "ssh://" in text  # all three protocols


def test_translation_flags():
    args = build_parser().parse_args(["host", "--translate", "de"])
    assert args.translate == "de"
    assert args.install_lang is None
    # --install-lang is a setup command; it needs no target.
    setup = build_parser().parse_args(["--install-lang", "de"])
    assert setup.install_lang == "de"
    assert setup.target is None


def test_ssh_host_key_flags():
    args = build_parser().parse_args(["ssh://user@host", "--strict-host-keys"])
    assert args.strict_host_keys is True
    assert args.ignore_host_keys is False
