"""Run the OpenAI Codex CLI as a completion backend.

Second backend next to claude.py: fulfills Messages-API requests by shelling
out to the user's logged-in `codex` CLI (their ChatGPT subscription is the
auth). v1 scope: text, images, and extended thinking (codex "reasoning" items
map to Anthropic `thinking` blocks). Tools / web search / key-linked sessions
stay Claude-only — the router never sends those here.

Spike-verified invocation (codex-cli 0.144.6):
  codex exec --json --color never -s read-only --ephemeral
             --skip-git-repo-check --ignore-user-config -C <workspace>
             [-m model] [-c model_reasoning_effort="…"] [-i img…] -
  * prompt on stdin ("-"), JSONL events out: thread.started / item.completed
    {reasoning|agent_message|command_execution} / turn.completed {usage}.
  * usage carries cached_input_tokens (→ cache_read) and reasoning_output_tokens.
  * There is NO system-prompt flag; an AGENTS.md in the -C workspace serves as
    one (verified). Each run gets a throwaway workspace dir with AGENTS.md —
    also the privacy fence: an empty cwd means the read-only sandbox has no
    user files to pull into context.
  * CODEX_HOME selects the account (auth.json/config.toml live there).
"""

import base64
import json
import os
import shutil
import subprocess
import tempfile
import uuid

from . import claude as claude_mod
from . import settings
from .errors import BackendError

CODEX_BIN = os.environ.get("CODEX_BIN")
CODEX_TIMEOUT_S = float(os.environ.get("MISANTHROPIC_CODEX_TIMEOUT_MS", "300000")) / 1000.0

_COMMON_CODEX_PATHS = (
    "/opt/homebrew/bin/codex",
    "/usr/local/bin/codex",
    "~/.local/bin/codex",
    "~/.npm-global/bin/codex",
    "~/bin/codex",
)
_resolved_codex = None


class CodexError(BackendError):
    """A user-facing failure from the local Codex run."""


def codex_bin():
    """Resolve the `codex` executable (same PATH pragmatism as claude_bin)."""
    global _resolved_codex
    if CODEX_BIN:
        return CODEX_BIN
    if _resolved_codex:
        return _resolved_codex
    found = shutil.which("codex")
    if not found:
        shell = os.environ.get("SHELL", "/bin/zsh")
        try:
            out = subprocess.run([shell, "-lic", "command -v codex"],
                                 capture_output=True, text=True, timeout=10)
            for line in reversed(out.stdout.strip().splitlines()):
                cand = os.path.expanduser(line.strip())
                if cand and os.path.exists(cand):
                    found = cand
                    break
        except Exception:
            pass
    if not found:
        for cand in _COMMON_CODEX_PATHS:
            cand = os.path.expanduser(cand)
            if os.path.exists(cand):
                found = cand
                break
    if found:
        _resolved_codex = found
    return found or "codex"


def codex_available():
    b = codex_bin()
    return bool(shutil.which(b) or os.path.exists(os.path.expanduser(b)))


def reset_resolution():
    global _resolved_codex
    _resolved_codex = None


# The model a client requests is an Anthropic id; map its tier onto codex's
# reasoning-effort knob so opus-ish requests think harder. The codex model
# itself is the user's choice (settings "codex_model"), else codex's default.
_EFFORT = {"opus": "high", "sonnet": "medium", "haiku": "low"}


def reasoning_effort(requested_model):
    return _EFFORT.get(claude_mod.cli_model(requested_model).split("[", 1)[0], "medium")


def served_model_label():
    """What actually ran, for the dashboard: codex ignores the requested
    Anthropic id entirely. `codex_model` setting when set (that's what -m
    passes), else codex's own built-in default (which the JSONL doesn't name)."""
    m = settings.get("codex_model")
    return f"codex:{m}" if m else "codex:default-model"


_AGENTS_PREAMBLE = (
    "Answer the user directly from your knowledge. Do not run commands, do "
    "not read or write files, do not explore the filesystem.\n\n"
)


def _workspace(system):
    """A throwaway per-run workspace whose AGENTS.md carries the system
    prompt (codex has no --system-prompt flag; this is the verified channel)."""
    from . import sessions
    base = sessions.CONFIG_DIR / "codex-workspace"
    os.makedirs(base, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="run-", dir=str(base))
    with open(os.path.join(workdir, "AGENTS.md"), "w") as f:
        f.write(_AGENTS_PREAMBLE + (system or claude_mod.DEFAULT_SYSTEM))
    return workdir


_IMG_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
               "image/webp": ".webp"}


def _write_images(image_blocks, workdir):
    paths = []
    for blk in image_blocks or []:
        src = blk.get("source") or {}
        if src.get("type") != "base64" or not src.get("data"):
            continue
        suffix = _IMG_SUFFIX.get(src.get("media_type"), ".png")
        path = os.path.join(workdir, f"img-{len(paths)}{suffix}")
        try:
            with open(path, "wb") as f:
                f.write(base64.b64decode(src["data"]))
            paths.append(path)
        except Exception:
            continue
    return paths


def run_blocking(prompt, model=None, system=None, images=None, timeout=None,
                 account=None):
    """One codex completion. Returns (wrapper, blocks) — the wrapper shaped
    exactly like claude's (result/usage/stop_reason/session_id) so the rest of
    the pipeline can't tell backends apart; `blocks` are ordered
    thinking/text content blocks (thinking gating happens at the server)."""
    workdir = _workspace(system)
    try:
        args = [
            codex_bin(), "exec",
            "--json", "--color", "never",
            "-s", "read-only",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "-C", workdir,
            "-c", f'model_reasoning_effort="{reasoning_effort(model)}"',
        ]
        codex_model = settings.get("codex_model")
        if codex_model:
            args += ["-m", str(codex_model)]
        for path in _write_images(images, workdir):
            args += ["-i", path]
        args += ["-"]  # prompt on stdin

        proc, stderr_lines = claude_mod._spawn_claude(args, prompt, cwd=workdir,
                                                      account=account)
        timer, timed_out = claude_mod._kill_watchdog(
            proc, timeout if timeout is not None else CODEX_TIMEOUT_S)
        blocks = []
        usage = {}
        thread_id = None
        fail = None
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
                if otype == "thread.started":
                    thread_id = obj.get("thread_id")
                elif otype == "item.completed":
                    item = obj.get("item") or {}
                    itype = item.get("type")
                    if itype == "reasoning":
                        blocks.append({"type": "thinking",
                                       "thinking": item.get("text", ""),
                                       "signature": ""})
                    elif itype == "agent_message":
                        blocks.append({"type": "text", "text": item.get("text", "")})
                    # command_execution and anything else is dropped: the
                    # AGENTS.md preamble forbids it, and nothing a command
                    # printed belongs in an API response.
                elif otype == "turn.failed":
                    err = obj.get("error") or {}
                    fail = err.get("message") or json.dumps(err)[:300]
                elif otype == "turn.completed":
                    usage = obj.get("usage") or {}
            rc = proc.wait()
        finally:
            timer.cancel()

        if timed_out[0]:
            raise CodexError(f"Local Codex timed out after "
                             f"{timeout or CODEX_TIMEOUT_S:.0f}s.")
        if fail:
            raise CodexError(fail)
        if rc != 0:
            stderr = "".join(stderr_lines).strip()
            raise CodexError(stderr or f"codex exited with code {rc}")
        if not blocks:
            raise CodexError("Codex produced no output.")

        # Codex reports cached prompt tokens inside input_tokens; split them
        # out the way the Anthropic API does so usage (and the savings math)
        # keeps its semantics.
        cached = usage.get("cached_input_tokens", 0) or 0
        total_in = usage.get("input_tokens", 0) or 0
        wrapper = {
            "result": "".join(b["text"] for b in blocks if b["type"] == "text"),
            "usage": {
                "input_tokens": max(0, total_in - cached),
                "output_tokens": usage.get("output_tokens", 0) or 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": cached,
            },
            "stop_reason": "end_turn",
            "session_id": thread_id or uuid.uuid4().hex,
        }
        return wrapper, blocks
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def stream_shim(prompt, model_requested, system=None, images=None,
                thinking=False, account=None):
    """Codex has no token-level streaming; run fully, then replay as a single
    well-formed message in stream_events' (kind, obj) shape — a drop-in for
    the server's _open_stream, which only commits SSE after the first event
    (so a codex failure here still fails over cleanly)."""
    from . import translate
    try:
        wrapper, blocks = run_blocking(prompt, model=model_requested,
                                       system=system, images=images,
                                       account=account)
    except CodexError as e:
        yield ("error", {"message": str(e)})
        return
    content = [b for b in blocks
               if thinking or b.get("type") not in ("thinking", "redacted_thinking")]
    if not any(b.get("type") == "text" for b in content):
        content.append({"type": "text", "text": wrapper.get("result", "")})
    for _et, data in translate.tool_sse_events(
            content, wrapper["usage"], "end_turn", model_requested,
            translate._message_id(wrapper)):
        yield ("event", data)


def login_status(account=None):
    """Cheap login probe: `codex login status` (no generation, no tokens).
    Returns (ok: bool|None, detail: str)."""
    try:
        out = subprocess.run([codex_bin(), "login", "status"],
                             capture_output=True, text=True, timeout=15,
                             env=claude_mod._child_env(account))
        text = ((out.stdout or "") + (out.stderr or "")).strip()
        if out.returncode == 0 and "logged in" in text.lower() \
                and "not logged in" not in text.lower():
            return True, text.splitlines()[0][:200] if text else "Logged in"
        return False, text[:200] or f"codex login status exited {out.returncode}"
    except FileNotFoundError:
        return None, "codex CLI not found"
    except Exception as e:
        return None, f"probe failed: {e}"[:200]
