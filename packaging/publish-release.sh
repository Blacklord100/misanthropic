#!/usr/bin/env bash
# Publish a release to the public repo so installed apps can detect + download
# updates. Single-repo setup: source, releases, and appcast.json all live in one
# public repo (default: Blacklord100/misanthropic).
#
# Usage:  bash packaging/publish-release.sh [version]
#         (version defaults to misanthropic.__version__)
#
# Prereqs:
#   - `gh` authenticated with push access to the repo.
#   - dist/Misanthropic-<version>.dmg already built (build.sh + make_dmg.sh).
#   - optional: dist/misanthropic-<version>-py3-none-any.whl + .tar.gz (python -m build)
#     for the pipx install path.
#
# Effect:
#   1. Creates/updates release v<version>, attaching the .dmg (+ wheel/sdist if present),
#      with auto-generated install instructions in the body.
#   2. Commits/pushes appcast.json at the repo root — the manifest the app polls.
set -euo pipefail

cd "$(dirname "$0")/.."

REPO="${MISANTHROPIC_PUBLIC_REPO:-Blacklord100/misanthropic}"
BRANCH="${MISANTHROPIC_RELEASE_BRANCH:-master}"
VERSION="${1:-$(python3 -c 'import sys; sys.path.insert(0,"src"); from misanthropic import __version__; print(__version__)')}"

TAG="v${VERSION}"
DMG_NAME="Misanthropic-${VERSION}.dmg"
DMG="dist/${DMG_NAME}"
WHL="dist/misanthropic-${VERSION}-py3-none-any.whl"
SDI="dist/misanthropic-${VERSION}.tar.gz"
[ -f "$DMG" ] || { echo "error: $DMG not found. Build it first: bash packaging/build.sh && bash packaging/make_dmg.sh" >&2; exit 1; }

SHA256="$(shasum -a 256 "$DMG" | awk '{print $1}')"
DOWNLOAD_PAGE="https://github.com/${REPO}/releases/tag/${TAG}"
DMG_URL="https://github.com/${REPO}/releases/download/${TAG}/${DMG_NAME}"
WHL_URL="https://github.com/${REPO}/releases/download/${TAG}/$(basename "$WHL")"

# A versionless copy so releases/latest/download/Misanthropic.dmg is a stable URL.
STABLE_DMG="dist/Misanthropic.dmg"
cp -f "$DMG" "$STABLE_DMG"
ASSETS=("$DMG" "$STABLE_DMG")
[ -f "$WHL" ] && ASSETS+=("$WHL")
[ -f "$SDI" ] && ASSETS+=("$SDI")

CHANGE="$(awk -v tag="## ${TAG}" 'index($0,tag)==1{f=1;next} /^## v/{if(f)exit} f' CHANGELOG.md || true)"
[ -n "$CHANGE" ] || CHANGE="Misanthropic ${VERSION}"

ONELINER="curl -fsSL ${DMG_URL} -o /tmp/m.dmg && hdiutil attach /tmp/m.dmg -nobrowse -quiet && cp -R \"/Volumes/Misanthropic/Misanthropic.app\" /Applications/ && hdiutil detach \"/Volumes/Misanthropic\" -quiet && open /Applications/Misanthropic.app"

# Build the release body in a file (top-level heredocs — avoids quoting traps).
NOTES_FILE="$(mktemp)"
trap 'rm -f "$NOTES_FILE"' EXIT
cat > "$NOTES_FILE" <<EOF
## Install

**Requirements:** macOS 11+, and the [\`claude\`](https://docs.claude.com/en/docs/claude-code) CLI installed and logged in (the app uses *your own* Claude login).

### ⚡ Fast install — one line

Paste this into **Terminal**. It downloads the app, installs it, and opens it:

\`\`\`bash
${ONELINER}
\`\`\`

### Option A — download the .dmg by hand

Download **${DMG_NAME}** below, open it, and drag the skull onto **Applications**. The app is signed with a Developer ID and notarized by Apple, so it opens with a normal double-click — no Gatekeeper warning.
EOF

if [ -f "$WHL" ]; then
cat >> "$NOTES_FILE" <<EOF

### Option B — no app (pipx)

Best for CLI/server use:

\`\`\`bash
pipx install "misanthropic[app] @ ${WHL_URL}"
misanthropic-app      # menu-bar app   (or:  misanthropic serve)
\`\`\`
EOF
fi

cat >> "$NOTES_FILE" <<EOF

---

${CHANGE}
EOF

echo "==> Publishing $TAG to: $REPO"
echo "    assets: ${ASSETS[*]}"
echo "    sha256: $SHA256"

if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  gh release upload "$TAG" "${ASSETS[@]}" --repo "$REPO" --clobber
  gh release edit "$TAG" --repo "$REPO" --notes-file "$NOTES_FILE"
else
  gh release create "$TAG" "${ASSETS[@]}" --repo "$REPO" \
    --title "Misanthropic ${VERSION}" --notes-file "$NOTES_FILE"
fi

# Write/refresh appcast.json at the repo root (this working tree IS the repo).
python3 - "appcast.json" "$VERSION" "$DOWNLOAD_PAGE" "$DMG_URL" "$SHA256" "$CHANGE" <<'PY'
import json, sys
path, version, page, dmg, sha, notes = sys.argv[1:7]
manifest = {
    "version": version,
    "download_page": page,
    "dmg_url": dmg,
    "sha256": sha,
    "notes": notes.strip()[:500],
    "min_macos": "11.0",
}
with open(path, "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
PY
git add appcast.json   # stage first so the diff check also catches a brand-new file
if git diff --cached --quiet -- appcast.json; then
  echo "==> appcast.json already current"
else
  git commit -q -m "appcast: v${VERSION}"
  git push -q origin "$BRANCH"
  echo "==> appcast.json updated + pushed"
fi

echo "==> Done."
echo "    Feed: https://raw.githubusercontent.com/${REPO}/${BRANCH}/appcast.json"
echo "    Page: ${DOWNLOAD_PAGE}"
