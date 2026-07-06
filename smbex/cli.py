"""Command-line entry point: parse an impacket-style SMB target (or ssh:// URL),
connect, and launch the TUI."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from smbex import __version__


def _safe(host: str) -> str:
    """A filesystem-safe per-host download subdirectory name."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", host) or "host"


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
    smb.add_argument(
        "--port",
        type=int,
        default=445,
        metavar="PORT",
        help="SMB port (default 445; 139 legacy; any port for tunneled/pivoted SMB)",
    )

    ssh = parser.add_argument_group("SSH auth")
    ssh.add_argument("-i", "--identity", metavar="KEYFILE", help="SSH private key file")
    ssh.add_argument(
        "--strict-host-keys",
        action="store_true",
        help="verify SSH host keys against ~/.ssh/known_hosts (default: accept & remember)",
    )
    ssh.add_argument(
        "--ignore-host-keys",
        action="store_true",
        help="never check or store SSH host keys",
    )

    ui = parser.add_argument_group("UI")
    ui.add_argument(
        "--preload",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="prefetch surrounding folders while browsing (toggle in-app with 'p')",
    )
    ui.add_argument(
        "--auto-reconnect",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="silently reconnect after a dropped link (default: off — report the drop "
        "and wait for 'r', so a new login/session event is operator-driven)",
    )
    ui.add_argument(
        "--sort",
        choices=("name", "newest", "oldest"),
        default="name",
        help="initial listing sort (cycle in-app with 'o'; default: name)",
    )
    ui.add_argument(
        "--theme",
        metavar="NAME",
        default="dark",
        help="colour theme: dark, light, nord, gruvbox, … (switch in-app with 'T'; default: dark)",
    )
    ui.add_argument(
        "--config",
        metavar="FILE",
        help="config file to read (default: ~/.config/smbex/config.ini)",
    )
    ui.add_argument(
        "--write-config",
        action="store_true",
        help="write a commented sample config to the config path, then exit",
    )
    ui.add_argument(
        "--translate",
        metavar="LANG",
        help="show English translations of filenames from LANG (e.g. de); toggle in-app with 't'. "
        "Runs fully offline on this machine; needs the model (see --install-lang).",
    )
    ui.add_argument(
        "--install-lang",
        metavar="LANG",
        help="download + install the LANG->English translation model, then exit "
        "(one-time; the only step that uses the network)",
    )
    ui.add_argument(
        "--model-file",
        metavar="FILE",
        help="with --install-lang: install from this local .argosmodel file instead "
        "of downloading (offline model setup)",
    )
    ui.add_argument(
        "--download-dir",
        metavar="DIR",
        default="downloads",
        help="local root for downloads; the remote tree is mirrored under DIR/<host> (default: ./downloads)",
    )
    return parser


def _connect_ssh(ssh):
    """Connect over SSH, prompting once for a password if key/agent auth fails."""
    from smbex.backend.ssh_backend import SshBackend

    try:
        return SshBackend.connect(ssh)
    except Exception as exc:  # noqa: BLE001
        import paramiko

        if isinstance(exc, paramiko.AuthenticationException) and not ssh.password:
            import getpass

            who = ssh.username or getpass.getuser()
            ssh.password = getpass.getpass(f"Password for {who}@{ssh.host}: ")
            try:
                return SshBackend.connect(ssh)
            except Exception as retry_exc:  # noqa: BLE001
                raise SystemExit(f"connection failed: {retry_exc}")
        raise SystemExit(f"connection failed: {exc}")


def main(argv: list[str] | None = None) -> int:
    from smbex.config import load_config, write_sample_config

    parser = build_parser()
    # Resolve --config / --write-config first, then seed defaults from the config so
    # that CLI flags (parsed next) still override. Built-in default < config < flag.
    pre, _ = parser.parse_known_args(argv)
    if pre.write_config:
        print(f"Wrote sample config to {write_sample_config(pre.config)}")
        return 0
    parser.set_defaults(**load_config(pre.config))
    args = parser.parse_args(argv)

    if args.install_lang:  # one-time model setup; no host connection needed
        from smbex.translate import install_from_file, install_model

        code = args.install_lang
        try:
            if args.model_file:
                print(f"Installing {code}->en model from {args.model_file} ...")
                dest = install_from_file(args.model_file, code)
            else:
                print(f"Downloading {code}->en translation model (one-time, needs network)...")
                dest = install_model(code)
        except Exception as exc:  # noqa: BLE001 - report cleanly and exit non-zero
            raise SystemExit(f"model install failed: {exc}")
        print(f"Installed to {dest}.  Launch with:  --translate {code}")
        return 0

    if not args.target:
        parser.error("a target is required, e.g. 'DOMAIN/user:pass@host' or 'ssh://user@host'")

    translator = None
    if args.translate:
        from smbex.translate import Ct2Translator

        translator = Ct2Translator(args.translate)

    from smbex.auth import Proto, make_conn_spec

    policy = "strict" if args.strict_host_keys else "ignore" if args.ignore_host_keys else "auto"
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
        known_hosts_policy=policy,
    )

    from smbex.gateway import Gateway
    from smbex.ui.app import SmbexApp

    if spec.proto is Proto.SSH:
        backend = _connect_ssh(spec.ssh)
        SmbexApp(
            Gateway(backend, auto_reconnect=args.auto_reconnect),
            start_path=getattr(backend, "start_rel", ""),
            preload=args.preload,
            label=args.target,
            download_root=Path(args.download_dir) / _safe(spec.ssh.host),
            translator=translator,
            sort=args.sort,
            theme=args.theme,
        ).run()
        return 0

    smb = spec.smb
    assert smb is not None
    if smb.username and not smb.has_creds and not smb.use_kerberos and not smb.no_pass:
        import getpass

        smb.password = getpass.getpass(f"Password for {smb.username or 'guest'}: ")

    from smbex.backend.impacket_backend import ImpacketBackend

    try:
        backend = ImpacketBackend.connect(smb)
    except Exception as exc:  # noqa: BLE001 - report any connection failure cleanly
        raise SystemExit(f"connection failed: {exc}")

    SmbexApp(
        Gateway(backend, auto_reconnect=args.auto_reconnect),
        preload=args.preload,
        label=args.target,
        download_root=Path(args.download_dir) / _safe(smb.host),
        translator=translator,
        sort=args.sort,
        theme=args.theme,
    ).run()
    return 0
