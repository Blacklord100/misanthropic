#!/usr/bin/env bash
# Build Misanthropic.app. Run from the repo root: bash packaging/build.sh
#
# Signing: by default the app is ad-hoc signed (required for it to even launch on
# Apple Silicon). To ship to other people without the Gatekeeper warning, set a
# Developer ID and it'll be signed for real:
#     SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" bash packaging/build.sh
# then notarize per packaging/DISTRIBUTION.md.
set -euo pipefail

cd "$(dirname "$0")/.."

ICONSET="packaging/icons/Misanthropic.iconset"
ICNS="packaging/icons/appicon.icns"
if [ -d "$ICONSET" ] && command -v iconutil >/dev/null 2>&1; then
  echo "==> Building app icon ($ICNS)"
  iconutil -c icns "$ICONSET" -o "$ICNS"
fi

echo "==> Creating build venv"
python3 -m venv .build-venv
# shellcheck disable=SC1091
source .build-venv/bin/activate

echo "==> Installing app + build deps"
pip install --quiet --upgrade pip
pip install --quiet -e ".[app]" py2app

echo "==> Building Misanthropic.app"
rm -rf build dist
python packaging/setup_app.py py2app

APP="dist/Misanthropic.app"
IDENTITY="${SIGN_IDENTITY:--}"   # "-" = ad-hoc
if [ "$IDENTITY" = "-" ]; then
  echo "==> Ad-hoc signing (set SIGN_IDENTITY for a Developer ID / notarizable build)"
  codesign --force --deep --sign - "$APP"
else
  echo "==> Signing with: $IDENTITY (hardened runtime)"
  # Sign every nested Mach-O individually: --deep does not traverse
  # Resources/lib, so py2app's Python extension modules keep their ad-hoc
  # signatures and notarization rejects the bundle. Per-file noise (codesign
  # prints fat-slice warnings for pyobjc universal .so files) is tolerated —
  # the strict verify below is what actually gates a broken signature.
  MACHOS="$(mktemp)"
  { find "$APP" -type f -print0 | xargs -0 file 2>/dev/null \
      | grep 'Mach-O' | cut -d: -f1 | sort -u > "$MACHOS"; } || true
  N=0
  while IFS= read -r f; do
    codesign --force --options runtime --timestamp --sign "$IDENTITY" "$f" \
      2>&1 | grep -v 'replacing existing signature' || true
    N=$((N + 1))
  done < "$MACHOS"
  rm -f "$MACHOS"
  echo "    signed $N nested Mach-O binaries"
  codesign --force --options runtime --timestamp --sign "$IDENTITY" \
    "$APP/Contents/Frameworks/Python.framework/Versions/"[0-9]* 2>/dev/null || true
  codesign --force --options runtime --timestamp --sign "$IDENTITY" "$APP"
fi
codesign --verify --deep --strict "$APP" && echo "    signature OK"

echo "==> Done: $APP"
echo "    Try it:    open $APP"
echo "    Package:   bash packaging/make_dmg.sh   ->  dist/Misanthropic-<version>.dmg"
if [ "$IDENTITY" = "-" ]; then
  echo "    Note: ad-hoc build. On another Mac, first launch needs System Settings ->"
  echo "          Privacy & Security -> Open Anyway (or: xattr -dr com.apple.quarantine the .app)."
  echo "          For a clean double-click experience, sign + notarize (packaging/DISTRIBUTION.md)."
fi
