"""MCP stdio server exposing a request's client-defined tools to Claude Code.

Spawned BY the `claude` CLI (via the --mcp-config the proxy generates), one per
tool-enabled run. It has two faces:

  * MCP over stdio with the CLI: newline-delimited JSON-RPC (verified against
    CLI 2.1.177). It advertises the client's tools and, on tools/call, BLOCKS
    until the real result arrives — that stall is the proxy's park point: the
    CLI waits indefinitely (MCP_TOOL_TIMEOUT is raised by the proxy) while the
    HTTP client goes off to execute the tool.

  * a TCP line-protocol with the proxy (tool_bridge.py) on
    127.0.0.1:$MISANTHROPIC_BRIDGE_PORT:

      shim  -> {"op":"hello","token": $MISANTHROPIC_BRIDGE_TOKEN}
      proxy -> {"op":"tools","tools":[{name,description,input_schema},...]}
      shim  -> {"op":"tool_call","rpc_id":N,"tool_use_id":"toolu_...",
                "name":"...","arguments":{...}}
      proxy -> {"op":"tool_result","rpc_id":N,"content":[...],"is_error":false}
      proxy -> {"op":"shutdown"}

Tool definitions ride this channel (not argv/env) so huge schemas can't hit
ARG_MAX. Pure stdlib.
"""

import json
import os
import queue
import socket
import sys
import threading

_out_lock = threading.Lock()


def _send_rpc(obj):
    with _out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


class Bridge:
    """The proxy-side connection: request/response over one TCP line stream."""

    def __init__(self):
        port = int(os.environ["MISANTHROPIC_BRIDGE_PORT"])
        token = os.environ["MISANTHROPIC_BRIDGE_TOKEN"]
        self._sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self._rfile = self._sock.makefile("r", encoding="utf-8")
        self._wfile = self._sock.makefile("w", encoding="utf-8")
        self._wlock = threading.Lock()
        self._waiters = {}   # rpc_id -> Queue with the tool_result payload
        self._tools = queue.Queue()
        self.send({"op": "hello", "token": token})
        threading.Thread(target=self._reader, daemon=True).start()

    def send(self, obj):
        with self._wlock:
            self._wfile.write(json.dumps(obj) + "\n")
            self._wfile.flush()

    def _reader(self):
        try:
            for line in self._rfile:
                line = line.strip()
                if not line:
                    continue
                msg = json.loads(line)
                op = msg.get("op")
                if op == "tools":
                    self._tools.put(msg.get("tools") or [])
                elif op == "tool_result":
                    q = self._waiters.pop(msg.get("rpc_id"), None)
                    if q is not None:
                        q.put(msg)
                elif op == "shutdown":
                    os._exit(0)
        except Exception:
            pass
        # The proxy went away: a parked run whose bridge died can never get
        # results — exit so the CLI sees the server drop rather than hang.
        os._exit(1)

    def tools(self):
        return self._tools.get(timeout=15)

    def call(self, rpc_id, tool_use_id, name, arguments):
        q = queue.Queue()
        self._waiters[rpc_id] = q
        self.send({"op": "tool_call", "rpc_id": rpc_id,
                   "tool_use_id": tool_use_id, "name": name,
                   "arguments": arguments})
        return q.get()  # blocks — this stall IS the park


def main():
    bridge = Bridge()
    tools = bridge.tools()
    mcp_tools = [{"name": t["name"],
                  "description": t.get("description") or "",
                  "inputSchema": t.get("input_schema") or {"type": "object"}}
                 for t in tools]

    def handle(msg):
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            pv = (msg.get("params") or {}).get("protocolVersion", "2024-11-05")
            _send_rpc({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": pv,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "misanthropic", "version": "1"}}})
        elif method == "tools/list":
            _send_rpc({"jsonrpc": "2.0", "id": mid, "result": {"tools": mcp_tools}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            tool_use_id = (params.get("_meta") or {}).get("claudecode/toolUseId")
            res = bridge.call(mid, tool_use_id, params.get("name"),
                              params.get("arguments") or {})
            _send_rpc({"jsonrpc": "2.0", "id": mid, "result": {
                "content": res.get("content") or [],
                "isError": bool(res.get("is_error"))}})
        elif mid is not None:
            _send_rpc({"jsonrpc": "2.0", "id": mid, "result": {}})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Each request on its own thread: parallel tools/call must block
        # independently while list/ping keep answering.
        threading.Thread(target=handle, args=(msg,), daemon=True).start()

    # CLI closed stdin (it exited): nothing left to serve.


if __name__ == "__main__":
    main()
