# Changelog

All notable changes to Misanthropic are recorded here, newest first. Versions are
tagged in git and published as [GitHub releases](https://github.com/Blacklord100/misanthropic/releases)
with the `.dmg`, `.whl`, and `.tar.gz` attached.

> **Renamed in v0.8.0:** this project was previously called **Breakthrough**.
> Same trick, sharper name.

## v1.0.0 — 2026-06-10
First stable release. The Anthropic Messages API, served from your own Claude Code
login — no API key, no per-token bill. Everything that landed on the road to 1.0:

- **Drop-in Messages API** over the local `claude` CLI: non-streaming + streaming
  (SSE), `count_tokens`, all model tiers (full ids and `sonnet`/`opus`/`haiku`
  aliases), image/vision input, and faithful error shapes.
- **Per-request web search** driven by the `web_search` tool, with an `auto`/`on`/`off`
  server policy — mirrors the hosted API.
- **Key-linked sessions** for stateful, resumable conversations.
- **"Money saved" counter** — dashboard + `misanthropic savings` show what the
  hosted API would have charged.
- **macOS menu-bar app** with a generated skull icon, a styled drag-to-install
  `.dmg`, and background update checks.
- **Single public repo** (source + releases + appcast), one-command release flow,
  `pytest` suite + GitHub Actions CI, and an `AGENTS.md` for AI agents.

## v0.8.5 — 2026-06-09
- **Single repo.** Source, releases, and the update feed now live in one public
  repo (`Blacklord100/misanthropic`) instead of a separate `*-releases` repo. The
  in-app updater polls `appcast.json` at the repo root; `publish-release.sh`
  publishes the release (dmg + wheel + sdist), auto-stamps install instructions in
  the release body, and commits the appcast — all in one place. No behavior change
  to the app itself.

## v0.8.4 — 2026-06-09
- **Savings banner is always visible, and counts sub-cent usage.** It previously
  stayed hidden until the running total crossed a rounded cent — so after a fresh
  install (or a couple of small calls) it looked missing. Now the dashboard shows
  the banner as soon as the server is up (with an inviting empty state before the
  first request), and small totals display with sub-cent precision (e.g. `$0.0012`)
  instead of rounding to `$0.00`. `misanthropic savings` does the same.

## v0.8.3 — 2026-06-09
- **Real app icon + drag-to-install `.dmg`.** The app finally has its own icon
  (colored skull + clay Anthropic-asterisk, generated from code in
  `packaging/icons/draw.py`) instead of the generic py2app rocket. `make_dmg.sh`
  now produces a styled disk image — app icon on the left, an **Applications**
  alias on the right with an arrow — so installing is one drag, like Chrome.
- **One-command release flow.** `bash packaging/release.sh` builds the app and the
  styled `.dmg`; `--publish` also cuts the GitHub release and refreshes the update
  feed. `build.sh` now ad-hoc signs by default (so it runs on Apple Silicon) and
  honors `SIGN_IDENTITY` for a Developer ID / notarizable build. DISTRIBUTION.md
  rewritten with the honest signing story (ad-hoc = free but one-time Gatekeeper
  allow; Developer ID + notarization = clean double-click).

## v0.8.2 — 2026-06-09
- **"Money saved" counter.** The dashboard now shows a running total of what the
  hosted Anthropic API *would* have charged for every token you've run —
  *"You'd have paid $X on the API. Misanthropic charged you $0.00."* — all-time
  plus this month. It persists across restarts under `~/.misanthropic/savings.json`
  (the request log is in-memory and clears; this doesn't). Same figure from the
  terminal via `misanthropic savings`. List prices live in `pricing.py`.
- **Test suite + CI.** Added a `pytest` suite (`tests/`) covering the schema
  translation, model-tier mapping, web policy, sessions store, pricing, and the
  savings tally — 57 tests, no `claude` CLI required — plus a GitHub Actions
  workflow running them on Python 3.9–3.13 and building the wheel. Install with
  `pip install -e ".[dev]"` and run `pytest`.
- `python -m misanthropic` now works as an alias for the `misanthropic` command.

## v0.8.1 — 2026-06-09
- **Web search is now per-request, like the hosted API.** Previously web search
  was a single global server flag (`MISANTHROPIC_WEB=1`) that ignored the request
  body — un-faithful to the Messages API, where web search is a per-call tool. Now
  the server honors the `web_search` tool in each request's `tools` array, so real
  SDK code "just works": the same request that enables web search against
  `api.anthropic.com` enables it here.
- **`MISANTHROPIC_WEB` is now a policy, not a boolean.** `auto` (default) honors
  the per-request tool; `1`/`on` forces web on for every request (the old
  behavior — preserved as an alias); `off` is a new hard kill-switch that denies
  internet regardless of the request. The menu-bar item is now **Force web search
  on**, flipping between `auto` and `on` live.

## v0.8.0 — 2026-06-09
- **Rebrand: Breakthrough → Misanthropic.** Anthropic charges you; Misanthropic
  charges no one. Everything is renamed: the `misanthropic` command (and
  `misanthropic-app`), the `misanthropic` package, the `MISANTHROPIC_*` environment
  variables, the `~/.misanthropic` state directory, `Misanthropic.app`, and the
  GitHub repo. No behavior changed — it's the same Anthropic-compatible proxy over
  your local Claude Code login, with bolder docs.
- **Migrating from Breakthrough:** re-point your clients' base URL (unchanged:
  `http://127.0.0.1:8787`), swap `BREAKTHROUGH_*` env vars for `MISANTHROPIC_*`, and
  move any state from `~/.breakthrough` to `~/.misanthropic` (or set
  `MISANTHROPIC_HOME=~/.breakthrough` to keep using the old directory). Reinstall the
  package/app under the new name.

## v0.7.2 — 2026-06-06
- **Fix: "`claude` CLI not found on PATH" when the app is launched from Finder or
  at login.** macOS gives a launched app a minimal PATH that omits Homebrew
  (`/opt/homebrew/bin`), `~/.local/bin`, npm globals, and node-version-manager
  dirs — so the bundled server couldn't find `claude` even though your terminal
  can. The server now discovers `claude` via the current PATH, then your login
  shell, then common install locations, and runs it with an augmented PATH. Set
  `CLAUDE_BIN` to override.

## v0.7.1 — 2026-06-06
- **Any Anthropic model id "just works."** Full ids like
  `claude-3-5-sonnet-20241022` / `claude-sonnet-4-6` / `claude-opus-4-1` are now
  mapped to the matching Claude Code tier (`sonnet`/`opus`/`haiku`) instead of
  erroring on an unrecognized `--model`; unknown strings fall back to the default
  tier. The response still echoes back the exact id the client requested, so it's a
  true drop-in for real SDK code.

## v0.7.0 — 2026-06-06
- **In-app update notifications.** The menu-bar app checks a public feed
  (`appcast.json`) in the background and surfaces "⬆ Download vX.Y.Z…" plus a
  notification when a newer build ships. Adds **Check for Updates…**, an
  **Auto-check for updates** toggle, and **Skip This Version**. Notify-only — no
  silent download/replace yet; pure stdlib, no new dependencies.

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
- Opt-in web search (`MISANTHROPIC_WEB=1`), remapped into the API's `web_search`
  content shape.
- macOS menu-bar app: supervises the server, toggles web search, opens the
  dashboard, manages per-project keys.
