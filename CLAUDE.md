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
| 3 | SSH/SFTP backend (paramiko) + tests | **done** |
| 4 | Background downloads (SMB+SSH) + local mirror + progress UI | **done** |
| 5 | Prioritization & throttling (browse preempts downloads) | **done** (core; see note) |
| 6 | Preloader (surrounding folders, toggle) | **done** |
| 7 | Offline translation (CTranslate2 + SentencePiece) + toggle | **done** |
| 8 | Polish (help, reconnect, config, theming) | **done** |

> Phase 5 note: the throttle (browse preempts an in-flight download between chunks)
> is implemented and tested (`test_browse_preempts_between_download_chunks`). What
> remains is optional: an SSH second channel so a big transfer doesn't share the
> browse channel, and a live bandwidth/ETA display.

> Phase 6 note: on every navigation the browser warms the neighbourhood — the
> selected subdirectory, its siblings, and the parent — at `Priority.PRELOAD`
> (`smbex/preload.py`, `Preloader`). Fire-and-forget, cache-checked and single-flight
> (skips cached / in-flight paths), toggle-gated by `p` (which also warms the current
> view when switched on). **Default off** — opt in with `--preload` or the `p` toggle.
> Tested in `tests/test_preload.py`. Not done: preloading a level deeper
> (grandchildren) or the current dir's same-level siblings — the current set is the
> ranger "surrounding folders" neighbourhood, one hop in each direction.

> Phase 7 note: filename translation is local-only and lean (`smbex/translate.py`,
> `Ct2Translator`). It drives an Argos model **directly** with CTranslate2 (inference)
> + SentencePiece (tokeniser) — deliberately not the `argostranslate` library, whose
> `stanza -> torch` pull is ~5 GB of unused CUDA; runtime is ~65 MB and output is
> identical for filenames (same model, same `translate_batch` params incl.
> `replace_unknowns`/`length_penalty=0.2`). Configure a source language with
> `--translate <lang>`; `t` toggles the English column beside the original
> (extensions preserved), session-cached. **Privacy by construction:** inference is
> on-box and the translate path never hits the network — the *only* networked step is
> `--install-lang <lang>` (stdlib `urllib`+`zipfile` fetch/unpack of the one
> `.argosmodel` file; `--model-file` installs a local one). Missing model → originals
> shown + a status-bar hint. Tests: `FakeTranslator` (offline) + a synthetic-zip
> install/discovery unit test; real ja→en round-trips are `@pytest.mark.integration`,
> skipped when the model/engine is absent. Not done: language auto-detection (source
> is user-specified); smarter `snake_case`/compound handling (stem is one token).

> Phase 8 note (reconnect/recovery — done): the gateway handles a dropped link, and
> reconnect is **operator-driven by default** — a silent reconnect makes a fresh
> login/session event, which an operator may not want. `Gateway._execute` classifies
> a failure via the backend's `is_connection_error` (socket/transport: OSError/EOFError/
> Timeout/NetBIOSError for SMB; those + paramiko `SSHException` for SSH — operational
> errors like impacket `SessionError` "not found" propagate untouched):
> - **default (`auto_reconnect=False`)**: record the drop, emit `on_status`
>   "disconnected", and propagate; while down, jobs fail fast (but cached listings
>   still serve). `Gateway.reconnect()` — the `r` key (`action_reconnect`) — does one
>   deliberate reconnect (the only new login event). `_execute` runs it as a *raw* job
>   so it isn't itself wrapped in drop handling.
> - **`--auto-reconnect`**: heal transparently — reconnect (up to `reconnect_attempts`,
>   `reconnect_delay` apart) and retry the job once.
> `on_status` (reconnecting/connected/disconnected) drives a coloured status banner
> (`_on_conn_status`); disconnected shows "press 'r' to reconnect". Backends retain
> auth (`SmbAuth`/`SshAuth`) and rebuild in `reconnect()`. `Browser.load` is atomic
> (fetch then commit) so a dropped navigation stays put; `_refresh` tolerates a drop in
> the parent/preview fetches (current column always renders from memory). Browsing
> recovers; a download's in-flight handle can't survive a reconnect, so that transfer
> errors and resumes when re-grabbed. **Verified against a live SMB server** both ways
> (socket-drop → OSError; default reports + waits, `r` recovers; `--auto-reconnect`
> heals+retries). A torn-down impacket conn can instead raise `AttributeError` (only
> after an explicit `close()`, not a real drop) — not caught by design. Tested in
> `tests/test_reconnect.py` (+ SSH in `test_backend_ssh_integration.py`). Remaining
> Phase 8: theming.

> Help note: `?` opens a dismissible modal (`smbex/ui/help.py`, `HelpScreen` +
> `help_text`) listing keys grouped Navigate/Transfer/View/Connection; Esc/`?`/`q`
> close it. Curated by hand to mirror `SmbexApp.BINDINGS`. Tested in
> `tests/test_ui_help.py`.

> Config note: `smbex/config.py` reads an INI (`[ui]`) at
> `$XDG_CONFIG_HOME/smbex/config.ini` (or `--config PATH`). INI not TOML — `tomllib`
> is 3.11+ only and we target 3.10 apt-only. Precedence is **built-in < config < CLI**:
> `main()` pre-parses to find `--config`, then `parser.set_defaults(**load_config(...))`
> before the real parse. Keys mirror argparse dests: `preload`, `auto_reconnect`,
> `translate`, `sort` (name/newest/oldest), `download_dir`; `--preload`/`--auto-reconnect`
> are `BooleanOptionalAction` so config-on can be overridden with `--no-…`. `--sort`
> seeds `SmbexApp(sort=…)` → `browser.sort_mode` via `SORT_BY_LABEL`. `--write-config`
> drops a commented sample. `theme` is also persisted (see Theming note). Tested in
> `tests/test_config.py`. Reconnect over SSH/SFTP is verified too
> (`tests/test_backend_ssh_integration.py::test_ssh_reconnect_after_drop`).

> Theming note: `--theme NAME` / config `theme` set the startup theme (dark default);
> `T` cycles `_THEME_CYCLE` (textual-dark/-light/nord/gruvbox, filtered to those
> `available_themes` has). `_THEME_ALIASES` maps dark→textual-dark, light→textual-light;
> any other name passes through to Textual, else falls back to textual-dark
> (`SmbexApp._resolve_theme`). Tested in `tests/test_ui_theme.py`. **Phase 8 complete —
> all planned phases (0–8) are done.**

> Timestamps note: entries show a compact age (`columns.py` `human_time`: '5m'/'3h'/
> '2d'/'3w'/'6mo'/'2y', blank for unknown mtime=0) in every column; the file preview
> shows the absolute stamp (`full_time`). `mtime` is the only cross-protocol time
> (SFTP v3 = mtime/atime; SMB also has creation/change) and is already populated by
> both backends, so no backend change was needed. Sorting: `Browser.sort_mode` +
> `_sorted` (canonical name order stays in the cache; the active sort is a view
> transform applied in `load`/`parent_entries`/`preview_entries`). `o` cycles
> name→newest→oldest, keeping the selection and showing the mode in the status bar;
> mtime modes interleave dirs/files by time (name tiebreak). Tested in
> `tests/test_timestamps.py`. Recursive subtree time is a separate backlog item.

> Status markers note: the current column carries a one-char status gutter
> (`SmbexApp._entry_markers` → `Column.show(markers=...)`, styled in `columns.py`
> `MARKER_STYLE`): `·` dir listing cached (`path in browser.cache`), `↓` queued/
> downloading, `✓` downloaded, `✗` error. Folders aggregate the `DownloadManager.items`
> beneath them; files match their own `remote_path`. Pure render over in-memory state
> (no backend calls); re-rendered live on download progress via `_on_downloads_change`.
> Tested in `tests/test_ui_markers.py`. Only the current column is marked (its
> `child_path` is the correct base); parent/preview are left plain.

**Definition of done for any feature: its tests pass AND the code is committed.**
Commit once per completed phase (or smaller), with green tests in that commit.

## Open items / backlog (not yet done)

Remaining phases:
- **Phase 8 — Polish.** Help screen, reconnect/error recovery, config file, theming.

Smaller items raised in discussion (not blocking):
- **Recursive "latest modified in subtree" time.** A follow-on to the timestamp
  feature: a folder's own mtime only reflects add/remove/rename of its *direct*
  entries — not deep edits or in-place file changes (true on NTFS-over-SMB and
  POSIX-over-SFTP alike). To show "newest thing anywhere under here" you must walk the
  tree (max mtime) — do it on demand at low priority and cache it, exactly like the
  on-demand folder-size item; SSH can shortcut with `find DIR -printf '%T@\n' | sort
  -n | tail -1`. Also optional: a richer SMB-only view of creation/change times
  (SFTP v3 has neither).
- **Download reordering.** The download queue is FIFO with no way to prioritize one
  transfer; consider a "jump to front" key on the task panel.
- **On-demand folder sizes.** Not shown today (see Key decisions). If wanted: a key
  that computes the selected folder's recursive size at low priority and caches it;
  SSH can shell out to `du -sb`.
- **`gg` chord.** The feature list mentions ranger's `gg`; the current UI binds a
  single `g` for top (and `G` for bottom), and `d`/`a` (not `dd`/`da`/`dr`) for
  downloads. Decide whether to implement the real multi-key chords.
- **Phase 5 refinements (optional).** SSH second channel for downloads; live
  bandwidth/ETA in the task panel.

Actual keybindings today: `h/j/k/l`+arrows, `g`/`G`, `l`/`Enter` open, `h` up,
`d` download selected (file, or folder recursively), `a` all files here, `w` task
panel, `o` cycle sort (name→newest→oldest), `p` preload toggle (prefetches
surrounding folders), `r` reconnect (after a dropped link; auto only with
`--auto-reconnect`), `t` translate toggle (English beside originals; needs
`--translate <lang>`), `T` cycle colour theme (dark/light/nord/gruvbox), `?` help
overlay, `q` quit.

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
| ctranslate2 | **none** | — | 4.8.1 | **NOT in apt.** Translation inference engine; small wheel. |
| sentencepiece | `python3-sentencepiece` | 0.2.x | 0.2.1 | Tokeniser. In apt, but pip-installing it with ctranslate2 is simplest. |

> Translation is opt-in and lazy-imported: the core app is fully functional and
> testable without it. The engine is **CTranslate2 + SentencePiece driving an Argos
> model directly** — *not* the `argostranslate` library (whose `stanza -> torch`
> pull is ~5 GB of unused CUDA; see Key decisions). Runtime deps are ~65 MB. To keep
> the apt-only core untouched, use a venv that **inherits** the apt packages:
>
> ```sh
> python3 -m venv --system-site-packages ~/.venvs/smbex   # sees apt impacket/paramiko/textual
> ~/.venvs/smbex/bin/pip install ctranslate2 sentencepiece   # ~65 MB, no torch/CUDA
> ~/.venvs/smbex/bin/python -m smbex --install-lang ja      # one-time, online: fetch the ja->en model (~130 MB)
> ~/.venvs/smbex/bin/python -m smbex --translate ja user@host   # run; 't' toggles the English column
> ```
>
> A language is **one `.argosmodel` file** (a zip of `model/` + `sentencepiece.model`
> + `metadata.json`). `--install-lang ja` fetches it (stdlib `urllib`+`zipfile`, via
> the Argos index) into `~/.local/share/smbex/models/`; `--install-lang ja --model-file
> X.argosmodel` installs a pre-downloaded one (offline / air-gapped). Models from an
> existing `argostranslate` install under `~/.local/share/argos-translate/packages`
> are auto-discovered and reused. No `--break-system-packages`, no core reinstall.
> Inference is on-box; no filename leaves the machine. If the model is absent the app
> shows originals and the status bar prints the `--install-lang` hint.

### Dev (venv, any distro)

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"       # add ".[dev,translate]" for translation
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
   `roots() / list() / stat() / open_read() / open_file()` over a single POSIX path.
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
    ssh_backend.py       ✓ SSH/SFTP via paramiko (TOFU host keys; unified path rooted at /)
  gateway.py             ✓ async priority-queue gateway (browse preempts download; reconnect/retry)
  cache.py               ✓ in-memory, session-only listing cache
  config.py              ✓ INI config (~/.config/smbex/config.ini); built-in<config<CLI
  browser.py             ✓ ranger navigation controller (cache-backed, cursor memory)
  download.py            ✓ background DownloadManager (resume/skip, mirror, throttled; one handle/file)
  preload.py             ✓ surrounding-folder preloader (PRELOAD-priority, toggle-gated)
  translate.py           ✓ local filename translation (CTranslate2 + SentencePiece; lazy)
  ui/
    app.py               ✓ Textual ranger UI (parent|current|preview, dark default)
    columns.py           ✓ Miller-column widget
    downloads.py         ✓ download/task panel (progress bars)
    help.py              ✓ '?' help overlay (ModalScreen, keybindings by group)
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
- Keep the core importable and testable **without** the translation engine
  (`ctranslate2`/`sentencepiece` imported lazily).
- The listing cache is **session-only**; do not add disk persistence.
- Match the existing style: `from __future__ import annotations`, type hints,
  small modules, docstrings that state constraints (not narration).
- Don't touch system Python; use the `.venv` for dev.

## Key decisions (and why)
- **Python, not Rust**: `impacket` gives free auth parity with `impacket-smbclient`
  and is already on Kali; the Argos model format matches "one downloadable file per
  language" for offline translation; Textual is async-native (fits background
  downloads + preload). Rust would mean a single static binary but sacrifice auth
  breadth and offline translation.
- **Protocol-generic backend** so SMB and SSH share the gateway, cache, and UI.
- **Downloads open each remote file once** (`Backend.open_file` → `RemoteFile`),
  then read successive ranges as separate low-priority jobs. Still preemptible by
  browsing, but the SMB wire/audit footprint (one CREATE/CLOSE + TREE_CONNECT per
  file) stays like a normal client instead of one cycle per 256 KB chunk.
- **Folder sizes aren't shown** because neither SMB nor SFTP can report a
  directory's recursive size without walking it; if added, do it on demand at low
  priority and cache it (SSH could shell out to `du -sb`), never eagerly per listing.
- **CTranslate2 + SentencePiece, not the `argostranslate` library** — same Argos
  model and identical filename output, but ~65 MB of runtime instead of ~5 GB:
  `argostranslate` hard-depends on `stanza`→`torch` (CUDA), used only for sentence
  segmentation that filenames don't need. `smbex/translate.py` drives the model dir
  directly (verified identical on the demo vocabulary). These deps are pip-only (not
  apt), so translation stays optional/lazy — enable via a `--system-site-packages`
  venv (see Install & environment).
- **Translation is on-box only** — chosen over any cloud/API translator because
  filenames must not leave the machine. CTranslate2 runs the model locally;
  `smbex/translate.py` touches only the installed model at translate time, so the
  sole network use is the deliberate `--install-lang` model fetch.
