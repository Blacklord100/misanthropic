#!/usr/bin/env python3
"""A fake `codex` CLI for contract tests (mirrors the JSONL of codex 0.144.6).

Subcommands:
  exec          emits thread.started / item.completed / turn.completed JSONL
  login status  exit 0 "Logged in using ChatGPT" unless FAKE_CODEX_LOGGED_IN=0

Prompt keywords (stdin) — CX-prefixed so a failover to FAKE_CLAUDE (which has
its own RATELIMIT/AUTHFAIL triggers) doesn't re-trip on the same prompt:
  CODEXLIMIT -> stderr usage-limit error, exit 1
  CODEXNOAUTH  -> stderr not-logged-in error, exit 1
  IMGCHECK    -> answer with the count of -i image files that exist on disk
  SYSCHECK    -> answer with the last line of the workspace AGENTS.md
"""
import json
import os
import sys

args = sys.argv[1:]

if args[:2] == ["login", "status"]:
    if os.environ.get("FAKE_CODEX_LOGGED_IN", "1") == "0":
        print("Not logged in")
        sys.exit(1)
    print("Logged in using ChatGPT")
    sys.exit(0)

if not args or args[0] != "exec":
    sys.exit(2)

prompt = sys.stdin.read()

if "CODEXLIMIT" in prompt:
    sys.stderr.write("You've hit your usage limit. Try again later.\n")
    sys.exit(1)
if "CODEXNOAUTH" in prompt:
    sys.stderr.write("Not logged in. Run codex login first.\n")
    sys.exit(1)


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _flag(name):
    return args[args.index(name) + 1] if name in args else None


text = "codex says hi"
if "IMGCHECK" in prompt:
    n = sum(1 for i, a in enumerate(args)
            if a == "-i" and os.path.exists(args[i + 1]))
    text = f"images: {n}"
elif "SYSCHECK" in prompt:
    workdir = _flag("-C") or "."
    try:
        with open(os.path.join(workdir, "AGENTS.md")) as f:
            text = "system: " + f.read().strip().splitlines()[-1]
    except OSError:
        text = "system: MISSING"

out({"type": "thread.started", "thread_id": "th-fake-1"})
out({"type": "turn.started"})
out({"type": "item.completed",
     "item": {"id": "item_0", "type": "reasoning", "text": "pondering deeply"}})
out({"type": "item.completed",
     "item": {"id": "item_1", "type": "agent_message", "text": text}})
out({"type": "turn.completed",
     "usage": {"input_tokens": 100, "cached_input_tokens": 60,
               "output_tokens": 9, "reasoning_output_tokens": 4}})
