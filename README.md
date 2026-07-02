# smbex

A terminal (TUI) explorer for remote hosts over **SMB** and **SSH/SFTP**, built for
slow links: ranger-style navigation, in-session listing cache, background downloads
that yield to browsing, folder preloading, and offline filename translation.

> Status: **foundation** in progress. See [CLAUDE.md](CLAUDE.md) for the full
> architecture, roadmap, and hand-off notes.

## Install

### Kali Linux (apt only — no pip)

Every runtime/test dependency except the translation engine is packaged for Kali:

```sh
sudo apt update
sudo apt install python3-impacket python3-paramiko python3-textual \
                 python3-pytest python3-pytest-asyncio
# optional: the reference impacket CLI tools (impacket-smbclient, ...)
# sudo apt install impacket-scripts
```

Then run straight from the repo (no pip, no venv needed):

```sh
cd /path/to/smbex
python3 -m smbex --version
```

The offline translation engine (`argostranslate`) is **not** in apt; it is only
needed for the translation feature (a later phase). See CLAUDE.md → *Install* for
the pip/pipx options when you get there.

### Development (any distro, venv)

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

## Run

```sh
python3 -m smbex            # (connect flow lands in Phase 2)
```

## Test

```sh
.venv/bin/python -m pytest -q          # unit + UI tests
.venv/bin/python -m pytest -m integration   # local SMB/SSH server tests (Phase 1+)
```
