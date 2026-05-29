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

# `--tools ""` REMOVES every tool from the model's available set, so it can only
# produce text. This matters: `--disallowedTools` merely *denies* a tool, but the
# model still *attempts* the call, which burns the single turn and aborts with
# `error_max_turns` before any text is produced (e.g. "remember that" triggers an
# internal memory Read). Removing tools makes it a clean completion endpoint.
NO_TOOLS = ""

# Claude Code's default `-p` system prompt is the full agentic prompt (memory,
# tools, the user's env/identity). We always override it so the proxy behaves
# like the bare Messages API; this neutral default is used when the client sends
# no system prompt of its own (an empty override still leaks env context).
DEFAULT_SYSTEM = "You are a helpful AI assistant."

DEFAULT_MODEL = os.environ.get("BREAKTHROUGH_MODEL", os.environ.get("MODEL", "sonnet"))
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
GEN_TIMEOUT_S = float(os.environ.get("GEN_TIMEOUT_MS", "120000")) / 1000.0


class ClaudeError(RuntimeError):
    """A user-facing failure from the local Claude run."""


def _base_args(model, system, resume=None, persist=False):
    args = [
        CLAUDE_BIN,
        "-p",
        "--max-turns", "1",
        "--tools", NO_TOOLS,
        "--system-prompt", system if system else DEFAULT_SYSTEM,
    ]
    # Persist (and resume) when a request is linked to a key-session; otherwise
    # stay ephemeral so the proxy doesn't flood session history.
    if not persist:
        args += ["--no-session-persistence"]
    if resume:
        args += ["--resume", resume]
    if model:
        args += ["--model", model]
    return args


def run_blocking(prompt, model=None, system=None, timeout=None,
                 resume=None, persist=False, cwd=None):
    """Invoke `claude -p --output-format json` and return the parsed wrapper dict.

    The wrapper carries `result` (the text), `stop_reason`, `usage`, and
    `session_id`. Prompt goes in via stdin (no shell, no injection). When
    `resume` is set, the run continues that session. Raises ClaudeError on failure.
    """
    args = _base_args(model or DEFAULT_MODEL, system, resume=resume, persist=persist) + ["--output-format", "json"]
    try:
        proc = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else GEN_TIMEOUT_S,
            cwd=cwd,
        )
    except FileNotFoundError:
        raise ClaudeError(
            "`claude` CLI not found on PATH. Install Claude Code, or set "
            "CLAUDE_BIN to its full path."
        )
    except subprocess.TimeoutExpired:
        raise ClaudeError("Local Claude timed out. Try again or a faster model.")

    if proc.returncode != 0:
        # claude often writes a JSON error to stdout (e.g. error_max_turns) while
        # leaving stderr empty, so fall back to stdout for a useful message.
        raise ClaudeError(proc.stderr.strip() or proc.stdout.strip() or f"claude exited with code {proc.returncode}")

    try:
        wrapper = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ClaudeError("Could not parse Claude CLI output.")

    if wrapper.get("is_error"):
        result = wrapper.get("result")
        raise ClaudeError(result if isinstance(result, str) else "Local Claude returned an error.")

    return wrapper


def stream_events(prompt, model=None, system=None, resume=None, persist=False, cwd=None):
    """Invoke `claude -p --output-format stream-json` and yield events.

    The CLI wraps each Anthropic streaming event in a {"type":"stream_event",
    "event": {...}} line. We unwrap and yield the inner event verbatim, so the
    server can forward it as SSE with no schema translation.

    Yields (kind, obj) tuples where kind is:
      "session" -> obj is the session id (str), emitted once near the start
      "event"   -> obj is a raw Anthropic stream event (message_start, etc.)
      "error"   -> obj is {"message": str}
    """
    args = _base_args(model or DEFAULT_MODEL, system, resume=resume, persist=persist) + [
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
            cwd=cwd,
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
        otype = obj.get("type")
        if otype == "system" and obj.get("subtype") == "init" and obj.get("session_id"):
            yield ("session", obj["session_id"])
        elif otype == "stream_event":
            event = obj.get("event")
            if isinstance(event, dict):
                yield ("event", event)

    rc = proc.wait()
    if rc != 0:
        stderr = proc.stderr.read().strip() if proc.stderr else ""
        yield ("error", {"message": stderr or f"claude exited with code {rc}"})
