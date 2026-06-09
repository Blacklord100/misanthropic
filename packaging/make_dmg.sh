#!/usr/bin/env bash
# Wrap dist/Misanthropic.app into a compressed, shareable .dmg.
#
# Prereq: bash packaging/build.sh has already produced dist/Misanthropic.app.
# Output: dist/Misanthropic-<version>.dmg (UDZO, openable on any Mac).
#
# Unsigned: recipients hit Gatekeeper on first launch ("can't be verified").
# On macOS 15+ they allow it via System Settings -> Privacy & Security -> Open
# Anyway (or `xattr -dr com.apple.quarantine` the .app). For a clean launch
# experience on other Macs, sign + notarize per packaging/DISTRIBUTION.md.
set -euo pipefail

cd "$(dirname "$0")/.."

APP="dist/Misanthropic.app"
[ -d "$APP" ] || { echo "error: $APP not found. Run: bash packaging/build.sh" >&2; exit 1; }

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
DMG="dist/Misanthropic-${VERSION}.dmg"

echo "==> Creating $DMG from $APP"
rm -f "$DMG"
hdiutil create \
  -volname "Misanthropic" \
  -srcfolder "$APP" \
  -ov \
  -format UDZO \
  "$DMG" >/dev/null

echo "==> Done: $DMG ($(du -h "$DMG" | cut -f1))"
echo "    Share it. Recipients: open the .dmg, drag Misanthropic.app to /Applications,"
echo "    then allow it the first time: System Settings -> Privacy & Security ->"
echo "    Open Anyway (macOS 15+), or xattr -dr com.apple.quarantine the .app."
