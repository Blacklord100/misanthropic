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

# Opt-in web access. OFF by default so the proxy stays a faithful bare-Messages
# endpoint (the hosted API also has no internet unless you pass the web_search
# server tool). When on, we expose only WebSearch — a clean 1:1 analog to the
# API's `web_search` tool — and raise the turn cap so the agentic loop (search,
# then answer) can complete instead of aborting at `--max-turns 1`. `WebSearch`
# must also be in --allowedTools, else it is permission-denied in print mode.
#
# The state is mutable (not a frozen constant) so the menu-bar app can toggle it
# at runtime; BREAKTHROUGH_WEB only sets the initial value. The server reads it
# per request via web_enabled(), so a flip takes effect on the next request.
WEB_TOOLS = "WebSearch"
WEB_MAX_TURNS = os.environ.get("BREAKTHROUGH_WEB_MAX_TURNS", "16")

_web_enabled = os.environ.get("BREAKTHROUGH_WEB", "").strip().lower() in ("1", "true", "yes", "on")


def web_enabled():
    return _web_enabled


def set_web_enabled(value):
    global _web_enabled
    _web_enabled = bool(value)
    return _web_enabled

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


def _base_args(model, system, resume=None, persist=False, web=False):
    args = [
        CLAUDE_BIN,
        "-p",
        "--system-prompt", system if system else DEFAULT_SYSTEM,
    ]
    if web:
        args += [
            "--max-turns", WEB_MAX_TURNS,
            "--tools", WEB_TOOLS,
            "--allowedTools", WEB_TOOLS,
        ]
    else:
        args += [
            "--max-turns", "1",
            "--tools", NO_TOOLS,
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


def run_web(prompt, model=None, system=None, resume=None, persist=False, cwd=None):
    """Run a web-enabled completion and collect the agentic loop's tool blocks.

    `--output-format json` collapses the whole run into one `result` string, so
    it can't expose the WebSearch tool_use / tool_result blocks we need to
    rebuild the API's `web_search` content shape. We therefore drive stream-json
    even for the non-streaming case and accumulate, in order, every text /
    tool_use / tool_result block the model produced across its turns.

    Returns (blocks, wrapper, session_id):
      blocks      -> ordered list of CLI content blocks (text, tool_use, tool_result)
      wrapper     -> the final `result` object (usage, modelUsage, stop_reason)
      session_id  -> the CLI session id (for key-linked sessions)
    """
    args = _base_args(model or DEFAULT_MODEL, system, resume=resume, persist=persist, web=True) + [
        "--output-format", "stream-json",
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

    blocks = []
    wrapper = None
    session_id = None
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
            session_id = obj["session_id"]
        elif otype == "assistant":
            for b in (obj.get("message", {}).get("content") or []):
                if isinstance(b, dict) and b.get("type") in ("text", "tool_use"):
                    blocks.append(b)
        elif otype == "user":
            for b in (obj.get("message", {}).get("content") or []):
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    blocks.append(b)
        elif otype == "result":
            wrapper = obj
            if obj.get("session_id"):
                session_id = obj["session_id"]

    rc = proc.wait()
    if rc != 0:
        stderr = proc.stderr.read().strip() if proc.stderr else ""
        detail = stderr or (wrapper.get("result") if isinstance(wrapper, dict) else "") or f"claude exited with code {rc}"
        raise ClaudeError(detail)
    if wrapper is None:
        raise ClaudeError("Claude CLI produced no result.")
    if wrapper.get("is_error"):
        result = wrapper.get("result")
        raise ClaudeError(result if isinstance(result, str) else "Local Claude returned an error.")

    return blocks, wrapper, session_id
