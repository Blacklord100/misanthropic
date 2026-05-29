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
- `BREAKTHROUGH_API_KEY` — if set, clients must send a matching `x-api-key`
  (or `Authorization: Bearer`). If unset, the server is open.

## How it works

For each request, `breakthrough` flattens the `system` + `messages` into a single
prompt and runs your local Claude Code as a one-shot, tool-free subprocess:

```
claude -p --max-turns 1 --no-session-persistence \
  --disallowedTools "Bash Edit Write Read Glob Grep WebFetch WebSearch Task NotebookEdit" \
  --system-prompt "<system>" --model <model> \
  --output-format json            # or stream-json for streaming
```

The prompt is passed via **stdin** (no shell, no injection), and all tools are
disallowed so it stays a pure text completion — fast, predictable, no file or web
access. For non-streaming, the CLI's JSON (`result`, `stop_reason`, `usage`) is
reshaped into a Messages response. For streaming, the CLI emits the raw Anthropic
stream events, which are forwarded verbatim as Server-Sent Events.

## Limitations

It's a faithful proxy for text generation, not a 1:1 reimplementation of the
hosted API:

- **`max_tokens`, `temperature`, `top_p`, `stop_sequences`** are accepted but not
  enforced — the CLI doesn't expose those knobs in print mode.
- **`tools` (function calling)** and **image inputs** aren't supported; image
  blocks are dropped with a placeholder.
- **`count_tokens`** is an estimate, not the exact server-side tokenizer.
- Multi-turn history is flattened into one prompt (works well; not a stateful
  agent session).

## Security note

By default the server binds to `127.0.0.1` (local only) and accepts any key.
If you bind to `0.0.0.0` or expose it, set `BREAKTHROUGH_API_KEY` so it isn't an
open relay to your Claude account.

## License

MIT
