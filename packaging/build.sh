#!/usr/bin/env bash
# Build Breakthrough.app. Run from the repo root: bash packaging/build.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Creating build venv"
python3 -m venv .build-venv
# shellcheck disable=SC1091
source .build-venv/bin/activate

echo "==> Installing app + build deps"
pip install --quiet --upgrade pip
pip install --quiet -e ".[app]" py2app

echo "==> Building Breakthrough.app"
rm -rf build dist
python packaging/setup_app.py py2app

echo "==> Done: dist/Breakthrough.app"
echo "    Try it:  open dist/Breakthrough.app"
echo "    (First launch on another Mac (macOS 15+): System Settings -> Privacy &"
echo "     Security -> Open Anyway, or: xattr -dr com.apple.quarantine the .app;"
echo "     or sign + notarize per packaging/DISTRIBUTION.md before sharing.)"
