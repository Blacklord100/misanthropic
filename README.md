# Breakthrough

**An Anthropic-API-compatible server that runs on your local Claude Code CLI.**

Point any Anthropic SDK or HTTP client at `breakthrough` and it behaves exactly
like the hosted Messages API — same request shape, same response shape, same
streaming events. The difference is invisible to your code: instead of calling
`api.anthropic.com` with a paid API key, every request is fulfilled by shelling
out to the `claude` binary you already have logged in.

```python
from anthropic import Anthropic

# The only change to your code: where it points.
client = Anthropic(base_url="http://127.0.0.1:8787", api_key="not-needed")

msg = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}],
)
print(msg.content[0].text)
```

That's it. `messages.create`, `messages.stream`, `count_tokens` — all work
unchanged.

## No API key — it uses the Claude you already pay for

There's no hosted API call and no secret key. `breakthrough` reuses your local
[Claude Code](https://docs.claude.com/en/docs/claude-code) login as the auth.
If you have a **Claude Pro or Max subscription** powering Claude Code, that's it:
no per-token API billing, nothing extra to buy.

> **Important:** this needs the **Claude Code CLI** (the `claude` command in your
> terminal), *not* the Claude Desktop chat app. If `claude --version` works in
> your terminal, you're set.

## Install

This lives in a private repo, so install straight from git:

```bash
pipx install git+https://github.com/Blacklord100/breakthrough-cli.git
# or:  pip install git+https://github.com/Blacklord100/breakthrough-cli.git
```

Or clone and install locally:

```bash
git clone https://github.com/Blacklord100/breakthrough-cli.git
cd breakthrough-cli && pipx install .   # or: pip install .
```

Requirements: the [`claude`](https://docs.claude.com/en/docs/claude-code) CLI on
your PATH and logged in, and Python 3.9+.

## Run the server

```bash
breakthrough serve                 # http://127.0.0.1:8787
breakthrough serve --port 9000     # custom port
breakthrough serve --host 0.0.0.0  # expose on the network (see security note)
```

Then point any client at the base URL.

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

**Streaming** — add `"stream": true` and you get the standard Anthropic SSE
sequence (`message_start`, `content_block_delta`, …, `message_stop`).

**Quick one-off test** (no server, no client needed):

```bash
breakthrough chat "Write a haiku about terminals"
```

## Desktop app (macOS menu bar)

Prefer a launch-and-forget app over a terminal? There's a menu-bar app that
supervises the server and gives you a dashboard for keys/projects.

```bash
bash packaging/build.sh        # -> dist/Breakthrough.app
open dist/Breakthrough.app
```

It lives in the menu bar (no Dock icon): start/stop the server, toggle
**web search** on/off (takes effect on the next request — see below), open the
dashboard, copy the base URL, and toggle "start at login". The dashboard
(also at `http://127.0.0.1:8787/` whenever the server runs) lets you:

- generate per-project API keys (formatted `sk-ant-local-…` so they drop into
  any Anthropic-SDK tooling),
- copy ready-to-paste connection snippets (`ANTHROPIC_BASE_URL` + key),
- see and delete keys / conversations.

Each generated key is an approved session key (see below), so connecting a
project and getting persistent memory is one click. The admin/dashboard endpoints
are **localhost-only**. To run the app from source without building:
`pip install "breakthrough[app]" && breakthrough-app`.

For signing/notarizing to share the app with others, see
[packaging/DISTRIBUTION.md](packaging/DISTRIBUTION.md). Note: the app uses each
user's *own* local Claude login — you ship the tool, not your subscription.

## Key-linked sessions (stateful conversations)

By default the server is **stateless** — like the hosted API, each request is
self-contained and ephemeral. But you can make an **API key double as a
conversation handle**: every request under that key flows into one persistent
`claude` session that's visible and resumable in the Claude Code CLI / desktop app.

Turn it on by approving one or more keys:

```bash
breakthrough keys add eat-chocolate     # approve a key (= a session)
breakthrough keys list
breakthrough serve                       # now runs in "session mode"
```

Once any key is approved, the server switches to **session mode**:

- Only approved keys are accepted (others get `401`). The key both authorizes
  *and* names the conversation.
- The first request under a key starts a persistent session; later requests
  `--resume` it, so the whole chat accumulates in one session.
- **Send only the new turn** each request — the session holds the prior history
  (you don't resend the whole `messages` array):

```bash
# turn 1
curl http://127.0.0.1:8787/v1/messages -H "x-api-key: eat-chocolate" \
  -H "content-type: application/json" \
  -d '{"model":"sonnet","max_tokens":256,"messages":[{"role":"user","content":"My name is Sam."}]}'

# turn 2 — no history, the session remembers
curl http://127.0.0.1:8787/v1/messages -H "x-api-key: eat-chocolate" \
  -H "content-type: application/json" \
  -d '{"model":"sonnet","max_tokens":256,"messages":[{"role":"user","content":"What is my name?"}]}'
# -> "Sam"
```

Inspect or reset the links:

```bash
breakthrough sessions list             # key -> session id, turn count
breakthrough sessions forget eat-chocolate   # next request starts a fresh session
```

State lives under `~/.breakthrough/` (override with `BREAKTHROUGH_HOME`):
`keys.json`, `sessions.json`, and a `workspace/` directory used as a stable
working dir so `--resume` (which is project-scoped) resolves. The sessions show
up in the Claude Code CLI/app under that workspace project.

Notes & limits: conversations are **append-only** — if a client rewrites earlier
turns, the server starts a fresh session rather than corrupt the old one.
Concurrent requests sharing a key are serialized. Approve keys via the CLI or the
`BREAKTHROUGH_KEYS="key1,key2"` env var.

## Endpoints

| Method | Path                        | Notes                                        |
|--------|-----------------------------|----------------------------------------------|
| `POST` | `/v1/messages`              | Messages API. Streaming + non-streaming.     |
| `POST` | `/v1/messages/count_tokens` | Approximate token count (~4 chars/token).    |
| `GET`  | `/health`                   | Liveness check.                              |

## Config (all optional)

Environment variables:

- `PORT` / `HOST` — server bind address. Defaults `8787` / `127.0.0.1`.
  (Or pass `--port` / `--host`.)
- `BREAKTHROUGH_MODEL` (or `MODEL`) — default model when a request omits one.
  Defaults to `sonnet`.
- `CLAUDE_BIN` — full path to the `claude` binary if it's not on PATH.
- `GEN_TIMEOUT_MS` — per-request generation timeout in ms. Defaults `120000`.
- `BREAKTHROUGH_API_KEY` — stateless-mode gate: if set (and no approved keys
  exist), clients must send a matching `x-api-key`. Ignored in session mode.
- `BREAKTHROUGH_KEYS` — comma-separated approved keys; enables session mode
  (same as `breakthrough keys add`).
- `BREAKTHROUGH_HOME` — where keys/sessions/workspace state lives. Defaults to
  `~/.breakthrough`.
- `BREAKTHROUGH_WEB` — set to `1` to enable web search (see below). Off by default.
- `BREAKTHROUGH_WEB_MAX_TURNS` — agentic turn cap when web is on. Defaults `16`.

## Web search (opt-in)

By default the proxy has no internet access — exactly like the bare Messages API.
Set `BREAKTHROUGH_WEB=1` and the server enables the CLI's `WebSearch` tool, then
remaps the result into the **API's own `web_search` content shape**:

```bash
BREAKTHROUGH_WEB=1 breakthrough serve
```

Responses then contain `server_tool_use` (name `web_search`),
`web_search_tool_result` (with `web_search_result` items), and `text` blocks —
the same structure the hosted API returns — for both streaming and non-streaming.
`usage.server_tool_use.web_search_requests` is populated.

Honest gaps (unavoidable from CLI output): `encrypted_content` is synthesized and
**not** reusable against the hosted API, `page_age` and text `citations` are
`null`, and web responses are buffered (correct block shape, but no token-by-token
streaming timing).

## How it works

For each request, `breakthrough` flattens the `system` + `messages` into a single
prompt and runs your local Claude Code as a one-shot, tool-free subprocess:

```
claude -p --max-turns 1 --tools "" \
  --system-prompt "<system or a neutral default>" --model <model> \
  --no-session-persistence \      # omitted (and --resume added) in session mode
  --output-format json            # or stream-json for streaming
```

The prompt is passed via **stdin** (no shell, no injection). Two flags make it
behave like the bare Messages API rather than an agent:

- `--tools ""` **removes** every tool from the model's set. (Merely *disallowing*
  tools isn't enough — the model would still *attempt* a call, e.g. an internal
  memory `Read` on "remember that", which burns the one turn and aborts with
  `error_max_turns` before producing text.)
- `--system-prompt` always overrides Claude Code's default agent prompt (which
  otherwise injects memory behavior and your environment/identity).

For non-streaming, the CLI's JSON (`result`, `stop_reason`, `usage`) is reshaped
into a Messages response. For streaming, the CLI emits the raw Anthropic stream
events, which are forwarded verbatim as Server-Sent Events.

With `BREAKTHROUGH_WEB=1` the invocation changes to `--tools WebSearch
--allowedTools WebSearch --max-turns 16` and always runs `stream-json` (even for
non-streaming requests, since the plain JSON wrapper collapses the agentic loop
into one string and hides the tool blocks). The CLI's `WebSearch`
tool_use/tool_result events are then remapped into the API's `web_search`
content blocks.

## Limitations

It's a faithful proxy for text generation, not a 1:1 reimplementation of the
hosted API:

- **`max_tokens`, `temperature`, `top_p`, `stop_sequences`** are accepted but not
  enforced — the CLI doesn't expose those knobs in print mode.
- **`tools` (function calling)** and **image inputs** aren't supported; image
  blocks are dropped with a placeholder. (Web search is supported separately —
  see above.)
- **`count_tokens`** is an estimate, not the exact server-side tokenizer.
- Multi-turn history is flattened into one prompt (works well; not a stateful
  agent session).

## Security note

By default the server binds to `127.0.0.1` (local only) and accepts any key.
If you bind to `0.0.0.0` or expose it, set `BREAKTHROUGH_API_KEY` so it isn't an
open relay to your Claude account.

## License

MIT
