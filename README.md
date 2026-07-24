<div align="center">

# ☠ Misanthropic

### Why pay for an API key when you already have a subscription?

**Anthropic charges you per token. Misanthropic charges no one.**
The Anthropic Messages API, conjured out of your own Claude Code login —
no API key, no per-token bill, no call to anyone's server but your own.

**[⬇ Download for macOS](https://github.com/Blacklord100/misanthropic/releases/latest)** · [Install](#install) · [Quick start](#quick-start) · [Tips & tricks](#tips--tricks) · [☕ Buy me a coffee](https://paypal.me/Blacklord100)

*For personal use only — don't stand it up as a shared server. v1.3.0 · [CHANGELOG](CHANGELOG.md) · formerly Breakthrough.*

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

### ⇄ Multiple accounts & the Codex backend

One subscription runs out? Register more — any mix of **Claude** logins and
**OpenAI Codex** (ChatGPT) logins. What happens at a usage limit is the
**failover policy** (Settings): **Stop** (the default) fails the request with
a clean 529 and waits for the limit to reset; **Auto** hops to the next
eligible account — streaming included: the stream doesn't start until a
working account produced its first token, so clients never see the switch.
Each API key can override the policy, so one project can fail over while
another insists on its account (for a session key, failing over starts a
fresh conversation). Pin an account to force it, or let priority order
decide. The dashboard's **Accounts** page shows every account's health, who's
serving, rate-limit countdowns, per-account usage, and how many runs each is
handling right now; the menu-bar gets a picker, the CLI gets `misanthropic
accounts`.

**Load-balancing (dispatch strategy).** With failover on and more than one
account eligible, the **Balanced** strategy *(default)* spreads concurrent
runs across accounts — each new request goes to the least-loaded one — so the
cloud-side per-account rate limit (the real throughput ceiling) is reached N×
slower and standby accounts do real work instead of waiting for #1 to break.
Switch to **Failover** to keep strict priority order (ride #1 until it hits a
limit, then hop). A pinned account always wins over balancing. Because CLI runs
are I/O-bound — each just waits on the cloud — the local process cap
(`max_concurrency`) is a RAM/process guard, not a CPU one, so it auto-scales
with the number of accounts (8 per account, capped at 30) unless you set it
explicitly.

```bash
misanthropic accounts add claude --label "Claude — work"
# → CLAUDE_CONFIG_DIR=~/.misanthropic/claude/<id> claude   # log it in once
misanthropic accounts add codex --label "Codex — personal"
# → CODEX_HOME=~/.misanthropic/codex/<id> codex login
misanthropic accounts list
```

Honest gaps: the Codex backend serves **text, images, thinking and web
search** — client tools and key-linked sessions always run on Claude accounts
(a request needing them 529s rather than silently degrading when every Claude
account is limited). Web policy applies consistently across backends: codex
runs pass `web_search="live"` when web is on and `"disabled"` otherwise
(codex enables web by default — the proxy always sets it explicitly), and
codex web responses count searches in `usage.server_tool_use` but carry no
`web_search_tool_result` blocks (codex reports only queries). Codex has no
system-prompt flag, so the system prompt is delivered via a per-run AGENTS.md
workspace. Sessions stick to the account that created them. Cooldowns:
15 min → 1 h → 4 h escalating (or the reset time the error names), tunable
via `MISANTHROPIC_COOLDOWN_S`.

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
| `MISANTHROPIC_ENFORCE_MAX_TOKENS` | off | Truncate responses at `max_tokens` (approximate count). |
| `MISANTHROPIC_TOOL_PARK_TTL_MS` | `600000` | How long a parked tool-loop process waits for its `tool_result`. |
| `MISANTHROPIC_MAX_PARKED` | `8` | Cap on simultaneously parked tool runs (oldest evicted). |
| `MISANTHROPIC_TOOL_MAX_TURNS` | `50` | Runaway guard on a single tool run's agentic turns. |
| `CODEX_BIN` | auto-discovered | Full path to `codex` if not on PATH. |
| `MISANTHROPIC_CODEX_TIMEOUT_MS` | `300000` | Per-request timeout for codex runs. |
| `MISANTHROPIC_COOLDOWN_S` / `_MAX_S` | `900` / `14400` | Rate-limit cooldown base / cap per account. |
| `MISANTHROPIC_APPCAST_URL` | public feed | Override the update-check manifest. |

### 📡 Endpoints

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/v1/messages` | Messages API. Streaming + non-streaming, tools, thinking. |
| `POST` | `/v1/messages/count_tokens` | Approximate token count (~4 chars/token). |
| `GET`  | `/v1/models` · `/v1/models/{id}` | Model catalog (what SDK pickers probe). |
| `GET`  | `/health` | Liveness check. |
| `GET`  | `/` | Web dashboard. |
| `GET`  | `/admin/state` · `/admin/requests` | Dashboard state / activity log. **Localhost-only.** |
| `POST` | `/admin/keys` · `/admin/keys/delete` · `/admin/sessions/forget` | Manage keys/sessions. **Localhost-only.** |
| `GET/POST` | `/admin/accounts` (+ `/update` `/delete` `/pin` `/probe`) | Manage backend accounts. **Localhost-only.** |

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

### 🔧 Tool use (function calling)

Send a `tools` array like you would to the hosted API and the model answers with
real `tool_use` blocks — parallel calls, authentic ids, `stop_reason:
"tool_use"` — then continue the loop with `tool_result` blocks. Agent
frameworks just work:

```python
tools = [{"name": "get_weather", "description": "Weather for a city.",
          "input_schema": {"type": "object",
                           "properties": {"city": {"type": "string"}},
                           "required": ["city"]}}]
first = client.messages.create(model="claude-sonnet-4-6", max_tokens=1024,
                               tools=tools, messages=[...])
# first.stop_reason == "tool_use" -> run the tool, send back tool_result
```

Under the hood the proxy exposes your tools to the local CLI over an
in-process MCP bridge and **parks** the live `claude` process while your code
executes the tool; the follow-up request continues the exact same model state.
Parks expire after 10 min (`MISANTHROPIC_TOOL_PARK_TTL_MS`) — an expired loop
transparently restarts from the resent history. Honest gaps: `tool_choice`
`any` / `tool` are best-effort nudges (the CLI has no forced-tool mode), and
tool requests always run stateless — key-linked sessions are bypassed.

### 🧩 Extended thinking

Pass `thinking={"type": "enabled", "budget_tokens": N}` and responses carry the
model's `thinking` blocks, streaming included. Without it, thinking is
stripped — exactly like the hosted API. (`budget_tokens` is accepted but not
enforced; the CLI exposes no thinking knobs.)

## Limitations

A faithful proxy for text generation, not a 1:1 reimplementation:

- **`stop_sequences` are enforced** by the proxy (the CLI can't) — including
  mid-stream, with the proper `stop_reason`. **`max_tokens` enforcement is
  opt-in** (Settings, or `MISANTHROPIC_ENFORCE_MAX_TOKENS=1`) because the
  count is a ~4 chars/token estimate, not the real tokenizer.
- **`temperature`, `top_p`, `top_k`** are accepted but not enforced — the CLI
  doesn't expose sampling knobs in print mode.
- **`tool_choice: {"type": "any"|"tool"}`** is a best-effort system-prompt
  nudge, not a guarantee; `thinking.budget_tokens` is not enforced.
- **`count_tokens`** is an estimate, not the exact server-side tokenizer.
- Multi-turn history is flattened into one prompt (works well; for true continuity use
  key-linked sessions). Tool requests run stateless even under an approved key.

## Security

**For personal use only — don't stand this up as a shared server.** It binds to
`127.0.0.1` by default. If you bind to `0.0.0.0` or expose it, set
`MISANTHROPIC_API_KEY` so it isn't an open relay to your Claude account — that's how
subscriptions get banned. Ship other people the tool and let each use their own login;
never point their machines at yours.

## Build & release (maintainers)

```bash
cd frontend && npm install && npm run build && cd ..   # dashboard -> src/misanthropic/static (checked in)
bash packaging/release.sh             # build app + styled .dmg into dist/
bash packaging/release.sh --publish   # ...then cut the GitHub release + update feed
```

The dashboard is a Preact + Tailwind app in `frontend/`; its compiled output is
committed under `src/misanthropic/static/` so pip/pipx/.app installs never need
Node. Rebuild it whenever you touch `frontend/`.

The `.dmg` is a styled drag-to-Applications installer with the generated skull icon.
Release builds are signed with a Developer ID and notarized
(`bash packaging/finish-signing.sh` runs the whole chain: sign → notarize →
staple → dmg); without `SIGN_IDENTITY` set, `build.sh` falls back to ad-hoc
signing for local use. Full details in
[packaging/DISTRIBUTION.md](packaging/DISTRIBUTION.md).

## License

MIT
