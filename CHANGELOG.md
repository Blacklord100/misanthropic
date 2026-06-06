# Changelog

All notable changes to Breakthrough are recorded here, newest first. Versions are
tagged in git and published as [GitHub releases](https://github.com/Blacklord100/breakthrough-cli/releases)
with the `.dmg`, `.whl`, and `.tar.gz` attached.

## v0.6.2 — 2026-06-06
- Fix: scrolling inside an expanded **Recent activity** row no longer snaps back
  to the top. The dashboard now re-renders only when the request set actually
  changes, and restores each open row's scroll position when it does.

## v0.6.1 — 2026-06-06
- The activity-log expander now shows the **full** prompt and response text
  instead of an 80-character preview (newlines preserved, wraps, scrolls). The
  toggle reads **Show full text**.
- Streamed responses are captured in full too (no more `[streamed]` placeholder).

## v0.6.0 — 2026-06-06
- **Image input support.** Standard Anthropic `image` content blocks (base64) are
  passed straight through to the model via the CLI's `--input-format stream-json`.
  Works in stateless, session, streaming, and web modes. (Previously dropped with
  a placeholder.)

## v0.5.4 — 2026-05-29
- Live request log in the dashboard — per-request rows with model, mode, tokens,
  duration, and status.

## v0.5.3 — 2026-05-29
- Fix an emoji crash in the packaged `.app`: pin subprocess UTF-8 so the
  launchd-inherited C/ASCII locale can't trip a `UnicodeDecodeError` mid-response.

## v0.5.2 — 2026-05-29
- Ship the menu-bar icon (skull + Anthropic asterisk) in the `.app` and `.dmg`.

## v0.5.1 — 2026-05-29
- Security/robustness audit fixes (HIGH/MED/LOW).

## v0.5.0 — 2026-05-29
- Opt-in web search (`BREAKTHROUGH_WEB=1`), remapped into the API's `web_search`
  content shape.
- macOS menu-bar app: supervises the server, toggles web search, opens the
  dashboard, manages per-project keys.
