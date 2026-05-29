# Breakthrough (CLI)

Write **one** honest, well-aimed first-touch to a specific investor, then send it
yourself. A VC's read on what actually makes a partner reply — in your terminal.

Not a spam tool. No auto-sending, no mass campaigns. You get a draft; you edit
and send it.

## No API key — it uses the Claude you already pay for

Generation runs through your **local [Claude Code](https://docs.claude.com/en/docs/claude-code) CLI**, reusing
the login you already have. `breakthrough` shells out to the `claude` binary on
your machine and your existing Claude Code session *is* the auth — there's no
hosted API call and no secret key.

If you have a **Claude Pro or Max subscription** powering Claude Code, that's it:
no per-token API billing, nothing extra to buy. You're reusing the plan you're
already on.

> **Important:** this needs the **Claude Code CLI** (the `claude` command in your
> terminal), *not* the Claude Desktop chat app. The desktop app doesn't expose a
> command another program can call. If `claude --version` works in your terminal,
> you're set.

## Requirements

- The [`claude`](https://docs.claude.com/en/docs/claude-code) CLI on your PATH and
  logged in (you already are if you use Claude Code). Check with `claude --version`.
- Python 3.9+.

## Install

This lives in a private repo, so install straight from git:

```bash
# with pipx (recommended — isolated app install)
pipx install git+https://github.com/Blacklord100/breakthrough-cli.git

# or with pip
pip install git+https://github.com/Blacklord100/breakthrough-cli.git
```

Or clone and install locally:

```bash
git clone https://github.com/Blacklord100/breakthrough-cli.git
cd breakthrough-cli
pipx install .          # or: pip install .
```

Either way the command on your PATH is `breakthrough`.

## Use

Interactive:

```bash
breakthrough
```

One-shot (non-interactive — needs at least `--investor` and `--one-liner`):

```bash
breakthrough \
  --investor "Jane Doe" --firm "Acme Capital" \
  --notes "Leads seed AI infra, posted about eval tooling" \
  --one-liner "Eval harness that catches LLM regressions before prod" \
  --stage "pre-seed" --traction "3 design partners, $4k MRR" \
  --ask "exploring a $750k pre-seed" \
  --tone direct
```

Pipe the raw JSON somewhere else:

```bash
breakthrough --investor "Jane Doe" --one-liner "..." --json
```

## Config (all optional)

Set as environment variables:

- `MODEL` — model alias or id. Defaults to `sonnet`. Use `opus` for max quality.
  (Or pass `--model`.)
- `CLAUDE_BIN` — full path to the claude binary if it's not on PATH.
- `GEN_TIMEOUT_MS` — generation timeout in ms. Defaults to `120000` (120s).

## How it works

`breakthrough` runs your local Claude Code as a one-shot subprocess:

```
claude -p --output-format json --max-turns 1 --no-session-persistence \
  --disallowedTools "Bash Edit Write Read Glob Grep WebFetch WebSearch Task NotebookEdit" \
  --system-prompt "<the VC prompt>" --model <model>
```

The user prompt is passed via **stdin** (no shell, no injection), and every tool
is disallowed so it stays a pure writing task — fast, predictable, no file or web
access. The JSON it returns is parsed and printed as a brief: the angle, the
draft, when to send, and an honest "would a VC reply?" score.

## License

MIT
