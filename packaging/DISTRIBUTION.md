# Distributing Breakthrough.app

The app runs on **each user's own Mac** and uses **their own** local Claude Code
login as the auth. You are shipping the tool, not your subscription — every
recipient connects their own Claude account. (You cannot, technically or within
Anthropic's terms, let other people hit *your* subscription over the network.)

Each recipient needs: macOS 11+, the `claude` CLI installed and logged in.

## 1. Build

```bash
bash packaging/build.sh
# -> dist/Breakthrough.app
```

## 2. Run locally (you, this Mac)

```bash
open dist/Breakthrough.app
```

Unsigned apps are fine for yourself. On first launch macOS Gatekeeper may block
it — right-click the app → **Open** → **Open**.

## 3. Sign + notarize (required to share without scary warnings)

You need an Apple Developer account ($99/yr) and a "Developer ID Application"
certificate.

```bash
# a) Sign (hardened runtime is required for notarization)
codesign --deep --force --options runtime \
  --sign "Developer ID Application: YOUR NAME (TEAMID)" \
  dist/Breakthrough.app

# b) Zip and submit for notarization
ditto -c -k --keepParent dist/Breakthrough.app Breakthrough.zip
xcrun notarytool submit Breakthrough.zip \
  --apple-id "you@example.com" --team-id TEAMID \
  --password "app-specific-password" --wait

# c) Staple the ticket so it works offline
xcrun stapler staple dist/Breakthrough.app
```

Then distribute the `.app` (or wrap it in a `.dmg`). Notarized apps open with a
normal double-click on any Mac.

## 4. Python runtime note

py2app bundles a Python interpreter and the `breakthrough` package into the
`.app`, so recipients do **not** need Python installed. They do still need the
`claude` CLI — the app detects its absence on launch and tells the user.

## Caveats

- **Architecture:** py2app builds for the machine you build on. For a universal
  app supporting both Apple Silicon and Intel, build on each and/or use a
  universal2 Python. Simplest: build on Apple Silicon, note it's arm64-only.
- **Auto-update:** not included. For real distribution consider Sparkle.
