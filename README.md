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
