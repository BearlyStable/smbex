# CLAUDE.md — smbex

Guidance for any Claude thread (or human) continuing this project. Read this first.

## What we're building

`smbex`: a terminal file explorer for remote hosts, designed to stay responsive on
a slow connection. Feature list (all implemented — phases 0–9 done):

- **Three protocols**, one UI:
  - **SMB** with the same login surface as `impacket-smbclient` (password, NTLM
    hash / pass-the-hash, Kerberos ccache, AES key, null session, `-dc-ip`,
    `-target-ip`, port 139/445).
  - **SSH/SCP** via SFTP (connect, browse, download). Auth: password, key file, agent,
    or **ride an existing OpenSSH ControlMaster socket** (`--mux`; no re-login).
  - **FTP / FTPS** via stdlib `ftplib` (`ftp://` / `ftps://`; anonymous or user/pass).
- **Ranger-style navigation** (Miller columns, `h/j/k/l`, `gg`/`G`, previews).
- **In-session listing cache** so revisiting a folder is instant. **Session-only —
  never persisted to disk.**
- **Background downloads** (single file / all files in current folder / recursive
  folder), replicating the remote tree locally — or **flat** (`--flat`): one folder
  per host, remote path folded into each filename. Task panel with progress, which
  stays out of the way; transfers can be cancelled or reprioritized mid-flight
  (pushing the running one down lets a small file through, then it resumes).
- **Browsing has priority**: downloads are throttled to whatever bandwidth is left
  so navigation stays snappy, and **no keystroke ever waits on the wire** (cursor
  moves render from memory; side-column listings are fetched after a settle).
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
| 9 | FTP/FTPS backend (ftplib) + tests | **done** |

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
> before the real parse. Keys mirror argparse dests and are listed in `DEFAULTS`:
> `preload`, `auto_reconnect`, `translate`, `sort` (name/newest/oldest), `theme`,
> `parent`, `preview`, `download_dir`, `download_panel` (auto/hidden), `flat`;
> `--preload`/`--auto-reconnect`/`--parent`/`--preview`/`--flat` are
> `BooleanOptionalAction` so config-on can be overridden with `--no-…`. `--sort`
> seeds `SmbexApp(sort=…)` → `browser.sort_mode` via `SORT_BY_LABEL`. Two writers,
> one commented `_TEMPLATE` (so a written file can't drift from the sample):
> `--write-config` renders `DEFAULTS`, `--save-config` renders the options *in effect*
> for that command line (`save_config(vars(args))` — i.e. built-in < config < CLI, the
> same merge the app runs with) and exits, printing what it stored. Only `DEFAULTS`
> keys are written, so a target/password/hash on the same command line never lands on
> disk. Tested in
> `tests/test_config.py`. Reconnect over SSH/SFTP is verified too
> (`tests/test_backend_ssh_integration.py::test_ssh_reconnect_after_drop`).

> Phase 9 note (FTP/FTPS): `smbex/backend/ftp_backend.py` on stdlib `ftplib` (no new
> runtime dep). `ftp://` / `ftps://` targets (`auth.build_ftp_auth`, `Proto.FTP`,
> `cli._connect_ftp`; no user → anonymous). Unified path rooted at "/" like SSH;
> listings prefer MLSD (type/size/modify → `DirEntry`) with a best-effort Unix-LIST
> fallback. Two FTP gotchas handled: (1) force `TYPE I` before every RETR — FTP
> defaults to ASCII (CRLF translation) and MLSD/LIST leave the session in TYPE A;
> (2) reads that stop before EOF **drain the data connection** rather than `ABOR`
> (aborting a RETR desyncs the control channel with a leftover 426/226). No persistent
> file handle: `open_file` streams one RETR sequentially, reopening on a seek.
> `reconnect`/`is_connection_error` mirror the others (FileNotFoundError from a 550 is
> excluded so it isn't treated as a drop). Verified against a live in-process
> pyftpdlib server, `tests/test_backend_ftp_integration.py` (list/read/stat, partial
> read, recursive download, drop→manual-reconnect, TUI browse) — **@integration; skips
> if pyftpdlib is absent** (test-only dep; `python3-pyftpdlib` on apt).

> Mux note (ride an existing SSH ControlMaster socket — `--mux`): `smbex/mux.py`
> reuses an already-authenticated OpenSSH connection instead of logging in. A control
> socket speaks OpenSSH's private multiplexing protocol (`mux.c`), **not** SSH2, so
> paramiko can't use it directly; instead we drive the system `ssh` client: `ssh -s
> sftp` over the ControlPath opens the SFTP subsystem on a multiplexed session, and
> paramiko's `SFTPClient` speaks SFTP to that subprocess over its stdio pipes
> (`PipeChannel` adapter — send/recv/get_name/close; **close via the Popen file
> objects, never `os.close` the fds**, or a later process's recycled fds get
> clobbered → EBADF). So `MuxBackend` subclasses `SshBackend` and reuses its whole
> list/stat/read surface unchanged. **Holds no credentials — only the socket path:**
> every slave is gated on `ssh -O check` (the master must be alive) and started with
> no key + `BatchMode=yes` + `-F /dev/null`, so OpenSSH's direct-connect fallback
> **can't** silently open a *new* login (verified: without the gate it does — the #1
> footgun). Reconnect (`r`) just re-checks + re-spawns; it heals **iff** a live master
> is (re-)established at the *same* socket path (there are no creds to rebuild one).
> `--mux` with no arg scans conventional control dirs (`~/.ssh`, `~/.ssh/sockets`,
> `~/.ssh/controlmasters`, `~/.ansible/cp` — deliberately **not** `$XDG_RUNTIME_DIR`,
> too many non-mux sockets), keeps sockets we own, and probes each with `-O check`
> **concurrently** with a short timeout (a non-mux socket makes `-O check` *block* to
> the timeout), then **always shows a picker** (`smbex/ui/mux_picker.py`); `--mux DIR`
> scans DIR; `--mux SOCKET` connects directly (no picker). Picker labels are
> best-effort: master pid from `-O check` → `/proc/<pid>/cmdline` (Linux) for the real
> destination → a `%r@%h:%p` filename → the socket path. **Runtime deps: none new**
> (stdlib `subprocess` + existing paramiko); needs the system `ssh` **client** (Linux;
> `/proc` for labels). Tested: `tests/test_mux.py` (offline — argv/label parsing,
> resolve, discovery with a faked `master_check`, `PipeChannel` incl. the
> fd-double-close regression), `tests/test_ui_mux_picker.py` (picker Pilot), and
> `tests/test_backend_mux_integration.py` (**@integration** — a real `ssh` master
> multiplexing in front of the in-process paramiko SFTP server, **no sshd**: list/read,
> ranged `open_file`, kill-master→manual-reconnect-same-path, TUI browse, CLI
> `_run_mux`; skips if the ssh client binaries are absent).

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
> downloading, `✓` downloaded, `✗` error, `⊘` cancelled part-way (partial on disk). Folders aggregate the `DownloadManager.items`
> beneath them; files match their own `remote_path` — bucketed in **one pass** over the
> download items (a per-entry scan was O(entries × downloads) on every repaint). Pure
> render over in-memory state (no backend calls); re-rendered live on download progress
> via `_on_downloads_change`, which coalesces progress repaints to 10/s (a status change
> always paints immediately).
> Tested in `tests/test_ui_markers.py`. Only the current column is marked (its
> `child_path` is the correct base); parent/preview are left plain.

> Layout note: listings render as a `rich` `Table.grid` (`Column.show`) — a flexible
> name(+translation) column that truncates with an ellipsis, plus fixed right-aligned
> size and age columns, so metadata stays put at any width even with translation on.
> `Column.rendered_text` is a plain-text side-channel for tests. Parent/preview columns
> are toggleable (`[`/`]`, `--parent`/`--preview`, config `parent`/`preview`); a hidden
> column skips its fetch (`_refresh`) and render — saves a round-trip on a slow link.
> **Only the visible window is rendered** (`Column.window`): a keystroke in a 10k-entry
> folder would otherwise rebuild 10k rich rows, and the cursor could walk off the pane
> (nothing scrolled it). The window follows the cursor with a 2-row scrolloff. This
> needs a *known* height, so `Column` is `height: 100%` and clips (`overflow: hidden`) —
> the columns used to auto-size to content, so their own height couldn't drive it (the
> file viewer takes its height from the `#columns` container for the same reason).

> Responsiveness note (cursor never blocks): a keypress must not await a listing.
> `SmbexApp._refresh` = `_render_now()` (paints all three columns from `browser.entries`
> + the cache — no backend call, no await) followed by `_schedule_side_refresh()`,
> which fetches only the *uncached* parent/preview listings after `SIDE_REFRESH_DELAY`
> (0.12 s) in an **exclusive** worker (`group="side"`). A newer cursor position cancels
> the pending one *during the settle*, so a key repeat never reaches the wire — holding
> `j` over 30 folders costs one listing (where you stopped), not 30 serialized behind
> each other. Cancelling after submission would **not** help: a queued gateway job runs
> regardless of who is awaiting it, which is why the debounce (not the cancel) is what
> keeps the wire quiet. An uncached side column renders `Column.show_loading()` ("…"),
> never a stale neighbour's listing. `Browser.parent_path`/`preview_path`/`peek()` are
> the cache-only accessors (`ListingCache.peek` skips hit/miss accounting so a
> speculative look isn't counted as a browse); `SmbexApp.wait_for_side_refresh()` is the
> hook tests await (fixture `settle`). Tested in `tests/test_ui_responsive.py`, which
> is the **regression guard for the lag** — a green suite is meant to mean "scrolling
> is still fluid": a gated `FakeBackend.list` proves the cursor moves (and stays under
> budget) with a fetch stuck in flight; `test_a_cursor_move_makes_no_backend_call`
> pins that the key handler never talks to the backend; and two cost tests bound the
> per-keystroke work (< 25 ms in a 20k-entry folder with 2k queued downloads — ~1 ms
> in practice) and pin that render cost doesn't grow with folder size. All were
> checked to **fail** against the pre-fix code (340 ms/keystroke unwindowed; the
> inline-fetch version deadlocks outright on the gated-listing test).

> Flat downloads note (`--flat`, config `flat`): `DownloadManager(flat=True)` maps every
> remote file into the single per-host root, `smbex/download.py` `flat_name()` joining
> the path components with `_` (`share/2024/report.pdf` → `share_2024_report.pdf`).
> Path separators + non-portable characters → `_` (non-ASCII kept: CJK names survive);
> truncation is in **bytes** keeping the extension (255-byte FS limit, CJK = 3 B/char).
> A name is assigned **once per remote path** (`_assigned`/`_claimed`) so resume and the
> preview's "is this downloaded?" lookup agree; a clash between two *different* remote
> paths (`a/b_c.txt` vs `a_b/c.txt` fold alike) is numbered `a_b_c~2.txt` — `~`, not
> `_`, so a counter can't be misread as a path component. An existing file for the
> *same* remote path is left alone: that's resume, not a clash.
> Tested in `tests/test_download_flat.py`.

> Task panel note: the panel covered the browser even with nothing to say, and a long
> queue pushed the live transfers out of view. Now `_refresh_downloads` decides
> visibility: mode `auto` (default) shows it only while `DownloadManager.pending` is
> non-empty and lists *only* those (max 4 rows), hiding itself when the queue drains;
> `--download-panel hidden` (config `download_panel`) never shows it unasked. The
> always-on readout is in the status bar (`dl:3/12 ↓47%`). `w` opens the full list
> (finished included) and makes the panel **modal**: `j`/`k` select (the panel windows
> around the cursor), `K`/`J` call `DownloadManager.reorder(item, ∓1)`, `w`/`h`/`Esc`
> close. Reordering is why the queue *is* `items`: the worker takes the first entry
> still `queued` (`_next_queued`), so swapping two queued entries changes what's next —
> an `asyncio.Queue` plus a parallel display list could not express that. A `running`
> `join()` waits on `pending` via the `_idle`/`_wake` events.
> Tested in `tests/test_ui_downloads.py`.

> Interrupting a transfer note (cancel / deprioritize): both work **between chunks**
> and cost nothing extra on the wire, because a download is already a sequence of
> separate chunk jobs against a resumable partial file. `DownloadItem.control` is the
> one-shot signal a *running* transfer reads at each chunk boundary
> (`DownloadManager._download` → `_interrupt`): `"cancel"` → status `cancelled`,
> `"yield"` → back to `queued` where it now sits in the list. Whatever was written
> stays, so a yielded transfer resumes from those bytes on its next turn and a
> cancelled one resumes if re-grabbed (`d`) — verified by asserting the second pass
> re-opens at the partial offset, not 0.
> * `cancel(item)` returns what it did — `"cancelled"` (queued: dropped before it ever
>   opens the file), `"stopping"` (running) or `"cleared"` (a finished/errored entry,
>   removed from the list). One key, `x`, covers "get rid of this".
> * `reorder` now moves *pending* entries (not just queued ones) and enforces one
>   invariant — **the wire belongs to the first pending entry** — via
>   `_preempt_if_displaced()`. So `J` on the running transfer and `K` on the queued one
>   behind it are the same operation from either side: the big file yields, the small
>   one goes now. With nothing to swap with, `reorder` returns False and the UI says so.
> Caveat: on **FTP** a handle closed before EOF drains the rest of the data connection
> (see the Phase 9 note), so an interrupted FTP transfer still costs its remaining
> bytes; SMB/SFTP close immediately. Tested in `tests/test_download_control.py`
> (chunk-level `ChunkGate` fixture in conftest holds a transfer mid-file) plus the UI
> wiring in `tests/test_ui_downloads.py`.

> Status-note note: `_status_note` messages (copied, cancelled, yielded…) are held for
> `SmbexApp.NOTE_SECONDS` (4 s) in `self._note` and re-rendered by `_update_status`.
> Without the hold the very next repaint — download progress, or the panel refresh
> that follows the action being reported — wiped the message before it could be read.
> A link-state change clears the note, so the reconnect banner is never hidden.

> File viewer note: `l`/`Enter`/`Right` on a **downloaded text** file opens the content
> viewer (`SmbexApp._enter_file_view`, `_FileViewState`, `smbex/viewer.py` `LazyLines`).
> The parent column hides; the content fills `#current`+`#preview`. Translation off →
> two-page view (middle = this screen, right = the next). Translation on → middle =
> original, right = English, line-aligned and translated **lazily** per visible window
> in an exclusive worker (`_translate_view_window`), cached by line index. `LazyLines`
> reads only enough of the file to fill the window (+ scroll history), never the whole
> file unless you jump to the end (`G`). Height comes from the `#columns` container
> (the columns auto-size to content, so their own size can't drive the window); re-fit
> on `on_resize`. `j/k` scroll, `PgUp/PgDn` page, `g/G` top/bottom, `h/Esc` back.
> A **binary** file opens instead as a scrollable xxd **hex** view (`LazyHex`, seeks
> straight to each 16-byte window, so a huge binary opens instantly; no translation,
> two-page). Undownloaded files don't enter the viewer (a status hint says why); the
> download behaviour is unchanged. `Column.show_lines(gutter=…)` renders a line-number
> gutter (off for hex rows, which carry their own offset). Tested in
> `tests/test_viewer.py`. The demo server (`scripts/demo_server.py`) seeds multi-screen
> Japanese `.txt` docs and real image/binary files (写真/, 素材/) to exercise both modes.

> Preview note: when the selected file is fully downloaded (`_downloaded_local_path`:
> mirror file exists and complete), the preview pane shows its **local** content via
> `smbex/preview.py` — text as-is, binary as an xxd-style hex dump — reading only a
> bounded prefix (64 KB text / 2 KB hex) so a huge file can't stall the UI. With
> translate on, `_maybe_translate_preview` translates the first ~40 lines off the event
> loop in an *exclusive* worker (switching files cancels a stale one) and appends a
> "── translation ──" section. Not-downloaded files show metadata only. Tested in
> `tests/test_preview.py`.

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
panel (then `j`/`k` select, `K`/`J` reprioritize — crossing the running transfer
pauses it — `x` cancel/clear, `w`/`h`/`Esc` close), `o` cycle sort (name→newest→oldest), `p` preload toggle (prefetches
surrounding folders), `r` reconnect (after a dropped link; auto only with
`--auto-reconnect`), `t` translate toggle (English beside originals; needs
`--translate <lang>`), `T` cycle colour theme (dark/light/nord/gruvbox),
`[`/`]` show/hide the parent/preview column, `?` help overlay, `q` quit.

## Install & environment

Dev was done on Fedora 44 / Python 3.14 (a 3.13-built `.venv` breaks when Fedora
retires the interpreter — rebuild it, don't debug it). Deploy target is **Kali**, where the user
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
| pyftpdlib | `python3-pyftpdlib` | 2.0.x | 2.2.0 | **dev/test only** — in-process FTP server for the FTP integration tests. FTP runtime itself is stdlib `ftplib` (no dep). |
| ctranslate2 | **none** | — | 4.8.2 | **NOT in apt.** Translation inference engine; small wheel. |
| sentencepiece | `python3-sentencepiece` | 0.2.x | 0.2.2 | Tokeniser. In apt, but pip-installing it with ctranslate2 is simplest. |

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
.venv/bin/python -m pytest -m integration     # real local SMB/SSH/FTP servers
.venv/bin/python -m smbex --version
.venv/bin/python -m smbex --help              # ends with the QUICKSTART epilog (cli.QUICKSTART)
```

**Release**: `bash scripts/make_release.sh` writes `dist/smbex.pyz` (single-file
zipapp; bundles only smbex's pure-Python code — deps stay apt/pip on the target),
`dist/smbex-<ver>.tar.gz` (source), and `dist/QUICKSTART.txt`. `smbex --help`'s
epilog is `cli.QUICKSTART` (connect / config / one-time translation-model setup with
the Argos index URL) — a single source of truth reused by the release script.

## Architecture

Two load-bearing seams keep this testable and responsive:

1. **Backend abstraction** (`smbex/backend/base.py`) — a protocol with
   `roots() / list() / stat() / open_read() / open_file()` over a single POSIX path.
   Implementations: `impacket_backend.py` (SMB; first path component = share),
   `ssh_backend.py` (paramiko/SFTP), `ftp_backend.py` (stdlib ftplib; FTP/FTPS),
   `fake_backend.py` (in-memory tree for fast offline tests), and `mux.py`'s
   `MuxBackend` (subclasses `ssh_backend` to ride an existing OpenSSH ControlMaster
   socket — SFTP over the system `ssh` client instead of a paramiko connection).
   Everything above the backend is protocol-agnostic.
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
    ftp_backend.py       ✓ FTP/FTPS via stdlib ftplib (MLSD listings; REST+RETR reads)
  gateway.py             ✓ async priority-queue gateway (browse preempts download; reconnect/retry)
  cache.py               ✓ in-memory, session-only listing cache
  config.py              ✓ INI config (~/.config/smbex/config.ini); built-in<config<CLI
  preview.py             ✓ bounded text/hex preview of a downloaded file
  viewer.py              ✓ windowed lazy readers for the content viewer (LazyLines / LazyHex)
  browser.py             ✓ ranger navigation controller (cache-backed, cursor memory)
  download.py            ✓ background DownloadManager (resume/skip, mirror, throttled; one handle/file)
  preload.py             ✓ surrounding-folder preloader (PRELOAD-priority, toggle-gated)
  translate.py           ✓ local filename translation (CTranslate2 + SentencePiece; lazy)
  mux.py                 ✓ ride an existing SSH ControlMaster socket (--mux): PipeChannel + discovery + MuxBackend
  ui/
    app.py               ✓ Textual ranger UI (parent|current|preview, dark default)
    columns.py           ✓ Miller-column widget
    downloads.py         ✓ download/task panel (progress bars)
    help.py              ✓ '?' help overlay (ModalScreen, keybindings by group)
    mux_picker.py        ✓ '--mux' control-socket picker (pre-launch selection app)
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
