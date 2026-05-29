#!/usr/bin/env bash
# Wrap dist/Breakthrough.app into a compressed, shareable .dmg.
#
# Prereq: bash packaging/build.sh has already produced dist/Breakthrough.app.
# Output: dist/Breakthrough-<version>.dmg (UDZO, openable on any Mac).
#
# Unsigned: recipients hit Gatekeeper on first launch ("can't be verified").
# They right-click -> Open once to clear it. For a clean launch experience on
# other Macs, sign + notarize per packaging/DISTRIBUTION.md before sharing.
set -euo pipefail

cd "$(dirname "$0")/.."

APP="dist/Breakthrough.app"
[ -d "$APP" ] || { echo "error: $APP not found. Run: bash packaging/build.sh" >&2; exit 1; }

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
DMG="dist/Breakthrough-${VERSION}.dmg"

echo "==> Creating $DMG from $APP"
rm -f "$DMG"
hdiutil create \
  -volname "Breakthrough" \
  -srcfolder "$APP" \
  -ov \
  -format UDZO \
  "$DMG" >/dev/null

echo "==> Done: $DMG ($(du -h "$DMG" | cut -f1))"
echo "    Share it. Recipients: open the .dmg, drag Breakthrough.app to /Applications,"
echo "    right-click -> Open the first time to clear Gatekeeper (unsigned build)."
