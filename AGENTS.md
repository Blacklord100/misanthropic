# AGENTS.md — install & run Misanthropic

Instructions for an AI coding agent. Misanthropic is a local server that speaks the
Anthropic Messages API but fulfills every request by shelling out to the user's
logged-in `claude` CLI (their Claude Pro/Max subscription is the auth — no API key).

## Prerequisites (check first)

- macOS or Linux, Python 3.9+.
- The `claude` CLI installed **and logged in**. Verify: `claude --version`.
  If missing, stop and tell the user to install Claude Code and run `claude` once to log in.

## Install (CLI — recommended for agents)

```bash
pipx install "git+https://github.com/Blacklord100/misanthropic.git"
# no pipx? ->  python3 -m pip install --user "git+https://github.com/Blacklord100/misanthropic.git"
```

The `misanthropic` command lands in `~/.local/bin`. If it's not on PATH in the current
shell, call it by full path: `~/.local/bin/misanthropic`.

## Run & verify

```bash
misanthropic serve &                              # starts http://127.0.0.1:8787
sleep 2
curl -fsS http://127.0.0.1:8787/health            # expect: {"status":"ok",...}
```

Smoke-test a generation:

```bash
curl -fsS http://127.0.0.1:8787/v1/messages -H 'content-type: application/json' \
  -d '{"model":"sonnet","max_tokens":32,"messages":[{"role":"user","content":"say hi"}]}'
```

## Point a client at it

Set the base URL; any Anthropic SDK works unchanged. The key is ignored in the
default (stateless) mode:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
export ANTHROPIC_API_KEY=not-needed
```

## Notes / gotchas

- The macOS **`.app`** (menu-bar GUI) is a *separate* install from the `misanthropic`
  CLI — installing one does not provide the other. For the command line, use pipx/pip above.
- Models: `sonnet` / `opus` / `haiku` or any full Claude id (e.g. `claude-sonnet-4-6`).
  `GET /v1/models` lists the catalog.
- Web search is per-request: include `tools:[{"type":"web_search_20260209","name":"web_search"}]`.
- Client tool use (function calling) works like the hosted API: send a `tools`
  array, get `tool_use` blocks back, continue with `tool_result` blocks.
  Extended thinking via `thinking:{"type":"enabled"}`.
- Do **not** bind to `0.0.0.0` / expose it publicly — it routes the user's own subscription.
  Personal use only.

## Run from source instead (if cloning)

```bash
git clone https://github.com/Blacklord100/misanthropic.git
cd misanthropic && pipx install .
# tests:  pip install -e ".[dev]" && pytest -q
```
