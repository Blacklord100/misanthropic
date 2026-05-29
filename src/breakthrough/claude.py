"""Run the user's local Claude Code CLI as a one-shot completion backend.

This is the whole point of the package: instead of calling the hosted Anthropic
API with a paid API key, we shell out to the `claude` binary the user already
has logged in. Their existing Claude Code session IS the auth — no API key, no SDK.

Two modes:
  - run_blocking()  -> `claude -p --output-format json`, returns the parsed wrapper.
  - stream_events() -> `claude -p --output-format stream-json`, yields the raw
                       Anthropic stream events the CLI emits (verbatim), so the
                       server can re-emit them as Server-Sent Events.
"""

import json
import os
import subprocess

# Tools a pure text completion never needs. Disallowing them keeps every call a
# fast, predictable, side-effect-free writing task — no file/web/agent access,
# so the server behaves like a completion endpoint and not an autonomous agent.
DISALLOWED_TOOLS = "Bash Edit Write Read Glob Grep WebFetch WebSearch Task NotebookEdit"

DEFAULT_MODEL = os.environ.get("BREAKTHROUGH_MODEL", os.environ.get("MODEL", "sonnet"))
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
GEN_TIMEOUT_S = float(os.environ.get("GEN_TIMEOUT_MS", "120000")) / 1000.0


class ClaudeError(RuntimeError):
    """A user-facing failure from the local Claude run."""


def _base_args(model, system):
    args = [
        CLAUDE_BIN,
        "-p",
        "--max-turns", "1",
        "--no-session-persistence",
        "--disallowedTools", DISALLOWED_TOOLS,
    ]
    if system:
        args += ["--system-prompt", system]
    if model:
        args += ["--model", model]
    return args


def run_blocking(prompt, model=None, system=None, timeout=None):
    """Invoke `claude -p --output-format json` and return the parsed wrapper dict.

    The wrapper carries `result` (the text), `stop_reason`, and `usage`. Prompt
    goes in via stdin (no shell, no injection). Raises ClaudeError on failure.
    """
    args = _base_args(model or DEFAULT_MODEL, system) + ["--output-format", "json"]
    try:
        proc = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else GEN_TIMEOUT_S,
        )
    except FileNotFoundError:
        raise ClaudeError(
            "`claude` CLI not found on PATH. Install Claude Code, or set "
            "CLAUDE_BIN to its full path."
        )
    except subprocess.TimeoutExpired:
        raise ClaudeError("Local Claude timed out. Try again or a faster model.")

    if proc.returncode != 0:
        raise ClaudeError(proc.stderr.strip() or f"claude exited with code {proc.returncode}")

    try:
        wrapper = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ClaudeError("Could not parse Claude CLI output.")

    if wrapper.get("is_error"):
        result = wrapper.get("result")
        raise ClaudeError(result if isinstance(result, str) else "Local Claude returned an error.")

    return wrapper


def stream_events(prompt, model=None, system=None):
    """Invoke `claude -p --output-format stream-json` and yield events.

    The CLI wraps each Anthropic streaming event in a {"type":"stream_event",
    "event": {...}} line. We unwrap and yield the inner event verbatim, so the
    server can forward it as SSE with no schema translation.

    Yields (kind, obj) tuples where kind is:
      "event"  -> obj is a raw Anthropic stream event (message_start, etc.)
      "error"  -> obj is {"message": str}
    """
    args = _base_args(model or DEFAULT_MODEL, system) + [
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
    ]
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        raise ClaudeError(
            "`claude` CLI not found on PATH. Install Claude Code, or set "
            "CLAUDE_BIN to its full path."
        )

    proc.stdin.write(prompt)
    proc.stdin.close()

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "stream_event":
            event = obj.get("event")
            if isinstance(event, dict):
                yield ("event", event)

    rc = proc.wait()
    if rc != 0:
        stderr = proc.stderr.read().strip() if proc.stderr else ""
        yield ("error", {"message": stderr or f"claude exited with code {rc}"})
