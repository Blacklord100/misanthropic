# Breakthrough (CLI)

Write **one** honest, well-aimed first-touch to a specific investor, then send it
yourself. A VC's read on what actually makes a partner reply — in your terminal.

Not a spam tool. No auto-sending, no mass campaigns. You get a draft; you edit
and send it.

## No API key

Generation runs through your **local Claude Code CLI**, using the login you
already have. There's no hosted API call and no secret key — `breakthrough`
shells out to the `claude` binary on your machine, and your existing Claude Code
session *is* the auth.

## Install

```bash
pipx install breakthrough     # recommended (isolated app install)
# or
pip install breakthrough
```

Requirements: the [`claude`](https://docs.claude.com/en/docs/claude-code) CLI on
your PATH and logged in (you already are if you use Claude Code). Python 3.9+.

## Use

Interactive:

```bash
breakthrough
```

One-shot (non-interactive):

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
- `GEN_TIMEOUT_MS` — generation timeout in ms. Defaults to `120000`.

## How it works

`claude -p --output-format json` with a custom `--system-prompt` (the VC prompt)
and all tools disallowed, run as a subprocess. The prompt is passed via stdin —
no shell, no injection. The JSON it returns is parsed and printed as a brief:
the angle, the draft, when to send, and an honest "would a VC reply?" score.

## License

MIT
