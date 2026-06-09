# Distributing Misanthropic.app

The app runs on **each user's own Mac** and uses **their own** local Claude Code
login as the auth. You are shipping the tool, not your subscription — every
recipient connects their own Claude account. (You cannot, technically or within
Anthropic's terms, let other people hit *your* subscription over the network.)

Each recipient needs: macOS 11+, the `claude` CLI installed and logged in.

## TL;DR — generate / update a release

```bash
bash packaging/release.sh            # build the app + styled .dmg  -> dist/Misanthropic-<version>.dmg
bash packaging/release.sh --publish  # ...then cut the GitHub release + appcast
```

To **update**: bump the version in `src/misanthropic/__init__.py`, re-run the
command. Installed apps see the new `appcast.json` on their next check and prompt
the user to download (see *Updates* in the README). The two steps run
individually too:

```bash
bash packaging/build.sh      # -> dist/Misanthropic.app   (icon + ad-hoc sign)
bash packaging/make_dmg.sh   # -> dist/Misanthropic-<version>.dmg   (drag-to-install)
```

## The install experience

`make_dmg.sh` produces a styled disk image: opening it shows the **Misanthropic**
icon and an **Applications** alias with an arrow — the user drags one onto the
other, exactly like Chrome/Slack/etc. The window background, icon positions, and
the `/Applications` symlink are all baked into the `.dmg`.

The app icon (colored skull + clay asterisk) is generated from code:
`python packaging/icons/draw.py` re-renders the menu-bar template, the app
`.iconset`, and the DMG backdrop; `build.sh` turns the iconset into
`packaging/icons/appicon.icns` via `iconutil`. The generated assets are committed,
so a normal build needs no Pillow.

## Signing: the honest version

There are two different things, and only the second gives the "double-click, it
just opens" experience:

| | What it does | Cost |
|---|---|---|
| **Ad-hoc signing** (default in `build.sh`) | Lets the app *run at all* on Apple Silicon and keeps it internally consistent. Does **not** remove Gatekeeper's "can't be verified" prompt on other Macs. | Free |
| **Developer ID + notarization** | The app opens with a normal double-click on any Mac, no warning — like Chrome. | Apple Developer account, **$99/yr** |

### Unsigned/ad-hoc (free) — recipients do a one-time allow

First launch on someone else's Mac shows "Apple could not verify…". On **macOS
15+** the right-click→Open trick is gone; they use **System Settings → Privacy &
Security → Open Anyway**, or clear quarantine in Terminal:
`xattr -dr com.apple.quarantine /Applications/Misanthropic.app`. (macOS ≤ Sonoma:
right-click → **Open** → **Open**.) After that one allow, it launches normally.

### Developer ID + notarization (clean experience)

```bash
# 1) Build signed with your Developer ID (hardened runtime, required for notarization)
SIGN_IDENTITY="Developer ID Application: YOUR NAME (TEAMID)" bash packaging/build.sh

# 2) Notarize the app
ditto -c -k --keepParent dist/Misanthropic.app Misanthropic.zip
xcrun notarytool submit Misanthropic.zip \
  --apple-id "you@example.com" --team-id TEAMID \
  --password "app-specific-password" --wait

# 3) Staple the ticket so it verifies offline, then package
xcrun stapler staple dist/Misanthropic.app
bash packaging/make_dmg.sh
```

Notarize the `.app` *before* `make_dmg.sh` — the staple lives in the bundle the
`.dmg` wraps. (You can also notarize+staple the `.dmg` itself for belt-and-braces.)

## Python runtime note

py2app bundles a Python interpreter and the `misanthropic` package into the
`.app`, so recipients do **not** need Python installed. They do still need the
`claude` CLI — the app detects its absence on launch and tells the user.

## Caveats

- **Architecture:** py2app builds for the machine you build on. For a universal
  app supporting both Apple Silicon and Intel, build on each and/or use a
  universal2 Python. Simplest: build on Apple Silicon, note it's arm64-only.
- **GUI for the styled layout:** `make_dmg.sh` positions icons via Finder
  (AppleScript), which needs a logged-in GUI session. Over plain SSH/CI it falls
  back to a plain (still drag-installable) image — run it from a desktop session
  for the pretty layout.
- **Auto-update:** notify-only today (the app points users at the new `.dmg`);
  no silent in-place replace yet. For that, consider Sparkle.
