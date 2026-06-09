#!/usr/bin/env bash
# Package dist/Misanthropic.app into a styled, drag-to-install .dmg.
#
# Prereq: bash packaging/build.sh has produced dist/Misanthropic.app.
# Output: dist/Misanthropic-<version>.dmg — opens to a window with the app icon
#         and an "Applications" alias, so installing is one drag (like Chrome).
#
# Self-contained (hdiutil + Finder via AppleScript). The Finder layout step needs
# a GUI login session; if it can't run (headless/SSH/CI), the DMG is still built
# with the Applications symlink — you can drag to install, just without the
# positioned background.
#
# Unsigned/ad-hoc builds still hit Gatekeeper on first launch on other Macs
# ("can't be verified") — recipients use System Settings -> Privacy & Security ->
# Open Anyway, or `xattr -dr com.apple.quarantine` the app. Sign + notarize
# (packaging/DISTRIBUTION.md) for a clean double-click.
set -euo pipefail

cd "$(dirname "$0")/.."

APP="dist/Misanthropic.app"
VOL="Misanthropic"
BG="packaging/icons/dmg-background.png"
[ -d "$APP" ] || { echo "error: $APP not found. Run: bash packaging/build.sh" >&2; exit 1; }

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
DMG="dist/Misanthropic-${VERSION}.dmg"
RW="dist/.misanthropic-rw.dmg"
MNT="/Volumes/$VOL"

cleanup() { hdiutil detach "$MNT" >/dev/null 2>&1 || true; rm -f "$RW"; }
trap cleanup EXIT
hdiutil detach "$MNT" >/dev/null 2>&1 || true   # in case a prior run left it mounted

echo "==> Creating writable image"
STAGE_KB=$(( $(du -sk "$APP" | cut -f1) + 40000 ))   # app + ~40MB slack
rm -f "$RW" "$DMG"
hdiutil create -srcfolder "$APP" -volname "$VOL" -fs HFS+ \
  -format UDRW -size "${STAGE_KB}k" "$RW" >/dev/null

echo "==> Mounting and laying out the window"
hdiutil attach "$RW" -mountpoint "$MNT" -readwrite -noverify -noautoopen >/dev/null
ln -sf /Applications "$MNT/Applications"
if [ -f "$BG" ]; then
  mkdir -p "$MNT/.background"
  cp "$BG" "$MNT/.background/bg.png"
fi

# Finder layout (best-effort — needs a GUI session).
if osascript - "$VOL" >/dev/null 2>&1 <<'OSA'
on run argv
  set volName to item 1 of argv
  tell application "Finder"
    tell disk volName
      open
      set current view of container window to icon view
      set toolbar visible of container window to false
      set statusbar visible of container window to false
      set the bounds of container window to {220, 140, 880, 540}
      set opts to the icon view options of container window
      set arrangement of opts to not arranged
      set icon size of opts to 112
      set text size of opts to 13
      try
        set background picture of opts to file ".background:bg.png"
      end try
      set position of item "Misanthropic.app" of container window to {180, 200}
      set position of item "Applications" of container window to {480, 200}
      update without registering applications
      delay 1
      close
    end tell
  end tell
end run
OSA
then
  echo "    styled layout applied"
else
  echo "    (Finder layout skipped — no GUI session; DMG still drag-installs)"
fi

sync
hdiutil detach "$MNT" >/dev/null
trap 'rm -f "$RW"' EXIT   # volume already detached

echo "==> Compressing -> $DMG"
hdiutil convert "$RW" -format UDZO -imagekey zlib-level=9 -o "$DMG" >/dev/null
rm -f "$RW"

echo "==> Done: $DMG ($(du -h "$DMG" | cut -f1))"
echo "    Recipients: open it, drag Misanthropic onto Applications."
echo "    Unsigned first launch: System Settings -> Privacy & Security -> Open Anyway."
