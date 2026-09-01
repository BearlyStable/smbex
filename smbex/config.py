"""User config file (INI): personal defaults for the UI flags.

Location: ``$XDG_CONFIG_HOME/smbex/config.ini`` (i.e. ``~/.config/smbex/config.ini``),
overridable with ``--config PATH``. Precedence is built-in default < config < CLI
flag, so config sets your defaults and any flag still overrides per run.

INI (``configparser``), not TOML: ``tomllib`` is stdlib only on 3.11+, and this
project targets Python >= 3.10 with an apt-only (no pip) install on Kali —
configparser is stdlib everywhere and hand-editable with comments. This is
persisted *preferences*, distinct from the session-only listing cache (never
written to disk).
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path

# Keys mirrored to argparse dests; the rest are treated as strings.
_BOOL_KEYS = ("preload", "auto_reconnect", "parent", "preview", "flat")
_STR_KEYS = ("translate", "sort", "theme", "download_dir")

_SAMPLE = """\
# smbex configuration.  Location: ~/.config/smbex/config.ini
# (override with --config PATH).  Command-line flags override these values.

[ui]
# Prefetch surrounding folders while browsing (true/false).
preload = false

# Silently reconnect after a dropped link (true/false).  A reconnect creates a
# fresh login/session event, so this is off by default — you press 'r' instead.
auto_reconnect = false

# Translate filenames from this language to English (blank = off), e.g. de, ja.
# Needs the model:  smbex --install-lang <lang>
translate =

# Initial sort order:  name | newest | oldest
sort = name

# Colour theme: dark | light | nord | gruvbox (or any Textual theme name).
theme = dark

# Show the parent / preview columns (true/false). Turn off to save screen space;
# toggle in-app with '[' (parent) and ']' (preview).
parent = true
preview = true

# Local root for downloads (the remote tree is mirrored under DIR/<host>).
download_dir = downloads

# Save downloads flat (true/false): one folder per host, the remote path folded
# into each filename (share/2024/report.pdf -> share_2024_report.pdf).
flat = false
"""


def config_path(explicit: str | os.PathLike | None = None) -> Path:
    """The config file path: ``--config`` if given, else the XDG default."""
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "smbex" / "config.ini"


def load_config(explicit: str | os.PathLike | None = None) -> dict:
    """Read the ``[ui]`` section into an argparse-defaults dict. Missing/unreadable
    file or blank values -> nothing (so the built-in/CLI default stands)."""
    path = config_path(explicit)
    if not path.is_file():
        return {}
    parser = configparser.ConfigParser()
    try:
        parser.read(path)
    except configparser.Error:
        return {}  # a malformed config must not stop the app from launching
    if not parser.has_section("ui"):
        return {}
    ui = parser["ui"]
    out: dict = {}
    for key in _BOOL_KEYS:
        if key in ui:
            try:
                out[key] = ui.getboolean(key)
            except ValueError:
                pass
    for key in _STR_KEYS:
        val = ui.get(key, "").strip()
        if val:  # ignore blank -> keep the built-in/CLI default
            out[key] = val
    return out


def write_sample_config(explicit: str | os.PathLike | None = None) -> Path:
    """Write a commented default config and return its path (creates parents)."""
    path = config_path(explicit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SAMPLE)
    return path
