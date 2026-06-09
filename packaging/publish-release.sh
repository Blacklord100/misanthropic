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

ASSETS=("$DMG")
[ -f "$WHL" ] && ASSETS+=("$WHL")
[ -f "$SDI" ] && ASSETS+=("$SDI")

CHANGE="$(awk -v tag="## ${TAG}" 'index($0,tag)==1{f=1;next} /^## v/{if(f)exit} f' CHANGELOG.md || true)"
[ -n "$CHANGE" ] || CHANGE="Misanthropic ${VERSION}"

ONELINER="curl -fsSL ${DMG_URL} -o /tmp/m.dmg && hdiutil attach /tmp/m.dmg -nobrowse -quiet && cp -R \"/Volumes/Misanthropic/Misanthropic.app\" /Applications/ && hdiutil detach \"/Volumes/Misanthropic\" -quiet && xattr -dr com.apple.quarantine /Applications/Misanthropic.app && open /Applications/Misanthropic.app"

PIPX_BLOCK=""
if [ -f "$WHL" ]; then
  PIPX_BLOCK="$(cat <<EOF

### Option B — no app, no Gatekeeper (pipx)

A pip install is not a quarantined app, so there is never a warning:

\`\`\`bash
pipx install "misanthropic[app] @ ${WHL_URL}"
misanthropic-app      # menu-bar app   (or:  misanthropic serve)
\`\`\`
EOF
)"
fi

NOTES="$(cat <<EOF
## Install

**Requirements:** macOS 11+, and the [\`claude\`](https://docs.claude.com/en/docs/claude-code) CLI installed and logged in (the app uses *your own* Claude login).

### ⚡ Fast install — one line, no scary warning

Paste this into **Terminal**. It downloads the app, installs it, and opens it — skipping the macOS "can't be verified" prompt:

\`\`\`bash
${ONELINER}
\`\`\`

> **Why a warning?** The app isn't *notarized* by Apple (that needs a paid \$99/yr Developer account). It's **not** malware — macOS flags anything downloaded outside the App Store. The \`xattr -dr com.apple.quarantine\` part clears that flag so it opens normally.

### Option A — download the .dmg by hand

Download **${DMG_NAME}** below → open it → drag the skull onto **Applications**. First launch: macOS says *"Apple could not verify…"* → **System Settings → Privacy & Security → Open Anyway**. One time, then it's normal.
${PIPX_BLOCK}
---

${CHANGE}
EOF
)"

echo "==> Publishing $TAG to: $REPO"
echo "    assets: ${ASSETS[*]}"
echo "    sha256: $SHA256"

if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  gh release upload "$TAG" "${ASSETS[@]}" --repo "$REPO" --clobber
  gh release edit "$TAG" --repo "$REPO" --notes "$NOTES"
else
  gh release create "$TAG" "${ASSETS[@]}" --repo "$REPO" \
    --title "Misanthropic ${VERSION}" --notes "$NOTES"
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
if git diff --quiet -- appcast.json; then
  echo "==> appcast.json already current"
else
  git add appcast.json
  git commit -q -m "appcast: v${VERSION}"
  git push -q origin "$BRANCH"
  echo "==> appcast.json updated + pushed"
fi

echo "==> Done."
echo "    Feed: https://raw.githubusercontent.com/${REPO}/${BRANCH}/appcast.json"
echo "    Page: ${DOWNLOAD_PAGE}"
