"""Run the user's local Claude Code CLI in headless print mode.

This is the whole point of the package: instead of calling a hosted LLM API
with a secret key, we shell out to the `claude` binary the user already has
logged in. Their existing OAuth session IS the auth. No API key, no SDK.

Python equivalent of the Node version's runLocalClaude() in server.js.
"""

import json
import os
import subprocess

# Tools the generation never needs. Keeps it a pure writing task: fast,
# predictable, no file/web access.
DISALLOWED_TOOLS = "Bash Edit Write Read Glob Grep WebFetch WebSearch Task NotebookEdit"

DEFAULT_MODEL = os.environ.get("MODEL", "sonnet")
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
GEN_TIMEOUT_S = float(os.environ.get("GEN_TIMEOUT_MS", "120000")) / 1000.0


class ClaudeError(RuntimeError):
    """A user-facing failure from the local Claude run."""


def run_local_claude(system, user_prompt, model=None, claude_bin=None, timeout=None):
    """Invoke `claude -p` and return the model's raw text result.

    Prompt goes in via stdin (no shell, no injection). Raises ClaudeError with
    a friendly message on any failure.
    """
    model = model or DEFAULT_MODEL
    claude_bin = claude_bin or CLAUDE_BIN
    timeout = timeout if timeout is not None else GEN_TIMEOUT_S

    args = [
        claude_bin,
        "-p",
        "--output-format", "json",
        "--max-turns", "1",
        "--no-session-persistence",
        "--disallowedTools", DISALLOWED_TOOLS,
        "--system-prompt", system,
    ]
    if model:
        args += ["--model", model]

    try:
        proc = subprocess.run(
            args,
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise ClaudeError(
            "`claude` CLI not found on PATH. Install Claude Code, or set "
            "CLAUDE_BIN to its full path."
        )
    except subprocess.TimeoutExpired:
        raise ClaudeError(
            "Local Claude timed out. Try again or a faster model (MODEL=sonnet)."
        )

    if proc.returncode != 0:
        raise ClaudeError(proc.stderr.strip() or f"claude exited with code {proc.returncode}")

    try:
        wrapper = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ClaudeError("Could not parse Claude CLI output.")

    if wrapper.get("is_error"):
        result = wrapper.get("result")
        raise ClaudeError(result if isinstance(result, str) else "Local Claude returned an error.")

    result = wrapper.get("result")
    return result if isinstance(result, str) else ""


def extract_json(text):
    """Best-effort parse of a JSON object out of the model's text. None on failure."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None
