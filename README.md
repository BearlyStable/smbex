# smbex

A terminal (TUI) explorer for remote hosts over **SMB**, **SSH/SFTP**, and
**FTP/FTPS**, built for slow links: ranger-style navigation, in-session listing
cache, background downloads that yield to browsing, folder preloading, and offline
filename translation.

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

Filename translation is optional and needs two small wheels (**not** in apt) —
CTranslate2 + SentencePiece (~65 MB, no torch/CUDA). Add them in a venv that
inherits the apt packages, then fetch a language model (one `.argosmodel` file):

```sh
python3 -m venv --system-site-packages ~/.venvs/smbex
~/.venvs/smbex/bin/pip install ctranslate2 sentencepiece
~/.venvs/smbex/bin/python -m smbex --install-lang ja   # one-time, online: ja->en model (~130 MB)
```

Translation runs entirely on this machine (no filename leaves the box); the only
network use is that one-time model download. See CLAUDE.md → *Install* for details
(offline `--model-file`, reusing existing Argos models, etc.).

### Development (any distro, venv)

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

## Run

```sh
# SMB, impacket-smbclient-style target (password is prompted if omitted):
python3 -m smbex 'DOMAIN/user:password@host'
python3 -m smbex -H :NThash --no-pass 'DOMAIN/user@host'          # pass-the-hash
python3 -m smbex -k --dc-ip 10.0.0.1 'DOMAIN/user@host.domain'    # Kerberos (ccache)

# SSH/SFTP (host keys auto-accepted by default; --strict-host-keys to verify):
python3 -m smbex 'ssh://user@host'
python3 -m smbex -i ~/.ssh/id_ed25519 'ssh://user@host:2222/var/log'

# FTP / FTPS (no user -> anonymous; ftps:// for TLS):
python3 -m smbex 'ftp://user@host'
python3 -m smbex 'ftps://user:pass@host:21/pub'
```

Navigate like ranger: `h/j/k/l` (or arrows), `g`/`G` for top/bottom, `q` to quit.
Listings show a compact age (`3d`, `2w`) and file sizes; `o` cycles the sort
(name → newest → oldest). A status gutter marks folders whose listing is cached
(`·`), and files/folders queued (`↓`) / downloaded (`✓`).
Download in the background: `d` (selected file, or a folder recursively), `a` (all
files in the current folder), `w` (show/hide the task panel). Downloads mirror the
remote tree under `DIR/<host>` (`--download-dir`, default `./downloads`), resume
partial files, skip complete ones, and yield to browsing so navigation stays snappy.

Optional extras: `--preload` prefetches surrounding folders (toggle with `p`);
`--translate <lang>` shows English filename translations beside the originals
(toggle with `t`), computed on-box — see *Install* for the one-time model setup.
Press `?` for the key reference. If a link drops it's reported and you press `r`
to reconnect (a deliberate new login); `--auto-reconnect` heals silently instead.
`--theme <name>` (dark/light/nord/gruvbox) sets the colour theme; `T` switches it.
`[` / `]` hide the parent / preview column to save space. A downloaded file's
content shows in the preview pane (text, or an xxd-style hex dump for binaries).
Press `l`/`Enter` on a downloaded file to open a full content viewer: text scrolls
with `j/k` (loaded lazily, so large files open instantly) and, with translation on,
shows the original and English **side by side**; a binary opens as a scrollable
xxd-style **hex** view. `h`/`Esc` goes back.

Defaults live in a config file (`~/.config/smbex/config.ini`; flags override it):

```sh
python3 -m smbex --write-config          # drop a commented sample, then edit it
```

`smbex --help` ends with a **quickstart** covering connecting, the config file, and
the one-time translation-model setup (with the model index URL).

## Build a release to transfer

`scripts/make_release.sh` writes transferable artifacts to `dist/`:

```sh
bash scripts/make_release.sh
```

- **`dist/smbex.pyz`** — a single-file [zipapp](https://docs.python.org/3/library/zipapp.html).
  Copy it to the target and run it; it needs only `python3` plus the runtime deps
  (on Kali: `sudo apt install python3-impacket python3-paramiko python3-textual`):

  ```sh
  python3 smbex.pyz 'demo:demo@127.0.0.1' --port 4455   # or: ./smbex.pyz
  ```
- **`dist/smbex-<ver>.tar.gz`** — the source tree (run from it with `python3 -m smbex`).
- **`dist/QUICKSTART.txt`** — the same quickstart shown at the end of `--help`.

## Try it out (local demo, no root)

Terminal 1 — start a throwaway SMB server with sample files on a high port:

```sh
python3 scripts/demo_server.py
```

Terminal 2 — connect the client (any username/password is accepted):

```sh
python3 -m smbex 'demo:demo@127.0.0.1' --port 4455
# with Japanese filename translation (needs: --install-lang ja), then press 't':
python3 -m smbex --translate ja 'demo:demo@127.0.0.1' --port 4455
```

The demo share has a `日本語/` folder of Japanese-named files and folders (写真/,
仕事/, 地図.png, …) so you can watch them render as `写真 → Photos` with `t`.

## Test

```sh
.venv/bin/python -m pytest -q          # unit + UI tests
.venv/bin/python -m pytest -m integration   # local SMB/SSH server tests (Phase 1+)
```

## Protocol fidelity — how smbex differs from the standard tools

smbex builds on the same libraries the reference tools use (`impacket` for SMB,
`paramiko` for SSH/SFTP, stdlib `ftplib` for FTP), so the **packets themselves are
well-formed the same way** — smbex does not hand-roll protocol framing. The
differences below are all in *usage patterns* (command sequences, frequency, trust
decisions), not in the bytes on the wire. They are documented here so you can weigh
the risk against your own hosts, especially fragile or tightly-audited ones.

### SMB — per-file tree-connect churn
smbex issues one `TREE_CONNECT`/`TREE_DISCONNECT` **per file**, where
impacket-smbclient and the Windows redirector connect a share **once** and reuse
that tree for the whole session. Each `open_file` does `connectTree` → `openFile`
and its close tears both down (`smbex/backend/impacket_backend.py`). Downloading 500
files is 500 tree cycles here vs. 1 there.

- **Why it's written this way:** statelessness. A cached tree id (`tid`) goes stale
  after a dropped link (see the reconnect design), so by never holding one across
  operations we can't reuse a dead `tid`. This is a *simplicity* choice, not a
  performance one — there is no throughput advantage.
- **Cost:** on a host with SMB auditing this multiplies tree-connect events; it is
  slightly more churn than a normal client. It is not malformed and cannot desync the
  session.
- **If it matters:** cache `{share: tid}` on the backend, connect-on-first-use, and
  invalidate it in `reconnect()` — a small, self-contained change that restores
  one-tree-per-share parity.

### SMB — `stat()` uses a directory FIND, not a file QUERY_INFO
`stat()` reads a single file's metadata via `listPath` (an SMB2 `QUERY_DIRECTORY`
against the parent, filename as the search pattern) rather than opening the file and
issuing `QUERY_INFO`.

- **Why:** SMB2 has no "getattr by path" — a real `QUERY_INFO` requires first
  **opening the file** (`CREATE`), which emits a file-**access** audit event and
  participates in sharing-mode/oplock negotiation (you can collide with a process
  holding the file open). The directory FIND reads the metadata out of the parent's
  enumeration **without ever opening the file**. On a sensitive host this is the
  lighter, less-intrusive touch, and it matches impacket-smbclient.
- **Cost:** it is a directory-query op rather than a handle getinfo, and (today) it
  carries a tree-connect cycle with it (see above).

### SSH — default host-key policy is trust-on-first-use, not persisted
The default `auto` policy (`smbex/backend/ssh_backend.py`) **silently accepts an
unknown host key** and keeps it only in memory. OpenSSH `ssh`/`scp` instead *prompt*
on an unknown key (and refuse in batch mode), writing accepted keys to
`~/.ssh/known_hosts`.

- A **changed** key is still rejected (paramiko raises regardless of policy), so the
  exposure is limited to unknown/first-contact hosts (MITM-susceptible on first
  connect). Because nothing is written to `known_hosts`, every run is "first contact".
- **If it matters:** pass `--strict-host-keys` (policy `strict`) to require a
  pre-known key, matching default OpenSSH behavior.

### SSH — SFTP subsystem, not scp
smbex speaks the **SFTP subsystem** (`sftp-server`), not the scp protocol. SFTP was
chosen because it supports listing, `stat`, and seekable/resumable reads — the things
the browser, resumable downloads, and the lazy preview/viewer need; plain scp is a
transfer-only subset. On a host where the sftp subsystem is disabled
(`Subsystem sftp` removed, a `ForceCommand`/restricted shell permitting only scp, or
old dropbear builds without sftp-server), smbex will fail where `scp` works. See
**"scp support"** below for the evaluation of adding an scp backend.

### SSH — listings probe symlink targets
When listing a directory, smbex issues an extra `STAT` on each **symlink target** to
decide whether a link is a directory (`_to_entry`, `smbex/backend/ssh_backend.py`).
`ls`/`sftp` use the `lstat` attrs already returned by readdir and do **not** follow
the link.

- **Why:** so directory-symlinks are browsable (you can `l` into them).
- **Cost:** extra round trips, and it touches paths you did not navigate to. `stat`
  does not *open* the target, so the risk is low, but on a sensitive host a link could
  point at a device node, an automount trigger, or a monitored path.

### FTP — early-stopped reads drain the rest of the file instead of `ABOR`
A normal FTP client that stops a `RETR` early sends `ABOR`. smbex instead **reads the
data connection to its natural EOF** and then consumes the single clean `226`
(`_drain_and_finish`, `smbex/backend/ftp_backend.py`).

- **Why FTP forces the choice:** FTP splits **control** (commands/responses) from
  **data** (file bytes), and the session invariant is that *every control response
  must be read by the command that provoked it* — one response left unread and the
  control channel is off-by-one and wedged. `ABOR` is genuinely awkward: it is
  preceded by an out-of-band Telnet interrupt (`IAC`+`IP`, then Synch) and the server
  then emits **two** control responses in a server-dependent order (typically `426`
  *and* `226`; some do `226`+`225`). Real clients (`lftp`, BSD `ftp`) implement all of
  that; **`ftplib`'s `abort()` does not** and is known to leave the control channel
  desynced against many servers. Draining keeps the control channel provably in sync.
- **The trade-off:** correctness/robustness over bandwidth. For a fragile server the
  drain is the **conservative** choice — it never leaves the control channel in a weird
  state (the thing that wedges FTP sessions). The cost is bandwidth: previewing or
  seeking within a **large** file can pull the rest of that file. It only triggers when
  you stop **early** — a normal download-to-EOF closes cleanly with no drain.
- **If the bandwidth ever bites:** the alternative is *not* ftplib's flaky `abort()` —
  it is to drop and reopen the whole control connection to abandon the transfer, which
  is clean but makes a fresh login/audit event each time.

### FTP — passive mode + `MLSD`
`ftplib` defaults to passive mode (`PASV`/`EPSV`) and smbex prefers `MLSD` for
listings (falling back to `LIST`). The classic `ftp` command defaults to *active* mode
and uses `LIST`/`NLST`. Both smbex choices match modern clients (`lftp`); if your
baseline is the traditional `ftp` client, expect a different data-connection direction
(firewall-visible) and `MLSD` where you would see `LIST`. `TYPE I` is also re-sent
before every `RETR` (extra but valid — MLSD/LIST leave the session in ASCII mode).

### Reconnect produces additional login/auth events
By design, a dropped link plus `r` (or `--auto-reconnect`) performs a **fresh login**,
so one smbex "session" can show multiple logins from the same source — unlike a single
long-lived client session. `--auto-reconnect` makes these re-logins silent. Relevant
for audit correlation.

### scp support (evaluated, not implemented)
Adding scp as a peer protocol is technically possible (paramiko `exec_command` running
`scp -f`) but fits smbex's model **poorly**, so it is intentionally **not** built:

- **No listing or stat.** The scp protocol only transfers files/trees; browsing would
  require shelling out to remote `ls`/`find` over an exec channel — i.e. **running
  commands** on the host (a larger footprint than SFTP's read-only file ops, and
  shell/PATH-dependent). The ranger UI is built on `list()`/`stat()`, which scp lacks.
- **No seek/resume.** scp streams a whole file start-to-finish, so resumable downloads,
  the lazy preview, and the windowed viewer (all of which seek) cannot work.
- **Shrinking need.** OpenSSH 9.0 (2022) deprecated the scp protocol and made the `scp`
  command use SFTP under the hood, so on modern hosts "scp" already *is* sftp.

Where it would earn its place: appliances/embedded gear (e.g. **dropbear** without
sftp-server) or hardened boxes that permit scp but not the sftp subsystem. If such
targets exist in your fleet, the honest shape is **not** a browsing peer backend but a
restricted **download-only** mode (transfer via scp, browse via remote `ls`), with no
resume/preview. Recommendation: don't build it speculatively — revisit only if you can
enumerate real sftp-disabled hosts.
