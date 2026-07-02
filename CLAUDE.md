# CLAUDE.md — smbex

Guidance for any Claude thread (or human) continuing this project. Read this first.

## What we're building

`smbex`: a terminal file explorer for remote hosts, designed to stay responsive on
a slow connection. Target features (full list — most are still ahead):

- **Two protocols**, one UI:
  - **SMB** with the same login surface as `impacket-smbclient` (password, NTLM
    hash / pass-the-hash, Kerberos ccache, AES key, null session, `-dc-ip`,
    `-target-ip`, port 139/445).
  - **SSH/SCP** via SFTP (connect, browse, download). Auth: password, key file, agent.
- **Ranger-style navigation** (Miller columns, `h/j/k/l`, `gg`/`G`, previews).
- **In-session listing cache** so revisiting a folder is instant. **Session-only —
  never persisted to disk.**
- **Background downloads** (single file / all files in current folder / recursive
  folder), replicating the remote tree locally. Viewable download list + progress.
- **Browsing has priority**: downloads are throttled to whatever bandwidth is left
  so navigation stays snappy.
- **Preloading** of surrounding folders' listings (toggleable off).
- **Offline translation** of filenames to English, shown beside the original when
  toggled. Any language; each language is one downloadable model file.
- **Dark mode** (default).

## Current status

| Phase | Scope | State |
|------|-------|-------|
| 0 | Scaffold, packaging, venv, dark-mode app boots, smoke tests | **done** |
| 1 | SMB connect/auth + backend abstraction + gateway + cache + tests | **done** |
| 2 | Ranger-style browser UI wired to cache + tests | **done** |
| 3 | SSH/SFTP backend (paramiko) + tests | **next** |
| 4 | Background downloads (SMB+SSH) + local mirror + progress UI | handed off |
| 5 | Prioritization & throttling (browse preempts downloads) | handed off |
| 6 | Preloader (surrounding folders, toggle) | handed off |
| 7 | Offline translation (argostranslate) + toggle | handed off |
| 8 | Polish (help, reconnect, config, theming) | handed off |

**Definition of done for any feature: its tests pass AND the code is committed.**
Commit once per completed phase (or smaller), with green tests in that commit.

## Install & environment

Dev was done on Fedora 44 / Python 3.13. Deploy target is **Kali**, where the user
installs Python deps **via apt only** (no pip).

### Kali (apt) — the user's environment

```sh
sudo apt install python3-impacket python3-paramiko python3-textual \
                 python3-pytest python3-pytest-asyncio
```

Run apt-only, straight from the repo (cwd is on `sys.path`, so no install step):

```sh
python3 -m smbex ...
```

Approximate versions in Kali rolling (Debian sid), confirmed on packages.debian.org
(July 2026) — they line up closely with the dev versions, so behavior matches:

| Dependency | apt package | Kali/sid ver | dev ver | Notes |
|---|---|---|---|---|
| impacket | `python3-impacket` | 0.13.0 | 0.13.1 | SMB + auth |
| paramiko | `python3-paramiko` | 4.0.0 | 5.0.0 | SSH/SFTP; keep to stable `SSHClient`/`SFTPClient` API |
| textual | `python3-textual` | 8.2.3 | 8.2.8 | needs the theme API (Textual ≳ 2.x; sid is fine, Debian *stable* trixie=2.1.2 ok, bookworm=0.1.13 **too old**) |
| pytest | `python3-pytest` | — | 9.1.1 | dev/test |
| pytest-asyncio | `python3-pytest-asyncio` | 1.4.0 | 1.4.0 | dev/test; `asyncio_mode=auto` set in pyproject |
| **argostranslate** | **none** | — | (Phase 7) | **NOT in apt.** Install via pipx or a venv when doing Phase 7; models are downloaded `.argosmodel` files. |

> Because `argostranslate` isn't apt-installable, treat translation as opt-in: keep
> the core app fully functional (and testable) without it. When implementing Phase 7,
> import it lazily and degrade gracefully if it's absent. Flag the pip/pipx
> requirement to the user rather than silently assuming pip.

### Dev (venv, any distro)

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"       # add ".[dev,translate]" for Phase 7
.venv/bin/python -m pytest -q
```

## How to run & test

```sh
.venv/bin/python -m pytest -q                 # unit + Textual Pilot tests (fast, offline)
.venv/bin/python -m pytest -m integration     # real local SMB/SSH servers (Phase 1+)
.venv/bin/python -m smbex --version
```

## Architecture

Two load-bearing seams keep this testable and responsive:

1. **Backend abstraction** (`smbex/backend/base.py`) — a protocol with
   `roots() / list() / stat() / open_read()` over a single POSIX-style path.
   Implementations: `impacket_backend.py` (SMB; first path component = share),
   `ssh_backend.py` (paramiko/SFTP, Phase 3), and `fake_backend.py` (in-memory tree
   for fast offline tests). Everything above the backend is protocol-agnostic.
2. **Serializing gateway** (`smbex/gateway.py`) — owns the connection and an asyncio
   **priority** queue. One worker pops the highest-priority job and runs the blocking
   backend call via `asyncio.to_thread` behind a lock (a single impacket/paramiko
   connection is not concurrency-safe). Browse/preload = high priority; downloads =
   low and **yield between chunks** so a queued browse job runs first. This is the
   throttling mechanism — design downloads around it from the start.

```
smbex/
  __main__.py            python -m smbex
  cli.py                 ✓ argparse; impacket-style SMB flags + ssh:// target; connect + launch
  auth.py                ✓ ConnSpec: parse smb/ssh target + build login params
  backend/
    base.py              ✓ Backend protocol, DirEntry
    impacket_backend.py  ✓ SMB via impacket SMBConnection
    fake_backend.py      ✓ in-memory tree for tests
    ssh_backend.py       Phase 3 — SSH/SFTP via paramiko
  gateway.py             ✓ async priority-queue gateway (browse preempts download)
  cache.py               ✓ in-memory, session-only listing cache
  browser.py             ✓ ranger navigation controller (cache-backed, cursor memory)
  download.py            Phase 4 — DownloadManager
  preload.py             Phase 6 — Preloader
  translate.py           Phase 7 — Translator (lazy argostranslate)
  ui/
    app.py               ✓ Textual ranger UI (parent|current|preview, dark default)
    columns.py           ✓ Miller-column widget
tests/                   pytest; FakeBackend + live SMB server fixtures
```

### Concurrency notes
- Textual runs on asyncio. impacket and paramiko are **blocking** → always call them
  through the gateway (`to_thread` + lock), never directly from UI event handlers.
- One `SMBConnection` / `SFTPClient` per gateway; do not share across threads.
- SSH can open a second channel for downloads (Phase 4/5) so a big transfer doesn't
  block SFTP listing — optional optimization, not required.

## Testing strategy
- **Unit**: drive gateway/cache/navigation with `FakeBackend` — deterministic, offline.
- **UI**: Textual `App.run_test()` returns a `Pilot`; press keys and assert widget
  state headlessly (no real terminal). See `tests/test_smoke.py`.
- **Integration** (`@pytest.mark.integration`): stand up a real server locally and
  run the real backend against it — no external infra:
  - SMB: `impacket.smbserver.SimpleSMBServer` on `127.0.0.1:4455` with a temp share.
  - SSH: an in-process `paramiko` SFTP server on a localhost port.
  Skip with a clear reason if the port can't bind.
- Cross-version Textual: resolve dark mode via the theme object if present, else the
  legacy `dark` flag (see `_is_dark` in `tests/test_smoke.py`).

## Conventions
- Test-first where practical; a feature isn't done until tests pass and it's committed.
- Keep the core importable and testable **without** `argostranslate` (lazy import).
- The listing cache is **session-only**; do not add disk persistence.
- Match the existing style: `from __future__ import annotations`, type hints,
  small modules, docstrings that state constraints (not narration).
- Don't touch system Python; use the `.venv` for dev.

## Key decisions (and why)
- **Python, not Rust**: `impacket` gives free auth parity with `impacket-smbclient`
  and is already on Kali; `argostranslate` matches "one downloadable file per
  language" for offline translation; Textual is async-native (fits background
  downloads + preload). Rust would mean a single static binary but sacrifice auth
  breadth and offline translation.
- **Protocol-generic backend** so SMB and SSH share the gateway, cache, and UI.
- **argostranslate is pip/pipx-only on Kali** — the one dependency outside apt;
  keep translation optional so the apt-only install stays fully functional.
