<div align="center">

# ☠ Misanthropic

### Why pay for an API key when you already have a subscription?

**Anthropic charges you per token. Misanthropic charges no one.**
The Anthropic Messages API, conjured out of your own Claude Code login —
no API key, no per-token bill, no call to anyone's server but your own.

**[⬇ Download for macOS](https://github.com/Blacklord100/misanthropic/releases/latest)** · [Install](#install) · [Quick start](#quick-start) · [Tips & tricks](#tips--tricks)

*For personal use only — don't stand it up as a shared server. v1.0.1 · [CHANGELOG](CHANGELOG.md) · formerly Breakthrough.*

</div>

---

Point any Anthropic SDK or HTTP client at Misanthropic and it answers **exactly**
like `api.anthropic.com` — same request shape, same response, same streaming events.
Your code can't tell the difference. Underneath, there's no hosted call and no paid
key: every request is fulfilled by shelling out to the `claude` binary you already
have logged in. **Your subscription is the auth.**

```python
from anthropic import Anthropic

# The only line that changes. The rest of your code never finds out.
client = Anthropic(base_url="http://127.0.0.1:8787", api_key="not-needed")

msg = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}],
)
print(msg.content[0].text)
```

> **One requirement, every install path:** the [`claude`](https://docs.claude.com/en/docs/claude-code)
> CLI (Claude Code), installed and logged in — *not* the Claude desktop chat app.
> If `claude --version` works in your terminal, you're set. A **Claude Pro or Max**
> subscription powering it is the whole bill.

## Install

Pick the one that fits. The first is the fastest.

### 1 · macOS app — one line ⚡

Paste into **Terminal**. Downloads, installs to Applications, and opens it:

```bash
curl -fsSL https://github.com/Blacklord100/misanthropic/releases/latest/download/Misanthropic.dmg -o /tmp/m.dmg \
  && hdiutil attach /tmp/m.dmg -nobrowse -quiet \
  && cp -R "/Volumes/Misanthropic/Misanthropic.app" /Applications/ \
  && hdiutil detach "/Volumes/Misanthropic" -quiet \
  && open /Applications/Misanthropic.app
```

A skull appears in your menu bar and the server starts on `http://127.0.0.1:8787`.

### 2 · macOS app — download the .dmg by hand

**[⬇ Download the latest `.dmg`](https://github.com/Blacklord100/misanthropic/releases/latest)**
→ open it → drag the skull onto **Applications**. Releases from v1.0.1 on are
signed with a Developer ID and notarized by Apple, so the app opens with a
normal double-click — no Gatekeeper warning.

> **App vs. command line.** The `.app` is the menu-bar GUI — it does **not** add a
> `misanthropic` command to your terminal. For the CLI (`misanthropic serve`,
> `misanthropic savings`, …) install via pipx or pip below. Both share the same
> `~/.misanthropic` state, so the app and CLI always agree.

### 3 · No app (pipx)

Best for CLI/server use:

```bash
pipx install "misanthropic[app] @ git+https://github.com/Blacklord100/misanthropic.git"
misanthropic-app          # menu-bar app   (or:  misanthropic serve)
```

### 4 · From source

```bash
git clone https://github.com/Blacklord100/misanthropic.git
cd misanthropic && pipx install .          # or: pip install .
```

Python 3.9+. **Zero runtime dependencies** — the core is pure stdlib (the menu-bar
app adds `rumps`).

## Quick start

Start the server, then point any client at the base URL:

```bash
misanthropic serve                 # http://127.0.0.1:8787
misanthropic serve --port 9000     # custom port
misanthropic serve --host 0.0.0.0  # expose on the network (see Security)
```

**curl:**

```bash
curl http://127.0.0.1:8787/v1/messages \
  -H "content-type: application/json" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":1024,
       "messages":[{"role":"user","content":"Hello!"}]}'
```

**Streaming** — add `"stream": true` for the exact Anthropic SSE sequence
(`message_start` → `content_block_delta` → … → `message_stop`), token by token.

**One-off, no server:**

```bash
misanthropic chat "Write a haiku about terminals"
```

**Any model id works** — full Anthropic ids (`claude-sonnet-4-6`,
`claude-3-5-sonnet-20241022`, `claude-opus-4-1`) and short aliases
(`sonnet`/`opus`/`haiku`). The proxy maps each to the matching Claude Code tier and
echoes back the id you asked for. **Image inputs** work too — send standard
base64 `image` blocks.

---

## Tips & tricks

### 💀 See what you'd have paid

The dashboard (`http://127.0.0.1:8787/`, or **Open dashboard** in the menu) shows a
live counter of the hosted-API list price of every token you've run —
*"You'd have paid $X on the API. Misanthropic charged you $0.00."* — all-time plus
this month, persisted across restarts. Same number from the terminal:

```bash
misanthropic savings
```

### 🔑 Per-project keys + a live activity feed

From the dashboard you can mint per-project keys (shaped `sk-ant-local-…` so they
drop into any Anthropic-SDK tooling), copy ready-to-paste connection snippets, and
watch a **Recent activity** feed — every request with model, mode, tokens, duration,
status, and (toggle **Show full text**) the full prompt/response. It's an in-memory
ring buffer for local debugging; admin endpoints are **localhost-only**.

### 🧠 Stateful conversations (key-linked sessions)

By default the server is **stateless**, like the hosted API. But approve a key and it
becomes a **conversation handle** — every request under it flows into one persistent
`claude` session, resumable in the Claude Code CLI/app:

```bash
misanthropic keys add eat-chocolate   # approve a key (= a session)
misanthropic serve                     # now in "session mode"
```

In session mode, only approved keys are accepted (others get `401`), and you **send
only the new turn** each request — the session holds the history:

```bash
# turn 1
curl http://127.0.0.1:8787/v1/messages -H "x-api-key: eat-chocolate" \
  -H "content-type: application/json" \
  -d '{"model":"sonnet","max_tokens":256,"messages":[{"role":"user","content":"My name is Sam."}]}'

# turn 2 — no history sent, the session remembers -> "Sam"
curl http://127.0.0.1:8787/v1/messages -H "x-api-key: eat-chocolate" \
  -H "content-type: application/json" \
  -d '{"model":"sonnet","max_tokens":256,"messages":[{"role":"user","content":"What is my name?"}]}'
```

```bash
misanthropic sessions list                   # key -> session id, turn count
misanthropic sessions forget eat-chocolate   # next request starts fresh
```

State lives under `~/.misanthropic/` (`keys.json`, `sessions.json`, `workspace/`).
Conversations are append-only; if a client rewrites earlier turns the server starts a
fresh session rather than corrupt the old one. Approve keys via the CLI or
`MISANTHROPIC_KEYS="key1,key2"`.

### 🌐 Web search (opt-in, per request)

By default the proxy has no internet — exactly like the bare Messages API. Web search
is decided **per request, from the `web_search` tool in the body**, just like
`api.anthropic.com`:

```python
client.messages.create(
    model="claude-sonnet-4-6", max_tokens=1024,
    tools=[{"type": "web_search_20260209", "name": "web_search"}],   # this call searches
    messages=[{"role": "user", "content": "What shipped in Python 3.13?"}],
)
```

The server-wide `MISANTHROPIC_WEB` policy layers on top: `auto` *(default)* honors the
per-request tool; `1`/`on` forces web for every request; `off` is a hard kill-switch.
The menu-bar **Force web search on** toggle flips `auto`↔`on` live. Responses carry
the real `server_tool_use` / `web_search_tool_result` / `text` blocks (streaming and
not). *Honest gaps from CLI output: `encrypted_content` is synthesized (not reusable
against the hosted API), `page_age`/`citations` are `null`, and web responses are
buffered.*

### 🔄 Updates (menu-bar app)

The app checks a public manifest (`appcast.json`) in the background and never updates
silently. When a newer build ships, the menu item becomes **⬆ Download vX.Y.Z…** with
a notification. Use **Check for Updates…**, toggle **Auto-check**, or **Skip This
Version**. (For now an update is a re-install — grab the new `.dmg`.) Override the feed
with `MISANTHROPIC_APPCAST_URL`.

### ⚙️ Config (all optional)

| Env var | Default | What |
|---|---|---|
| `PORT` / `HOST` | `8787` / `127.0.0.1` | Bind address (or `--port`/`--host`). |
| `MISANTHROPIC_MODEL` (`MODEL`) | `sonnet` | Default model when a request omits one. |
| `CLAUDE_BIN` | auto-discovered | Full path to `claude` if not on PATH. |
| `GEN_TIMEOUT_MS` | `120000` | Per-request generation timeout (ms). |
| `MISANTHROPIC_API_KEY` | — | Stateless gate: require a matching `x-api-key` (ignored in session mode). |
| `MISANTHROPIC_KEYS` | — | Comma-separated approved keys; enables session mode. |
| `MISANTHROPIC_HOME` | `~/.misanthropic` | Where keys/sessions/workspace/savings live. |
| `MISANTHROPIC_WEB` | `auto` | Web policy: `auto` / `on` (`1`) / `off`. |
| `MISANTHROPIC_WEB_MAX_TURNS` | `16` | Agentic turn cap when web is on. |
| `MISANTHROPIC_WEB_TIMEOUT_MS` | `600000` | Watchdog for a web run (it can exceed 120s). |
| `MISANTHROPIC_APPCAST_URL` | public feed | Override the update-check manifest. |

### 📡 Endpoints

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/v1/messages` | Messages API. Streaming + non-streaming. |
| `POST` | `/v1/messages/count_tokens` | Approximate token count (~4 chars/token). |
| `GET`  | `/health` | Liveness check. |
| `GET`  | `/` | Web dashboard. |
| `GET`  | `/admin/state` · `/admin/requests` | Dashboard state / activity log. **Localhost-only.** |
| `POST` | `/admin/keys` · `/admin/keys/delete` · `/admin/sessions/forget` | Manage keys/sessions. **Localhost-only.** |

---

## How it works

Per request, Misanthropic flattens `system` + `messages` into one prompt and runs your
local Claude Code as a one-shot, tool-free subprocess:

```
claude -p --max-turns 1 --tools "" \
  --system-prompt "<system or a neutral default>" --model <model> \
  --no-session-persistence \      # omitted (+ --resume) in session mode
  --output-format json            # or: stream-json --include-partial-messages --verbose
```

The prompt goes in via **stdin** (no shell, no injection). Two flags make it behave
like the bare Messages API instead of an agent: `--tools ""` *removes* every tool (so
it can't burn its one turn attempting an internal call), and `--system-prompt`
overrides Claude Code's default agent prompt (memory, environment, identity).

Non-streaming responses are reshaped from the CLI's JSON; streaming forwards the CLI's
raw Anthropic events verbatim as SSE. **Images** switch to `claude --input-format
stream-json` (the only CLI path that accepts image content). **Web** runs adds
`--tools WebSearch --allowedTools WebSearch --max-turns 16` and remaps the CLI's
`WebSearch` blocks into the API's `web_search` content shape.

## Limitations

A faithful proxy for text generation, not a 1:1 reimplementation:

- **`max_tokens`, `temperature`, `top_p`, `stop_sequences`** are accepted but not
  enforced — the CLI doesn't expose those knobs in print mode.
- **`tools` (function calling)** isn't supported. (Web search *is*.)
- **`count_tokens`** is an estimate, not the exact server-side tokenizer.
- Multi-turn history is flattened into one prompt (works well; for true continuity use
  key-linked sessions).

## Security

**For personal use only — don't stand this up as a shared server.** It binds to
`127.0.0.1` by default. If you bind to `0.0.0.0` or expose it, set
`MISANTHROPIC_API_KEY` so it isn't an open relay to your Claude account — that's how
subscriptions get banned. Ship other people the tool and let each use their own login;
never point their machines at yours.

## Build & release (maintainers)

```bash
bash packaging/release.sh             # build app + styled .dmg into dist/
bash packaging/release.sh --publish   # ...then cut the GitHub release + update feed
```

The `.dmg` is a styled drag-to-Applications installer with the generated skull icon.
Release builds are signed with a Developer ID and notarized
(`bash packaging/finish-signing.sh` runs the whole chain: sign → notarize →
staple → dmg); without `SIGN_IDENTITY` set, `build.sh` falls back to ad-hoc
signing for local use. Full details in
[packaging/DISTRIBUTION.md](packaging/DISTRIBUTION.md).

## License

MIT
