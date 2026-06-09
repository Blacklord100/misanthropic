<div align="center">

# ☠ Misanthropic

### Why pay for an API key when you already have a subscription?

**Anthropic charges you per token. Misanthropic charges no one.**
The Anthropic Messages API, conjured out of your own Claude Code login.
No API key. No per-token bill. No call to anyone's server but your own.

**For your personal usage only. Don't you dare stand this up as a shared server.**

*v0.8.0 — see [CHANGELOG.md](CHANGELOG.md). Formerly known as Breakthrough.*

</div>

---

Point any Anthropic SDK or HTTP client at Misanthropic and it answers **exactly**
like `api.anthropic.com` — same request shape, same response shape, same streaming
events. Your code can't tell the difference. The difference is underneath: there is
no hosted call and no paid key. Every request is fulfilled by quietly shelling out
to the `claude` binary you already have logged in. Your subscription **is** the auth.

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

`messages.create`, `messages.stream`, `count_tokens` — all of it works, unchanged.

## The trick: there's no key, because your subscription is the key

There is no hosted API call and no secret to leak. Misanthropic wears the face of
the [Claude Code](https://docs.claude.com/en/docs/claude-code) login already sitting
on your machine. Have a **Claude Pro or Max subscription** powering Claude Code?
That's the whole bill. Nothing per-token, nothing extra to buy, nothing phoned home.

> **You need the Claude Code CLI** — the `claude` command in your terminal — *not*
> the Claude desktop chat app. If `claude --version` works in your terminal, you're
> already armed.

## Download the macOS app

The easiest way to run it: a menu-bar app (skull icon) that supervises the server
and gives you the dashboard.

**[⬇ Download the latest `.dmg`](https://github.com/Blacklord100/misanthropic/releases/latest)** → open it → drag the skull onto **Applications**.

### Fast install — one line, no warning

Prefer the terminal? This downloads, installs, and opens it, skipping the macOS
"can't be verified" prompt:

```bash
curl -fsSL https://github.com/Blacklord100/misanthropic/releases/latest/download/Misanthropic.dmg -o /tmp/m.dmg \
  && hdiutil attach /tmp/m.dmg -nobrowse -quiet \
  && cp -R "/Volumes/Misanthropic/Misanthropic.app" /Applications/ \
  && hdiutil detach "/Volumes/Misanthropic" -quiet \
  && xattr -dr com.apple.quarantine /Applications/Misanthropic.app \
  && open /Applications/Misanthropic.app
```

> **Why the warning at all?** The app isn't *notarized* by Apple — that needs a
> paid Apple Developer account ($99/yr) this project doesn't have. It is **not**
> malware; macOS flags anything downloaded from outside the App Store. The
> `xattr -dr com.apple.quarantine` line clears that download flag so it opens
> normally. If you grabbed the `.dmg` by hand instead, first launch shows the
> prompt → click **Done**, then **System Settings → Privacy & Security → Open
> Anyway** (one time).

### No app, no Gatekeeper (pipx)

A pip install isn't a quarantined app, so there's never a warning:

```bash
pipx install "misanthropic[app] @ git+https://github.com/Blacklord100/misanthropic.git"
misanthropic-app      # menu-bar app   (or:  misanthropic serve)
```

Every path needs the `claude` CLI installed and logged in.

## Install (CLI / from source)

```bash
pipx install git+https://github.com/Blacklord100/misanthropic.git
# or:  pip install git+https://github.com/Blacklord100/misanthropic.git
```

Or clone and install locally:

```bash
git clone https://github.com/Blacklord100/misanthropic.git
cd misanthropic && pipx install .   # or: pip install .
```

Requirements: the [`claude`](https://docs.claude.com/en/docs/claude-code) CLI on
your PATH and logged in, and Python 3.9+. **Zero runtime dependencies** — the core
is pure stdlib.

## Summon the server

```bash
misanthropic serve                 # http://127.0.0.1:8787
misanthropic serve --port 9000     # custom port
misanthropic serve --host 0.0.0.0  # expose on the network (read the security note)
```

Then aim any client at the base URL.

**curl:**

```bash
curl http://127.0.0.1:8787/v1/messages \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

**Streaming** — add `"stream": true` and you get the exact Anthropic SSE sequence
(`message_start`, `content_block_delta`, …, `message_stop`), token by token.

**One-off gut check** (no server, no client):

```bash
misanthropic chat "Write a haiku about terminals"
```

## Desktop app (macOS menu bar)

Want it always-on instead of babysitting a terminal? There's a menu-bar app — a
skull with an Anthropic-asterisk forehead mark — that supervises the server and
hands you a dashboard.

```bash
bash packaging/release.sh      # -> dist/Misanthropic.app + dist/Misanthropic-<version>.dmg
open dist/Misanthropic-*.dmg   # opens the installer window; drag the skull onto Applications
```

The `.dmg` is a proper drag-to-install image (app icon + an **Applications** alias
with an arrow — same as installing Chrome). `release.sh` builds the app, gives it
the colored skull icon, and packages the styled disk image; `bash packaging/release.sh
--publish` also cuts the GitHub release and refreshes the update feed. See
[packaging/DISTRIBUTION.md](packaging/DISTRIBUTION.md) for signing/notarizing so it
opens with no Gatekeeper warning on other Macs.

It lurks in the menu bar (no Dock icon): start/stop the server, **force web
search on** for every request (off by default — requests decide per call; see
below), open the dashboard, copy the base URL, and toggle "start at login". The
dashboard (also at
`http://127.0.0.1:8787/` whenever the server runs) lets you:

- see a running **"money saved"** banner — the hosted-API list price of every token
  you've ever run, totalled (all-time + this month). The whole pitch, as a live
  number: *"You'd have paid $X on the API. Misanthropic charged you $0.00."* (Same
  figure from the terminal: `misanthropic savings`.)
- mint per-project API keys (shaped `sk-ant-local-…` so they drop straight into any
  Anthropic-SDK tooling),
- copy ready-to-paste connection snippets (`ANTHROPIC_BASE_URL` + key),
- see and delete keys / conversations,
- watch a live **Recent activity** feed: every request shows time, key, model, mode,
  tokens, duration, and status as calls come in. Hit **Show full text** to expand a
  row and read the entire prompt and response (newlines preserved; long messages
  scroll, and an open row keeps its place while the feed refreshes). It's an
  in-memory ring buffer for local debugging — cleared on restart, never persisted.

Every generated key is an approved session key (see below), so wiring up a project
with persistent memory is one click. The admin/dashboard endpoints are
**localhost-only**. To run the app from source without building (from a clone):
`pip install -e ".[app]" && misanthropic-app`.

To sign/notarize the app and share it, see
[packaging/DISTRIBUTION.md](packaging/DISTRIBUTION.md). Note: the app uses each
recipient's *own* local Claude login — you ship the tool, not your subscription.

## Updates

The menu-bar app checks for new versions in the background and tells you — it never
updates silently. When a newer build ships, the menu item becomes **⬆ Download
vX.Y.Z…** and you get a notification; clicking it opens the download page with the
`.dmg`. Use **Check for Updates…** to check on demand, flip **Auto-check for
updates** off to disable the background check, or pick **Skip This Version** to
silence a release you don't want.

The check reads a small public manifest (`appcast.json` at the repo root) — it
sends nothing about you and needs no credentials. Point `MISANTHROPIC_APPCAST_URL`
at your own feed to override it.
(For now an update is a re-install — download the new `.dmg` and replace the app;
there's no silent in-place swap yet.)

## Key-linked sessions (conversations with a memory)

By default the server is **stateless** — like the hosted API, each request is
self-contained and ephemeral. But you can make an **API key double as a conversation
handle**: every request under that key flows into one persistent `claude` session
that's visible and resumable in the Claude Code CLI / desktop app.

Flip it on by approving one or more keys:

```bash
misanthropic keys add eat-chocolate     # approve a key (= a session)
misanthropic keys list
misanthropic serve                       # now runs in "session mode"
```

Once any key is approved, the server switches to **session mode**:

- Only approved keys are accepted (others get `401`). The key both authorizes *and*
  names the conversation.
- The first request under a key starts a persistent session; later requests
  `--resume` it, so the whole chat accumulates in one session.
- **Send only the new turn** each request — the session holds the prior history
  (you don't resend the whole `messages` array):

```bash
# turn 1
curl http://127.0.0.1:8787/v1/messages -H "x-api-key: eat-chocolate" \
  -H "content-type: application/json" \
  -d '{"model":"sonnet","max_tokens":256,"messages":[{"role":"user","content":"My name is Sam."}]}'

# turn 2 — no history sent, the session remembers
curl http://127.0.0.1:8787/v1/messages -H "x-api-key: eat-chocolate" \
  -H "content-type: application/json" \
  -d '{"model":"sonnet","max_tokens":256,"messages":[{"role":"user","content":"What is my name?"}]}'
# -> "Sam"
```

Inspect or sever the links:

```bash
misanthropic sessions list             # key -> session id, turn count
misanthropic sessions forget eat-chocolate   # next request starts a fresh session
```

State lives under `~/.misanthropic/` (override with `MISANTHROPIC_HOME`):
`keys.json`, `sessions.json`, and a `workspace/` directory used as a stable working
dir so `--resume` (which is project-scoped) resolves. The sessions show up in the
Claude Code CLI/app under that workspace project.

Notes & limits: conversations are **append-only** — if a client rewrites earlier
turns, the server starts a fresh session rather than corrupt the old one. Concurrent
requests sharing a key are serialized. Approve keys via the CLI or the
`MISANTHROPIC_KEYS="key1,key2"` env var.

## Endpoints

| Method | Path                        | Notes                                        |
|--------|-----------------------------|----------------------------------------------|
| `POST` | `/v1/messages`              | Messages API. Streaming + non-streaming.     |
| `POST` | `/v1/messages/count_tokens` | Approximate token count (~4 chars/token).    |
| `GET`  | `/health`                   | Liveness check.                              |
| `GET`  | `/`                         | Web dashboard (manage keys + sessions).      |
| `GET`  | `/admin/state`              | Dashboard state (keys, sessions). **Localhost-only.** |
| `GET`  | `/admin/requests`           | Recent request log (ring buffer). **Localhost-only.** |
| `POST` | `/admin/keys`               | Create a key. **Localhost-only.**            |
| `POST` | `/admin/keys/delete`        | Remove a key. **Localhost-only.**            |
| `POST` | `/admin/sessions/forget`    | Reset a key's session link. **Localhost-only.** |

## Config (all optional)

Environment variables:

- `PORT` / `HOST` — server bind address. Defaults `8787` / `127.0.0.1`.
  (Or pass `--port` / `--host`.)
- `MISANTHROPIC_MODEL` (or `MODEL`) — default model when a request omits one.
  Defaults to `sonnet`.
- `CLAUDE_BIN` — full path to the `claude` binary if it's not on PATH.
- `GEN_TIMEOUT_MS` — per-request generation timeout in ms. Defaults `120000`.
- `MISANTHROPIC_API_KEY` — stateless-mode gate: if set (and no approved keys exist),
  clients must send a matching `x-api-key`. Ignored in session mode.
- `MISANTHROPIC_KEYS` — comma-separated approved keys; enables session mode (same as
  `misanthropic keys add`).
- `MISANTHROPIC_HOME` — where keys/sessions/workspace state lives. Defaults to
  `~/.misanthropic`.
- `MISANTHROPIC_APPCAST_URL` — override the update-check manifest URL (the menu-bar
  app's "check for updates" feed). Defaults to the public release feed.
- `MISANTHROPIC_WEB` — web-search policy (see below). `auto` (default) decides
  per request from the `web_search` tool, like the hosted API; `1`/`on` forces it
  on for every request; `off` is a hard kill-switch (no internet, ever).
- `MISANTHROPIC_WEB_MAX_TURNS` — agentic turn cap when web is on. Defaults `16`.
- `MISANTHROPIC_WEB_TIMEOUT_MS` — watchdog timeout for a web run (the loop can
  legitimately take >120s, so this defaults to `600000` = 10 min; runaway runs are
  killed instead of hanging).

## Web search (per-request, like the hosted API)

By default the proxy has no internet access — exactly like the bare Messages API.
Web search is **decided per request, from the `web_search` tool in the request
body**, the same way `api.anthropic.com` does it. Include the tool on a call and
that call searches the web; leave it off and it doesn't. Nothing global to flip:

```python
client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    tools=[{"type": "web_search_20260209", "name": "web_search"}],   # this call searches
    messages=[{"role": "user", "content": "What shipped in Python 3.13?"}],
)
```

The server-wide `MISANTHROPIC_WEB` policy layers on top:

- `auto` *(default)* — honor each request's `web_search` tool. Drop-in faithful.
- `on` (`MISANTHROPIC_WEB=1`) — force web for **every** request, even ones that
  don't ask. Handy for clients that can't set `tools`.
- `off` (`MISANTHROPIC_WEB=off`) — hard kill-switch: deny internet regardless of
  the request. The "this server can never reach the web" guarantee.

The menu-bar app's **Force web search on** toggle flips between `auto` and `on`
live (takes effect on the next request). When web runs for a request, the server
arms the CLI's `WebSearch` tool and remaps the result into the **API's own
`web_search` content shape**:

```bash
misanthropic serve                  # auto: per-request
MISANTHROPIC_WEB=1 misanthropic serve   # force on for everything
```

Responses then carry `server_tool_use` (name `web_search`),
`web_search_tool_result` (with `web_search_result` items), and `text` blocks — the
same structure the hosted API returns — for both streaming and non-streaming.
`usage.server_tool_use.web_search_requests` is populated.

Honest gaps (unavoidable from CLI output): `encrypted_content` is synthesized and
**not** reusable against the hosted API, `page_age` and text `citations` are `null`,
and web responses are buffered (correct block shape, but no token-by-token streaming
timing).

## How the magic trick works

For each request, Misanthropic flattens the `system` + `messages` into a single
prompt and runs your local Claude Code as a one-shot, tool-free subprocess:

```
claude -p --max-turns 1 --tools "" \
  --system-prompt "<system or a neutral default>" --model <model> \
  --no-session-persistence \      # omitted (and --resume added) in session mode
  --output-format json            # or: stream-json --include-partial-messages --verbose
```

The prompt goes in via **stdin** (no shell, no injection). Two flags make it behave
like the bare Messages API instead of an agent:

- `--tools ""` **removes** every tool from the model's set. (Merely *disallowing*
  tools isn't enough — the model would still *attempt* a call, e.g. an internal
  memory `Read` on "remember that", burning the one turn and aborting with
  `error_max_turns` before producing text.)
- `--system-prompt` always overrides Claude Code's default agent prompt (which
  otherwise injects memory behavior and your environment/identity).

For non-streaming, the CLI's JSON (`result`, `stop_reason`, `usage`) is reshaped into
a Messages response. For streaming, the CLI emits the raw Anthropic stream events,
forwarded verbatim as Server-Sent Events.

When a request carries an `image` content block, the flow switches from a plain text
prompt to `claude --input-format stream-json` — the only CLI path that accepts image
content — feeding one Anthropic-shaped user message (the rendered text plus the image
blocks). This works in stateless, session, streaming, and web modes.

When a request runs with web on (it included the `web_search` tool, or the policy
forces it), the invocation changes to `--tools WebSearch --allowedTools WebSearch
--max-turns 16` and always runs `stream-json` (even for non-streaming requests,
since the plain JSON wrapper collapses the agentic loop into one string and hides
the tool blocks). The CLI's `WebSearch` tool_use/tool_result events are then
remapped into the API's `web_search` content blocks.

## Limitations

It's a faithful proxy for text generation, not a 1:1 reimplementation of the hosted
API:

- **`max_tokens`, `temperature`, `top_p`, `stop_sequences`** are accepted but not
  enforced — the CLI doesn't expose those knobs in print mode.
- **Any Claude model id works.** Full Anthropic ids (`claude-sonnet-4-6`,
  `claude-3-5-sonnet-20241022`, `claude-opus-4-1`, …) and short aliases
  (`sonnet`/`opus`/`haiku`) are both accepted; the proxy resolves each to the
  matching Claude Code tier, and the response echoes back the id you asked for. An
  unrecognized model falls back to the default tier instead of erroring.
- **`tools` (function calling)** isn't supported. (Web search *is* — see above.)
- **Image inputs are supported.** Send standard Anthropic `image` content blocks
  (base64) and they're passed straight through to the model via the CLI's stream-json
  input. Works in stateless, session, streaming, and web modes.
- **`count_tokens`** is an estimate, not the exact server-side tokenizer.
- Multi-turn history is flattened into one prompt (works well; not a stateful agent
  session unless you use key-linked sessions).

## Security note

**For your personal usage only. Don't you dare stand this up as a shared server.**
By default it binds to `127.0.0.1` (local only) and accepts any key. Bind to
`0.0.0.0` or expose it and you've turned your own Claude account into an open relay —
that's how subscriptions get banned. Ship other people the tool and let each one use
their own login; never point their machines at yours.

## License

MIT
