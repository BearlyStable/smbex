"""Command-line entry point.

Phase 0 keeps this intentionally small: argument parsing that launches the TUI.
The full impacket-smbclient-style target/auth flags land in Phase 1 (``auth.py``)
and are wired into the connect flow in Phase 2.
"""

from __future__ import annotations

import argparse

from smbex import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smbex",
        description="Terminal explorer for remote hosts over SMB and SSH/SFTP.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"smbex {__version__}",
    )
    # target + protocol/auth flags are added in Phase 1.
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)

    # Imported lazily so `--version` / `--help` work without a terminal.
    from smbex.ui.app import SmbexApp

    SmbexApp().run()
    return 0
