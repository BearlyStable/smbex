#!/usr/bin/env bash
# Build transferable release artifacts into ./dist:
#
#   smbex.pyz            single-file zipapp — copy it to the target and run
#                          python3 smbex.pyz 'user:pass@host'
#                        (needs python3 + the runtime deps: on Kali,
#                          sudo apt install python3-impacket python3-paramiko python3-textual)
#   smbex-<ver>.tar.gz   source tarball (smbex/, scripts/, docs, pyproject)
#   QUICKSTART.txt       the connect/config/translation quickstart
#
# smbex is pure Python and runs from source (no build step), so the zipapp bundles
# only smbex's own code — the deps stay apt/pip-provided on the target.
set -euo pipefail

cd "$(dirname "$0")/.."  # repo root
PY="${PYTHON:-python3}"
VER="$("$PY" -c 'import smbex; print(smbex.__version__)' 2>/dev/null || echo 0.0.0)"
DIST="dist"

echo ">> building smbex $VER release into $DIST/"
rm -rf "$DIST"
mkdir -p "$DIST"

# --- 1) single-file zipapp -------------------------------------------------
stage="$(mktemp -d)"
cp -r smbex "$stage/"
find "$stage" -name __pycache__ -type d -prune -exec rm -rf {} +
"$PY" -m zipapp "$stage" \
    --main "smbex.cli:main" \
    --python "/usr/bin/env python3" \
    --output "$DIST/smbex.pyz"
chmod +x "$DIST/smbex.pyz"
rm -rf "$stage"
echo "   wrote $DIST/smbex.pyz ($(du -h "$DIST/smbex.pyz" | cut -f1))"

# --- 2) source tarball -----------------------------------------------------
root="smbex-$VER"
tstage="$(mktemp -d)"
mkdir -p "$tstage/$root"
cp -r smbex scripts README.md CLAUDE.md pyproject.toml "$tstage/$root/"
find "$tstage/$root" -name __pycache__ -type d -prune -exec rm -rf {} +
"$PY" -c 'from smbex.cli import QUICKSTART; print(QUICKSTART)' > "$tstage/$root/QUICKSTART.txt"
tar -czf "$DIST/$root.tar.gz" -C "$tstage" "$root"
rm -rf "$tstage"
echo "   wrote $DIST/$root.tar.gz ($(du -h "$DIST/$root.tar.gz" | cut -f1))"

# --- 3) standalone quickstart ----------------------------------------------
"$PY" -c 'from smbex.cli import QUICKSTART; print(QUICKSTART)' > "$DIST/QUICKSTART.txt"
echo "   wrote $DIST/QUICKSTART.txt"

cat <<EOF

done. to deploy on the target (e.g. Kali):
  sudo apt install python3-impacket python3-paramiko python3-textual
  python3 smbex.pyz --help            # or: ./smbex.pyz --help
  python3 smbex.pyz 'demo:demo@127.0.0.1' --port 4455
Optional translation:  pip install ctranslate2 sentencepiece && python3 smbex.pyz --install-lang ja
EOF
