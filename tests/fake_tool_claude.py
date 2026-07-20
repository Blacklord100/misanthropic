#!/usr/bin/env python3
"""A fake `claude` for tool-calling contract tests.

Unlike FAKE_CLAUDE in test_contract.py, this one honors --mcp-config: it
SPAWNS the real tool_shim from the config and speaks real MCP to it, so a test
exercises the entire chain — server routing, bridge TCP protocol, shim
blocking, park/continue — with only the model faked.

Prompt keywords:
  TOOLCALL   one get_weather call, then a final answer using the result
  TOOLCALL2  parallel get_weather + get_time calls
  [tool_result for  (flattened continuation marker) -> straight final answer

Appends its PID to $FAKE_PID_FILE so tests can tell park-reuse from respawn.
"""
import json
import os
import subprocess
import sys
import threading

args = sys.argv[1:]
prompt = sys.stdin.read()

pid_file = os.environ.get("FAKE_PID_FILE")
if pid_file:
    with open(pid_file, "a") as f:
        f.write(f"{os.getpid()}\n")


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def ev(event):
    out({"type": "stream_event", "event": event})


# ---- spawn the real shim from --mcp-config (absent on plain runs) -----------
shim = None
if "--mcp-config" in args:
    cfg = json.loads(args[args.index("--mcp-config") + 1])
    srv = cfg["mcpServers"]["misanthropic"]
    shim_env = dict(os.environ)
    shim_env.update(srv.get("env") or {})
    shim = subprocess.Popen([srv["command"]] + srv["args"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            encoding="utf-8", bufsize=1, env=shim_env)

_next_id = [0]
_stdin_lock = threading.Lock()
_responses = {}
_resp_cond = threading.Condition()


def _reader():
    for line in shim.stdout:
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        with _resp_cond:
            _responses[m.get("id")] = m
            _resp_cond.notify_all()


if shim is not None:
    threading.Thread(target=_reader, daemon=True).start()


def rpc(method, params=None):
    _next_id[0] += 1
    mid = _next_id[0]
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    with _stdin_lock:
        shim.stdin.write(json.dumps(msg) + "\n")
        shim.stdin.flush()
    with _resp_cond:
        while mid not in _responses:
            _resp_cond.wait(timeout=30)
        return _responses.pop(mid)


if shim is not None:
    rpc("initialize", {"protocolVersion": "2025-11-25"})
    with _stdin_lock:
        shim.stdin.write(json.dumps({"jsonrpc": "2.0",
                                     "method": "notifications/initialized"}) + "\n")
        shim.stdin.flush()
    rpc("tools/list")

# ---- scripted stream ---------------------------------------------------------
if ("--output-format" in args
        and args[args.index("--output-format") + 1] == "stream-json"):
    out({"type": "system", "subtype": "init", "session_id": "sess-tool-1"})

USAGE = {"input_tokens": 11, "output_tokens": 0,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def msg_start():
    ev({"type": "message_start", "message": {
        "id": "msg_tool_fake", "type": "message", "role": "assistant",
        "content": [], "model": "sonnet", "stop_reason": None,
        "stop_sequence": None, "usage": dict(USAGE)}})


def text_block(idx, text):
    ev({"type": "content_block_start", "index": idx,
        "content_block": {"type": "text", "text": ""}})
    ev({"type": "content_block_delta", "index": idx,
        "delta": {"type": "text_delta", "text": text}})
    ev({"type": "content_block_stop", "index": idx})


def tool_block(idx, tid, name, inp):
    ev({"type": "content_block_start", "index": idx,
        "content_block": {"type": "tool_use", "id": tid, "name": name, "input": {}}})
    ev({"type": "content_block_delta", "index": idx,
        "delta": {"type": "input_json_delta", "partial_json": json.dumps(inp)}})
    ev({"type": "content_block_stop", "index": idx})


def turn_end(stop, out_tokens=5):
    ev({"type": "message_delta",
        "delta": {"stop_reason": stop, "stop_sequence": None},
        "usage": {"output_tokens": out_tokens}})
    ev({"type": "message_stop"})


STREAM_OUT = ("--output-format" in args
              and args[args.index("--output-format") + 1] == "stream-json")


def final(text):
    wrapper = {"type": "result", "result": text,
               "usage": {"input_tokens": 11, "output_tokens": 5,
                         "cache_creation_input_tokens": 0,
                         "cache_read_input_tokens": 0},
               "stop_reason": "end_turn", "session_id": "sess-tool-1",
               "is_error": False}
    if not STREAM_OUT:  # plain run_blocking path: one JSON document
        json.dump(wrapper, sys.stdout)
        return
    msg_start()
    text_block(0, text)
    turn_end("end_turn")
    out(wrapper)


if "[tool_result for" in prompt:
    # A flattened-history continuation (dead-park fallback path).
    final("Recovered from flattened history.")
elif "TOOLCALL2" in prompt or "TOOLCALL" in prompt:
    calls = [("toolu_fake_1", "mcp__misanthropic__get_weather", {"location": "Paris"})]
    if "TOOLCALL2" in prompt:
        calls.append(("toolu_fake_2", "mcp__misanthropic__get_time",
                      {"timezone": "Europe/Paris"}))
    msg_start()
    text_block(0, "Let me check.")
    for i, (tid, name, inp) in enumerate(calls, start=1):
        tool_block(i, tid, name, inp)
    turn_end("tool_use")
    # Dispatch tools/call like the real CLI (un-prefixed name, toolu id in
    # _meta) and BLOCK on the shim until the proxy delivers results.
    results = {}

    def call(tid, name, inp):
        results[tid] = rpc("tools/call", {
            "name": name.rsplit("__", 1)[-1], "arguments": inp,
            "_meta": {"claudecode/toolUseId": tid}})

    threads = [threading.Thread(target=call, args=c) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    parts = []
    for tid, _, _ in calls:
        content = (results[tid].get("result") or {}).get("content") or [{}]
        parts.append(str(content[0].get("text", "")))
    final("Got: " + " & ".join(parts))
else:
    final("plain")
