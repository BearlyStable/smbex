"""Command-line entry point: parse an impacket-style SMB target (or ssh:// URL),
connect, and launch the TUI."""

from __future__ import annotations

import argparse

from smbex import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smbex",
        description="Terminal explorer for remote hosts over SMB and SSH/SFTP.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="SMB: [domain/]user[:password]@host   |   SSH: ssh://user@host[:port][/path]",
    )
    parser.add_argument("--version", action="version", version=f"smbex {__version__}")

    smb = parser.add_argument_group("SMB auth (impacket-smbclient compatible)")
    smb.add_argument("-H", "--hashes", metavar="LM:NT", help="NTLM hashes for pass-the-hash")
    smb.add_argument("-k", "--kerberos", action="store_true", help="use Kerberos (ccache)")
    smb.add_argument("--no-pass", action="store_true", help="don't send/prompt a password")
    smb.add_argument("--aes-key", default="", metavar="HEX", help="Kerberos AES key (implies -k)")
    smb.add_argument("--dc-ip", metavar="IP", help="domain controller / KDC address")
    smb.add_argument("--target-ip", metavar="IP", help="address to dial if host is a name")
    smb.add_argument("--port", type=int, choices=(139, 445), default=445)

    ssh = parser.add_argument_group("SSH auth")
    ssh.add_argument("-i", "--identity", metavar="KEYFILE", help="SSH private key file")

    ui = parser.add_argument_group("UI")
    ui.add_argument("--no-preload", action="store_true", help="disable folder preloading")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.target:
        parser.error("a target is required, e.g. 'DOMAIN/user:pass@host' or 'ssh://user@host'")

    from smbex.auth import Proto, make_conn_spec

    spec = make_conn_spec(
        args.target,
        hashes=args.hashes,
        no_pass=args.no_pass,
        kerberos=args.kerberos,
        aes_key=args.aes_key,
        dc_ip=args.dc_ip,
        target_ip=args.target_ip,
        port=args.port,
        identity=args.identity,
    )

    if spec.proto is Proto.SSH:
        raise SystemExit("ssh:// targets arrive in Phase 3; SMB targets work today.")

    smb = spec.smb
    assert smb is not None
    if smb.username and not smb.has_creds and not smb.use_kerberos and not smb.no_pass:
        import getpass

        smb.password = getpass.getpass(f"Password for {smb.username or 'guest'}: ")

    from smbex.backend.impacket_backend import ImpacketBackend
    from smbex.gateway import Gateway
    from smbex.ui.app import SmbexApp

    try:
        backend = ImpacketBackend.connect(smb)
    except Exception as exc:  # noqa: BLE001 - report any connection failure cleanly
        raise SystemExit(f"connection failed: {exc}")

    SmbexApp(Gateway(backend), preload=not args.no_preload, label=args.target).run()
    return 0
