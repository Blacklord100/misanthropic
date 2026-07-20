"""Proxy-side machinery for client-defined tool use: the bridge and the parks.

The Messages API tool loop is stateless HTTP: request 1 (with `tools`) gets a
`tool_use` response; the client executes its tools and sends request 2 with
`tool_result` blocks. The CLI, though, wants to execute tools itself, inside
one process. This module bridges the two:

  * Each tool-enabled request spawns `claude` with an MCP config pointing at
    tool_shim.py. When the model calls a tool, the CLI dispatches tools/call
    to the shim; the shim forwards it here over TCP and BLOCKS.
  * The proxy answers the HTTP request with the turn's `tool_use` blocks and
    PARKS the still-running process (state "parked"), keyed by its tool_use
    ids. Parked processes hold no governor slot.
  * When a request arrives whose last message is exactly the matching
    `tool_result` blocks, the proxy routes the results through the shim; the
    model continues in the same process — thinking state, prompt cache and all.
  * A park that expires (PARK_TTL_S) or dies is silently recovered: the next
    request falls back to a fresh run with the history flattened to text
    (translate._content_to_text keeps tool calls/results pairable).

Spike-verified CLI facts this design rests on (v2.1.177): ENABLE_TOOL_SEARCH=0
surfaces MCP tools directly; a blocked tools/call stalls the CLI indefinitely
(with MCP_TOOL_TIMEOUT raised); tools/call carries the exact stream `toolu_` id
in _meta["claudecode/toolUseId"]; resume + injected tool_result does NOT
continue a pending call (the model re-calls the tool) — hence parking, not
resume.
"""

import hashlib
import json
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

from . import claude as claude_mod
from .claude import ClaudeError

PARK_TTL_S = float(os.environ.get("MISANTHROPIC_TOOL_PARK_TTL_MS", "600000")) / 1000.0
MAX_PARKED = int(os.environ.get("MISANTHROPIC_MAX_PARKED", "8"))
SHIM_BOOT_TIMEOUT_S = 15.0
DISPATCH_WAIT_S = 15.0

_reg_lock = threading.Lock()
_runs = {}          # park_id -> ToolRun
_by_tool_use = {}   # tool_use_id -> park_id

_listener_lock = threading.Lock()
_listener_port = None


# ---- shim process plumbing ---------------------------------------------------

def shim_command():
    """(argv, pythonpath) to run tool_shim as the CLI's MCP server process.

    tool_shim is pure stdlib, so any python3 can host it — which is what makes
    the frozen .app case workable: py2app's sys.executable can't take `-m`, so
    there we fall back to the system python3 with PYTHONPATH pointed at the
    bundled package.
    """
    pkg_parent = str(Path(__file__).resolve().parent.parent)
    if getattr(sys, "frozen", None):
        return ["/usr/bin/python3", "-m", "misanthropic.tool_shim"], pkg_parent
    return [sys.executable, "-m", "misanthropic.tool_shim"], pkg_parent


def _mcp_config(port, token):
    argv, pythonpath = shim_command()
    return json.dumps({"mcpServers": {"misanthropic": {
        "type": "stdio",
        "command": argv[0],
        "args": argv[1:],
        "env": {
            "MISANTHROPIC_BRIDGE_PORT": str(port),
            "MISANTHROPIC_BRIDGE_TOKEN": token,
            "PYTHONPATH": pythonpath,
        },
    }}})


def _ensure_listener():
    """Start (once) the TCP accept loop shims connect back to; returns port."""
    global _listener_port
    with _listener_lock:
        if _listener_port is not None:
            return _listener_port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(64)
        _listener_port = sock.getsockname()[1]

        def _accept_loop():
            while True:
                try:
                    conn, _ = sock.accept()
                except OSError:
                    return
                threading.Thread(target=_serve_shim, args=(conn,),
                                 daemon=True).start()

        threading.Thread(target=_accept_loop, daemon=True).start()
        return _listener_port


def _serve_shim(conn):
    """Per-shim-connection reader: hello -> attach to its run, then route
    tool_call messages into the registry until the socket dies."""
    try:
        rfile = conn.makefile("r", encoding="utf-8")
        wfile = conn.makefile("w", encoding="utf-8")
        hello = json.loads(rfile.readline() or "{}")
        run = _runs.get(hello.get("token")) if hello.get("op") == "hello" else None
        if run is None:
            conn.close()
            return
        run.attach_shim(wfile)
        for line in rfile:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("op") == "tool_call":
                run.on_tool_call(msg)
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


# ---- the run -----------------------------------------------------------------

def fingerprint(model, system):
    return (model or "", hashlib.sha256((system or "").encode()).hexdigest())


class ToolRun:
    """One tool-enabled `claude` process, from spawn through parks to exit."""

    def __init__(self, tools, model, system, fp=None):
        self.park_id = uuid.uuid4().hex
        self.tools = tools
        # The continuation-match fingerprint is computed from the request's
        # RAW system (the caller passes it in); `system` here may carry the
        # tool_choice nudge.
        self.fingerprint = fp if fp is not None else fingerprint(model, system)
        self.model = model
        self.system = system
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.state = "generating"    # generating | parked | resuming | dead
        self.proc = None
        self.stderr_lines = []
        self._lines = None
        self._shim_wfile = None
        self._shim_ready = threading.Event()
        self.pending = {}            # tool_use_id -> rpc_id (current turn)
        self.parked_at = None
        self._ttl_timer = None
        self._watchdog = None
        self._watchdog_fired = None
        # Per-turn parse state, populated by read_turn():
        self.turn_stop_reason = None
        self.turn_blocks = []
        self.turn_usage = {}
        self.wrapper = None
        self.message_id = "msg_" + self.park_id[:24]

    # -- spawn / shim attach --

    def spawn(self, prompt, input_format="text"):
        port = _ensure_listener()
        with _reg_lock:
            _runs[self.park_id] = self
        args = claude_mod.tool_args(
            self.model, self.system,
            [f"mcp__misanthropic__{t['name']}" for t in self.tools],
            _mcp_config(port, self.park_id))
        if input_format == "stream-json":
            args += ["--input-format", "stream-json"]
        self.proc, self.stderr_lines = claude_mod._spawn_claude(
            args, prompt, cwd=None, env_extra=claude_mod.TOOL_RUN_ENV)
        self._lines = iter(self.proc.stdout)

    def attach_shim(self, wfile):
        with self.lock:
            self._shim_wfile = wfile
        self._send_shim({"op": "tools", "tools": self.tools})
        self._shim_ready.set()

    def _send_shim(self, obj):
        wfile = self._shim_wfile
        if wfile is None:
            raise ClaudeError("Tool bridge lost the shim connection.")
        try:
            wfile.write(json.dumps(obj) + "\n")
            wfile.flush()
        except OSError:
            raise ClaudeError("Tool bridge lost the shim connection.")

    def on_tool_call(self, msg):
        with self.cond:
            tid = msg.get("tool_use_id")
            if tid:
                self.pending[tid] = msg.get("rpc_id")
                with _reg_lock:
                    _by_tool_use[tid] = self.park_id
            self.cond.notify_all()

    # -- turn reading --

    def read_turn(self):
        """Yield this turn's raw stream events, ending after message_stop.

        Populates turn_stop_reason / turn_blocks / turn_usage; on the final
        turn the terminal `result` wrapper is read by finish(). Yields
        ("error", {...}) instead if the CLI dies mid-turn.
        """
        self.turn_stop_reason = None
        self.turn_blocks = []
        self.turn_usage = {}
        builder = _BlockBuilder()
        self._arm_watchdog()
        try:
            for line in self._lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                otype = obj.get("type")
                if otype == "stream_event":
                    event = obj.get("event")
                    if not isinstance(event, dict):
                        continue
                    builder.feed(event)
                    et = event.get("type")
                    if et == "message_start":
                        u = ((event.get("message") or {}).get("usage") or {})
                        self.turn_usage.update(u)
                    elif et == "message_delta":
                        d = event.get("delta") or {}
                        if d.get("stop_reason"):
                            self.turn_stop_reason = d["stop_reason"]
                        u = event.get("usage") or {}
                        if u.get("output_tokens") is not None:
                            self.turn_usage["output_tokens"] = u["output_tokens"]
                    yield ("event", event)
                    if et == "message_stop":
                        self.turn_blocks = builder.blocks()
                        return
                elif otype == "result":
                    # A result with no streamed message = the CLI failed fast
                    # (bad flags, error_max_turns, ...).
                    self.wrapper = obj
                    if obj.get("is_error"):
                        msg = obj.get("result")
                        yield ("error", {"message": msg if isinstance(msg, str)
                                         else "Local Claude returned an error."})
                        return
            # EOF without message_stop: the process died on us.
            rc = self.proc.wait()
            if self._watchdog_fired and self._watchdog_fired[0]:
                yield ("error", {"message": f"Local Claude timed out after "
                                            f"{claude_mod.GEN_TIMEOUT_S:.0f}s."})
                return
            stderr = "".join(self.stderr_lines).strip()
            yield ("error", {"message": stderr or f"claude exited with code {rc}"})
        finally:
            self._disarm_watchdog()

    def finish(self):
        """After an end_turn turn: drain to the terminal result event."""
        if self.wrapper is None:
            for line in self._lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "result":
                    self.wrapper = obj
                    break
        try:
            self.proc.wait(timeout=10)
        except Exception:
            pass
        return self.wrapper or {}

    def _arm_watchdog(self):
        self._watchdog, self._watchdog_fired = claude_mod._kill_watchdog(
            self.proc, claude_mod.GEN_TIMEOUT_S)

    def _disarm_watchdog(self):
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None

    # -- park lifecycle --

    def wait_for_dispatch(self, tool_use_ids):
        """Block until the CLI has dispatched tools/call for every id (the
        shim received them and is blocking). Parking without full dispatch
        would strand the continuation."""
        deadline = time.monotonic() + DISPATCH_WAIT_S
        with self.cond:
            while not all(t in self.pending for t in tool_use_ids):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.cond.wait(remaining)
            return True

    def park(self):
        with self.lock:
            self.state = "parked"
            self.parked_at = time.time()
            self._ttl_timer = threading.Timer(PARK_TTL_S, self._expire)
            self._ttl_timer.daemon = True
            self._ttl_timer.start()
        _evict_over_cap()

    def _expire(self):
        self.destroy()

    def claim_for_resume(self):
        """parked -> resuming, atomically. False if this park isn't available
        (already resuming, expired, or dead)."""
        with self.lock:
            if self.state != "parked":
                return False
            self.state = "resuming"
            if self._ttl_timer is not None:
                self._ttl_timer.cancel()
                self._ttl_timer = None
            return True

    def deliver_results(self, results):
        """Feed the client's tool_result blocks to the blocked shim calls."""
        with self.lock:
            pending = dict(self.pending)
            self.pending = {}
        with _reg_lock:
            for tid in pending:
                _by_tool_use.pop(tid, None)
        for r in results:
            rpc_id = pending.get(r["tool_use_id"])
            self._send_shim({
                "op": "tool_result",
                "rpc_id": rpc_id,
                "content": _result_content(r["content"]),
                "is_error": r["is_error"],
            })
        with self.lock:
            self.state = "generating"

    def destroy(self):
        """Kill the process, notify the shim, drop registry entries."""
        with self.lock:
            self.state = "dead"
            if self._ttl_timer is not None:
                self._ttl_timer.cancel()
                self._ttl_timer = None
            pending = dict(self.pending)
        try:
            self._send_shim({"op": "shutdown"})
        except Exception:
            pass
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.kill()
            except Exception:
                pass
        with _reg_lock:
            _runs.pop(self.park_id, None)
            for tid in pending:
                _by_tool_use.pop(tid, None)


def _result_content(content):
    """Normalize a tool_result's `content` (string or block list) into MCP
    content blocks for the shim's reply."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append({"type": "text", "text": b.get("text", "")})
            elif isinstance(b, dict) and b.get("type") == "image":
                src = b.get("source") or {}
                out.append({"type": "image", "data": src.get("data", ""),
                            "mimeType": src.get("media_type", "image/png")})
        return out
    return []


class _BlockBuilder:
    """Rebuild ordered content blocks from partial stream events."""

    def __init__(self):
        self._blocks = {}   # index -> block dict under construction
        self._json_parts = {}

    def feed(self, event):
        et = event.get("type")
        idx = event.get("index")
        if et == "content_block_start":
            cb = dict(event.get("content_block") or {})
            self._blocks[idx] = cb
            if cb.get("type") == "tool_use":
                self._json_parts[idx] = []
        elif et == "content_block_delta":
            d = event.get("delta") or {}
            blk = self._blocks.get(idx)
            if blk is None:
                return
            dt = d.get("type")
            if dt == "text_delta":
                blk["text"] = blk.get("text", "") + (d.get("text") or "")
            elif dt == "thinking_delta":
                blk["thinking"] = blk.get("thinking", "") + (d.get("thinking") or "")
            elif dt == "signature_delta":
                blk["signature"] = d.get("signature") or blk.get("signature", "")
            elif dt == "input_json_delta":
                self._json_parts.setdefault(idx, []).append(d.get("partial_json") or "")
        elif et == "content_block_stop":
            blk = self._blocks.get(idx)
            if blk is not None and blk.get("type") == "tool_use":
                raw = "".join(self._json_parts.get(idx, []))
                try:
                    blk["input"] = json.loads(raw) if raw.strip() else (blk.get("input") or {})
                except json.JSONDecodeError:
                    blk["input"] = blk.get("input") or {}

    def blocks(self):
        return [self._blocks[i] for i in sorted(self._blocks)]


# ---- registry queries (used by server routing) -------------------------------

def find_park(tool_use_ids):
    """The single live ToolRun covering ALL ids, or None. `partial` in the
    second slot flags ids that straddle parks or cover a park incompletely —
    the 400 case."""
    with _reg_lock:
        park_ids = {_by_tool_use.get(t) for t in tool_use_ids}
        if park_ids == {None}:
            return None, False
        if len(park_ids) != 1 or None in park_ids:
            return None, True
        run = _runs.get(park_ids.pop())
    if run is None:
        return None, False
    if set(run.pending) != set(tool_use_ids):
        return None, True
    return run, False


def parked_count():
    with _reg_lock:
        return sum(1 for r in _runs.values() if r.state == "parked")


def _evict_over_cap():
    """Oldest-first eviction beyond MAX_PARKED, so parks can't accumulate
    node processes without bound."""
    while True:
        with _reg_lock:
            parked = sorted((r for r in _runs.values() if r.state == "parked"),
                            key=lambda r: r.parked_at or 0)
        if len(parked) <= MAX_PARKED:
            return
        parked[0].destroy()


def start_run(tools, model, system, prompt, input_format="text", fp=None):
    """Spawn a tool-enabled run; returns the ToolRun with the shim attached.

    Raises ClaudeError if the shim never phones home (bundled-python problems,
    firewalled loopback, ...) — surfaced as a 500."""
    run = ToolRun(tools, model, system, fp=fp)
    run.spawn(prompt, input_format=input_format)
    if not run._shim_ready.wait(SHIM_BOOT_TIMEOUT_S):
        # Distinguish "CLI died instantly" (bad flags, logged out) from
        # "shim never connected".
        if run.proc.poll() is not None:
            stderr = "".join(run.stderr_lines).strip()
            run.destroy()
            raise ClaudeError(stderr or f"claude exited with code {run.proc.returncode}")
        run.destroy()
        raise ClaudeError("Tool bridge failed to start (shim never connected).")
    return run
