#!/usr/bin/env bash
# Publish a release to the PUBLIC feed repo so installed apps can detect + download
# updates. The source repo stays private; only the distributable build is public.
#
# Usage:  bash packaging/publish-release.sh [version]
#         (version defaults to misanthropic.__version__)
#
# Prereqs:
#   - `gh` authenticated with push access to the public feed repo.
#   - dist/Misanthropic-<version>.dmg already built (build.sh + make_dmg.sh).
#
# Effect:
#   1. Creates/updates release v<version> in the public repo, attaching the .dmg.
#   2. Writes appcast.json (version, download_page, dmg_url, sha256, notes) to the
#      public repo's default branch — the manifest the app polls.
set -euo pipefail

cd "$(dirname "$0")/.."

PUBLIC_REPO="${MISANTHROPIC_PUBLIC_REPO:-Blacklord100/misanthropic-releases}"
VERSION="${1:-$(python3 -c 'import sys; sys.path.insert(0,"src"); from misanthropic import __version__; print(__version__)')}"

TAG="v${VERSION}"
DMG_NAME="Misanthropic-${VERSION}.dmg"
DMG="dist/${DMG_NAME}"
[ -f "$DMG" ] || { echo "error: $DMG not found. Build it first: bash packaging/build.sh && bash packaging/make_dmg.sh" >&2; exit 1; }

SHA256="$(shasum -a 256 "$DMG" | awk '{print $1}')"
DOWNLOAD_PAGE="https://github.com/${PUBLIC_REPO}/releases/tag/${TAG}"
DMG_URL="https://github.com/${PUBLIC_REPO}/releases/download/${TAG}/${DMG_NAME}"

# Release notes: the matching section of CHANGELOG.md (best-effort, may be empty).
NOTES="$(awk -v tag="## ${TAG}" 'index($0,tag)==1{f=1;next} /^## v/{if(f)exit} f' CHANGELOG.md || true)"
[ -n "$NOTES" ] || NOTES="Misanthropic ${VERSION}"

echo "==> Publishing $TAG to public feed repo: $PUBLIC_REPO"
echo "    dmg:    $DMG ($(du -h "$DMG" | cut -f1))"
echo "    sha256: $SHA256"

# Create the release (or just (re)upload the asset if the release already exists).
if gh release view "$TAG" --repo "$PUBLIC_REPO" >/dev/null 2>&1; then
  gh release upload "$TAG" "$DMG" --repo "$PUBLIC_REPO" --clobber
else
  gh release create "$TAG" "$DMG" --repo "$PUBLIC_REPO" \
    --title "Misanthropic ${VERSION}" --notes "$NOTES"
fi

# Write/refresh appcast.json on the public repo's default branch.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone --depth 1 "https://github.com/${PUBLIC_REPO}.git" "$TMP/repo" >/dev/null 2>&1
python3 - "$TMP/repo/appcast.json" "$VERSION" "$DOWNLOAD_PAGE" "$DMG_URL" "$SHA256" "$NOTES" <<'PY'
import json, sys
path, version, page, dmg, sha, notes = sys.argv[1:7]
manifest = {
    "version": version,
    "download_page": page,
    "dmg_url": dmg,
    "sha256": sha,
    "notes": notes.strip(),
    "min_macos": "11.0",
}
with open(path, "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
PY
( cd "$TMP/repo"
  git add appcast.json
  if git diff --cached --quiet; then
    echo "==> appcast.json already current"
  else
    git commit -q -m "appcast: v${VERSION}"
    git push -q
    echo "==> appcast.json updated"
  fi )

echo "==> Done."
echo "    Feed: https://raw.githubusercontent.com/${PUBLIC_REPO}/main/appcast.json"
echo "    Page: ${DOWNLOAD_PAGE}"
