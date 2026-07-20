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
import shutil
import subprocess
import threading

# `--tools ""` REMOVES every tool from the model's available set, so it can only
# produce text. This matters: `--disallowedTools` merely *denies* a tool, but the
# model still *attempts* the call, which burns the single turn and aborts with
# `error_max_turns` before any text is produced (e.g. "remember that" triggers an
# internal memory Read). Removing tools makes it a clean completion endpoint.
NO_TOOLS = ""

# Web access. When a request runs with web on we expose only WebSearch — a clean
# 1:1 analog to the API's `web_search` tool — and raise the turn cap so the
# agentic loop (search, then answer) can complete instead of aborting at
# `--max-turns 1`. `WebSearch` must also be in --allowedTools, else it is
# permission-denied in print mode. Whether a given request runs with web on is
# decided per request — see the web policy below.
WEB_TOOLS = "WebSearch"


def _positive_int_env(name, default):
    """Read an env var as a positive int, falling back to default on garbage."""
    try:
        v = int(os.environ.get(name, str(default)))
        return v if v > 0 else default
    except ValueError:
        return default


WEB_MAX_TURNS = str(_positive_int_env("MISANTHROPIC_WEB_MAX_TURNS", 16))

# Client tool runs: the loop is client-driven (each tool round is a turn), so
# the cap is a runaway guard, not a policy.
TOOL_MAX_TURNS = str(_positive_int_env("MISANTHROPIC_TOOL_MAX_TURNS", 50))

# Environment for tool-enabled runs (spike-verified against CLI 2.1.177):
#   ENABLE_TOOL_SEARCH=0  surfaces MCP tools directly to the model instead of
#                         deferring them behind a ToolSearch lookup turn.
#   MCP_TOOL_TIMEOUT      (ms) how long the CLI waits on a tools/call — parked
#                         runs block a call while the HTTP client executes the
#                         tool, so give it a day.
#   MCP_TIMEOUT           (ms) MCP server startup window.
TOOL_RUN_ENV = {
    "ENABLE_TOOL_SEARCH": "0",
    "MCP_TOOL_TIMEOUT": "86400000",
    "MCP_TIMEOUT": "30000",
}
# The agentic web loop legitimately takes longer than GEN_TIMEOUT_S (~120s):
# multiple search turns + the final answer can run ~1-2 min in normal use, so
# default to 10 min and let it be overridden. The watchdog kills the process if
# it overruns instead of letting the request hang forever.
WEB_TIMEOUT_S = float(os.environ.get("MISANTHROPIC_WEB_TIMEOUT_MS", "600000")) / 1000.0

# Web access is a per-request decision, governed by a server-wide *policy*:
#
#   "auto" (default) — honor the request: web runs only for calls that include
#                      the API's web_search tool, exactly like the hosted
#                      Messages API. The faithful, drop-in behavior.
#   "on"             — force web for every request (handy for clients that can't
#                      set tools). This is what MISANTHROPIC_WEB=1 selects.
#   "off"            — hard kill-switch: deny internet regardless of the request.
#
# The policy is mutable so the menu-bar app can flip it at runtime; the server
# resolves it per request via resolve_web(), so a change takes effect on the next
# request. MISANTHROPIC_WEB only sets the initial policy.
_WEB_POLICIES = ("auto", "on", "off")


def _initial_web_policy():
    raw = os.environ.get("MISANTHROPIC_WEB", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return "on"
    if raw in ("0", "false", "no", "off"):
        return "off"
    if raw in _WEB_POLICIES:
        return raw
    return "auto"


_web_policy = _initial_web_policy()


def web_policy():
    return _web_policy


def set_web_policy(value):
    global _web_policy
    if value not in _WEB_POLICIES:
        raise ValueError(f"web policy must be one of {_WEB_POLICIES}")
    _web_policy = value
    return _web_policy


def resolve_web(requested):
    """Decide whether THIS request runs with web search.

    `requested` is whether the client asked for web search (the web_search tool
    was present in the request's `tools`). Policy "on" forces web, "off" denies
    it, and "auto" honors the request — the hosted-API-identical default.
    """
    if _web_policy == "on":
        return True
    if _web_policy == "off":
        return False
    return bool(requested)

# Claude Code's default `-p` system prompt is the full agentic prompt (memory,
# tools, the user's env/identity). We always override it so the proxy behaves
# like the bare Messages API; this neutral default is used when the client sends
# no system prompt of its own (an empty override still leaks env context).
DEFAULT_SYSTEM = "You are a helpful AI assistant."

DEFAULT_MODEL = os.environ.get("MISANTHROPIC_MODEL", os.environ.get("MODEL", "sonnet"))
# Explicit override (if the user set it). When unset we *discover* claude — see
# claude_bin() — because a .app launched from Finder/login gets a minimal PATH
# (/usr/bin:/bin:...) that omits Homebrew (/opt/homebrew/bin), ~/.local/bin, npm
# globals, and node-version-manager dirs, so a plain which("claude") fails even
# though the user's terminal finds it. The #1 "works in my terminal, not from the
# app" footgun.
CLAUDE_BIN = os.environ.get("CLAUDE_BIN")

_COMMON_CLAUDE_PATHS = (
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
    "~/.local/bin/claude",
    "~/.claude/local/claude",
    "~/.npm-global/bin/claude",
    "~/bin/claude",
)
_resolved_claude = None
_resolved_source = None  # how the binary was found — surfaced by the doctor


def claude_bin():
    """Resolve the `claude` executable robustly, regardless of launch PATH.

    Order: explicit CLAUDE_BIN -> current PATH -> the user's login shell (which
    sources their rc, picking up brew/nvm/fnm/asdf) -> common install locations.
    The first success is cached. Falls back to the bare name "claude" (the spawn
    then raises a clear ClaudeError) when nothing is found.
    """
    global _resolved_claude, _resolved_source
    if CLAUDE_BIN:
        _resolved_source = "env:CLAUDE_BIN"
        return CLAUDE_BIN
    if _resolved_claude:
        return _resolved_claude

    source = None
    found = shutil.which("claude")
    if found:
        source = "PATH"
    if not found:
        shell = os.environ.get("SHELL", "/bin/zsh")
        try:
            out = subprocess.run([shell, "-lic", "command -v claude"],
                                 capture_output=True, text=True, timeout=10)
            for line in reversed(out.stdout.strip().splitlines()):
                cand = os.path.expanduser(line.strip())
                if cand and os.path.exists(cand):
                    found = cand
                    source = "login shell"
                    break
        except Exception:
            pass
    if not found:
        for cand in _COMMON_CLAUDE_PATHS:
            cand = os.path.expanduser(cand)
            if os.path.exists(cand):
                found = cand
                source = "known location"
                break

    if found:
        _resolved_claude = found
        _resolved_source = source
    return found or "claude"


def resolution_source():
    """How claude_bin() found the binary ('PATH', 'login shell', ...), or None."""
    return _resolved_source


def reset_resolution():
    """Drop the cached binary path so the next claude_bin() re-runs discovery —
    used by the doctor's re-scan when the CLI moved (e.g. after an update)."""
    global _resolved_claude, _resolved_source
    _resolved_claude = None
    _resolved_source = None


def claude_available():
    """True if a `claude` binary can be located (used by the app's startup check)."""
    b = claude_bin()
    return bool(shutil.which(b) or os.path.exists(os.path.expanduser(b)))


def _child_env():
    """Environment for the claude subprocess: os.environ with PATH augmented so
    both we and claude can find the binary and its runtime (node, etc.) even when
    the app inherited a minimal launchd PATH."""
    env = dict(os.environ)
    parts = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
    extra = [os.path.dirname(claude_bin()), "/opt/homebrew/bin", "/usr/local/bin",
             os.path.expanduser("~/.local/bin"), "/usr/bin", "/bin"]
    for p in extra:
        if p and p not in parts:
            parts.append(p)
    env["PATH"] = os.pathsep.join(parts)
    return env


# Claude Code's `--model` takes tier aliases — "opus" / "sonnet" / "haiku" (and a
# few suffixed forms like "sonnet[1m]"). Clients, though, send full Anthropic API
# model ids: "claude-3-5-sonnet-20241022", "claude-sonnet-4-6", "claude-opus-4-1",
# etc. Passing those raw makes the CLI error on an unrecognized model. So we map any
# requested id to the matching CLI tier, so real SDK code "just works." The response
# still echoes back the model the client asked for (see translate.wrapper_to_message);
# only the CLI invocation is remapped. Unknown strings fall back to the default tier
# rather than failing the request.
_MODEL_ALIASES = ("opus", "sonnet", "haiku")


def cli_model(requested):
    """Translate a requested model into a value Claude Code's --model accepts."""
    if not requested:
        return DEFAULT_MODEL
    m = requested.strip()
    low = m.lower()
    if low.split("[", 1)[0] in _MODEL_ALIASES:  # already an alias (maybe "sonnet[1m]")
        return m
    for tier in _MODEL_ALIASES:                 # full id carries exactly one family
        if tier in low:
            return tier
    return DEFAULT_MODEL                         # unknown -> nearest default, never error
GEN_TIMEOUT_S = float(os.environ.get("GEN_TIMEOUT_MS", "120000")) / 1000.0


class ClaudeError(RuntimeError):
    """A user-facing failure from the local Claude run."""


def _spawn_claude(args, prompt, cwd, env_extra=None):
    """Popen claude, drain stderr concurrently, write the prompt to stdin.

    `--verbose` is talkative; if we left stderr buffered until after the run, the
    ~64 KB OS pipe would fill on long agentic loops and claude would deadlock
    writing into it. The drain thread keeps the pipe empty and stashes lines for
    error messages. stdin is written with BrokenPipe tolerance so a claude that
    rejects flags and exits early doesn't take us down — its stdout/stderr will
    still explain why. Returns (proc, stderr_lines)."""
    env = _child_env()
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # See run_blocking: pin UTF-8 instead of relying on the locale, so
            # the py2app .app (whose launchd-inherited locale is C/ASCII) can
            # still decode emoji in claude's stream-json without crashing.
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=cwd,
            env=env,
        )
    except FileNotFoundError:
        raise ClaudeError(
            "`claude` CLI not found on PATH. Install Claude Code, or set "
            "CLAUDE_BIN to its full path."
        )
    stderr_lines = []

    def _drain():
        try:
            for line in proc.stderr:
                stderr_lines.append(line)
        except Exception:
            pass

    threading.Thread(target=_drain, daemon=True).start()
    try:
        proc.stdin.write(prompt)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    return proc, stderr_lines


def _kill_watchdog(proc, timeout_s):
    """Start a background timer that kills `proc` after `timeout_s` if it's
    still running. Returns (timer, fired) — call timer.cancel() on normal exit;
    if fired[0] is True afterwards, the process was killed by us."""
    fired = [False]

    def _fire():
        fired[0] = True
        try:
            proc.kill()
        except Exception:
            pass

    timer = threading.Timer(timeout_s, _fire)
    timer.daemon = True
    timer.start()
    return timer, fired


def _base_args(model, system, resume=None, persist=False, web=False):
    args = [
        claude_bin(),
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
        args += ["--model", cli_model(model)]
    return args


def tool_args(model, system, allowed_tools, mcp_config_json):
    """Args for a client-tool run. `--tools "ToolSearch"` + ENABLE_TOOL_SEARCH=0
    (in TOOL_RUN_ENV) is the spike-verified combination that exposes exactly
    the MCP tools and zero built-ins; `--strict-mcp-config` keeps the user's
    own MCP servers out of proxy runs."""
    return [
        claude_bin(),
        "-p",
        "--system-prompt", system if system else DEFAULT_SYSTEM,
        "--tools", "ToolSearch",
        "--allowedTools", ",".join(allowed_tools),
        "--mcp-config", mcp_config_json,
        "--strict-mcp-config",
        "--max-turns", TOOL_MAX_TURNS,
        "--no-session-persistence",
        "--model", cli_model(model or DEFAULT_MODEL),
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
    ]


def _collect_blocking(args, payload, cwd, timeout_s):
    """Drive a stream-json run and collapse it into a run_blocking-style wrapper.

    `--output-format json` can't be combined with `--input-format stream-json`
    (the only CLI path that takes image input) and it drops thinking blocks, so
    for image or thinking requests we run the stream-json *output* and rebuild
    the one-shot wrapper from it: the CLI's terminal `result` event already
    carries `result`/`usage`/`stop_reason`/`session_id` — the same shape
    `run_blocking` returns. Returns (wrapper, blocks) where `blocks` is the
    ordered list of thinking/text content blocks the model produced; the joined
    text is also the fallback if the result event lacks the result string.
    """
    proc, stderr_lines = _spawn_claude(args, payload, cwd)
    timer, timed_out = _kill_watchdog(proc, timeout_s)
    wrapper = None
    blocks = []
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            otype = obj.get("type")
            if otype == "assistant":
                for b in (obj.get("message", {}).get("content") or []):
                    if isinstance(b, dict) and b.get("type") in (
                            "text", "thinking", "redacted_thinking"):
                        blocks.append(b)
            elif otype == "result":
                wrapper = obj
        rc = proc.wait()
    finally:
        timer.cancel()

    if timed_out[0]:
        raise ClaudeError("Local Claude timed out. Try again or a faster model.")
    if rc != 0:
        stderr = "".join(stderr_lines).strip()
        detail = stderr or (wrapper.get("result") if isinstance(wrapper, dict) else "") or f"claude exited with code {rc}"
        raise ClaudeError(detail)
    if wrapper is None:
        raise ClaudeError("Claude CLI produced no result.")
    if wrapper.get("is_error"):
        result = wrapper.get("result")
        raise ClaudeError(result if isinstance(result, str) else "Local Claude returned an error.")
    if not isinstance(wrapper.get("result"), str) or not wrapper.get("result"):
        wrapper["result"] = "".join(b.get("text", "") for b in blocks
                                    if b.get("type") == "text")
    return wrapper, blocks


def run_blocking(prompt, model=None, system=None, timeout=None,
                 resume=None, persist=False, cwd=None, input_format="text",
                 collect_blocks=False):
    """Invoke `claude -p --output-format json` and return the parsed wrapper dict.

    The wrapper carries `result` (the text), `stop_reason`, `usage`, and
    `session_id`. Prompt goes in via stdin (no shell, no injection). When
    `resume` is set, the run continues that session. Raises ClaudeError on failure.

    `input_format="stream-json"` feeds an Anthropic-shaped JSONL payload (used to
    carry image content); that path drives stream-json output and collects, since
    `--output-format json` is incompatible with `--input-format stream-json`.

    `collect_blocks=True` (extended thinking) also forces the stream-collection
    path — `--output-format json` drops thinking blocks — and returns
    (wrapper, blocks) with the ordered thinking/text content blocks.
    """
    base = _base_args(model or DEFAULT_MODEL, system, resume=resume, persist=persist)
    timeout_s = timeout if timeout is not None else GEN_TIMEOUT_S
    if input_format == "stream-json" or collect_blocks:
        args = base + ["--output-format", "stream-json", "--verbose"]
        if input_format == "stream-json":
            args += ["--input-format", "stream-json"]
        wrapper, blocks = _collect_blocking(args, prompt, cwd, timeout_s)
        return (wrapper, blocks) if collect_blocks else wrapper
    args = base + ["--output-format", "json"]
    try:
        proc = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            # Pin UTF-8 explicitly. With plain `text=True` the encoding falls
            # back to locale.getpreferredencoding(), which is ASCII in
            # py2app-frozen .app launches (launchd doesn't inherit a UTF-8
            # locale) — and any emoji in claude's output (e.g. "🩵") would
            # trip a UnicodeDecodeError mid-response and 500 the request.
            encoding="utf-8",
            errors="replace",
            timeout=timeout if timeout is not None else GEN_TIMEOUT_S,
            cwd=cwd,
            env=_child_env(),
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


def stream_events(prompt, model=None, system=None, resume=None, persist=False, cwd=None, input_format="text"):
    """Invoke `claude -p --output-format stream-json` and yield events.

    The CLI wraps each Anthropic streaming event in a {"type":"stream_event",
    "event": {...}} line. We unwrap and yield the inner event verbatim, so the
    server can forward it as SSE with no schema translation.

    Yields (kind, obj) tuples where kind is:
      "session" -> obj is the session id (str), emitted once near the start
      "event"   -> obj is a raw Anthropic stream event (message_start, etc.)
      "error"   -> obj is {"message": str}

    `input_format="stream-json"` reads an Anthropic-shaped JSONL payload from
    stdin (carries image content) instead of a plain text prompt.
    """
    args = _base_args(model or DEFAULT_MODEL, system, resume=resume, persist=persist) + [
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
    ]
    if input_format == "stream-json":
        args += ["--input-format", "stream-json"]
    proc, stderr_lines = _spawn_claude(args, prompt, cwd)
    timer, timed_out = _kill_watchdog(proc, GEN_TIMEOUT_S)
    try:
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
    finally:
        timer.cancel()
        # If the consumer abandoned us mid-stream (limit gate tripped, client
        # hung up), the CLI is still generating into a pipe nobody reads —
        # kill it instead of letting it burn tokens until the watchdog fires.
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    if timed_out[0]:
        yield ("error", {"message": f"Local Claude timed out after {GEN_TIMEOUT_S:.0f}s."})
    elif rc != 0:
        stderr = "".join(stderr_lines).strip()
        yield ("error", {"message": stderr or f"claude exited with code {rc}"})


def run_web(prompt, model=None, system=None, resume=None, persist=False, cwd=None, input_format="text"):
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
    if input_format == "stream-json":
        args += ["--input-format", "stream-json"]
    proc, stderr_lines = _spawn_claude(args, prompt, cwd)
    timer, timed_out = _kill_watchdog(proc, WEB_TIMEOUT_S)

    blocks = []
    wrapper = None
    session_id = None
    try:
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
    finally:
        timer.cancel()

    if timed_out[0]:
        raise ClaudeError(f"Local Claude timed out after {WEB_TIMEOUT_S:.0f}s during web search.")
    if rc != 0:
        stderr = "".join(stderr_lines).strip()
        detail = stderr or (wrapper.get("result") if isinstance(wrapper, dict) else "") or f"claude exited with code {rc}"
        raise ClaudeError(detail)
    if wrapper is None:
        raise ClaudeError("Claude CLI produced no result.")
    if wrapper.get("is_error"):
        result = wrapper.get("result")
        raise ClaudeError(result if isinstance(result, str) else "Local Claude returned an error.")

    return blocks, wrapper, session_id
