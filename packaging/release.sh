#!/usr/bin/env bash
# Generate (and optionally publish) a desktop release end to end.
#
#   bash packaging/release.sh             # build the app + styled .dmg
#   bash packaging/release.sh --publish   # ...then cut the GitHub release + appcast
#
# "Update" = bump the version (src/misanthropic/__init__.py), then re-run this.
# The menu-bar app's auto-update check picks up the new appcast and tells users.
set -euo pipefail

cd "$(dirname "$0")/.."

bash packaging/build.sh
bash packaging/make_dmg.sh
if [ "${1:-}" = "--publish" ]; then
  bash packaging/publish-release.sh
fi

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' dist/Misanthropic.app/Contents/Info.plist)"
echo "==> Release artifact: dist/Misanthropic-${VERSION}.dmg"
