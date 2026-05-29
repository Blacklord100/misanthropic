"""Anthropic-API-compatible HTTP server, backed by the local Claude Code CLI.

Implements just enough of the Messages API that an unmodified Anthropic SDK (or
curl) pointed at this server's base URL works as if it were talking to the
hosted API:

  POST /v1/messages              non-streaming and streaming (SSE)
  POST /v1/messages/count_tokens approximate token count
  GET  /health                   liveness

Stdlib only — no web framework. ThreadingHTTPServer handles concurrent clients.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, translate
from .claude import ClaudeError, DEFAULT_MODEL, run_blocking, stream_events

# Optional shared secret. If set, clients must send a matching x-api-key (or
# Authorization: Bearer). If unset, the server is open (it's local-only anyway).
API_KEY = os.environ.get("BREAKTHROUGH_API_KEY")


class Handler(BaseHTTPRequestHandler):
    server_version = f"breakthrough/{__version__}"
    protocol_version = "HTTP/1.1"

    # ---- helpers ------------------------------------------------------------

    def log_message(self, fmt, *args):
        sys.stderr.write(f"  {self.address_string()} - {fmt % args}\n")

    def _send_json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, status, etype, message):
        self._send_json(status, {"type": "error", "error": {"type": etype, "message": message}})

    def _authorized(self):
        if not API_KEY:
            return True
        key = self.headers.get("x-api-key") or ""
        auth = self.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            key = auth[7:]
        return key == API_KEY

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw or b"{}")

    # ---- routes -------------------------------------------------------------

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/health"):
            self._send_json(200, {
                "status": "ok",
                "service": "breakthrough",
                "version": __version__,
                "backend": "claude-code-cli",
            })
        else:
            self._send_error(404, "not_found_error", f"Unknown path: {self.path}")

    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._authorized():
            return self._send_error(401, "authentication_error", "Invalid API key.")
        try:
            body = self._read_body()
        except (json.JSONDecodeError, ValueError):
            return self._send_error(400, "invalid_request_error", "Request body is not valid JSON.")

        if path == "/v1/messages":
            return self._handle_messages(body)
        if path == "/v1/messages/count_tokens":
            return self._send_json(200, translate.count_tokens(body))
        return self._send_error(404, "not_found_error", f"Unknown path: {self.path}")

    # ---- messages -----------------------------------------------------------

    def _handle_messages(self, body):
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._send_error(400, "invalid_request_error", "`messages` must be a non-empty array.")

        model = body.get("model") or DEFAULT_MODEL
        system = translate.extract_system(body)
        prompt = translate.messages_to_prompt(messages)

        if body.get("stream"):
            return self._stream_messages(prompt, model, system)

        try:
            wrapper = run_blocking(prompt, model=model, system=system)
        except ClaudeError as e:
            return self._send_error(500, "api_error", str(e))
        self._send_json(200, translate.wrapper_to_message(wrapper, model))

    def _stream_messages(self, prompt, model, system):
        # SSE has no Content-Length and we don't chunk-encode, so the client
        # detects end-of-stream by EOF. Close the connection when done.
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def sse(event_type, data):
            chunk = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
            self.wfile.write(chunk.encode())
            self.wfile.flush()

        try:
            for kind, obj in stream_events(prompt, model=model, system=system):
                if kind == "event":
                    sse(obj.get("type", "message_delta"), obj)
                elif kind == "error":
                    sse("error", {"type": "error", "error": {"type": "api_error", "message": obj.get("message", "")}})
        except ClaudeError as e:
            sse("error", {"type": "error", "error": {"type": "api_error", "message": str(e)}})
        except (BrokenPipeError, ConnectionResetError):
            pass  # client hung up mid-stream


def serve(host="127.0.0.1", port=8787):
    httpd = ThreadingHTTPServer((host, port), Handler)
    base = f"http://{host}:{port}"
    print(f"breakthrough {__version__} — Anthropic-compatible API on {base}", file=sys.stderr)
    print(f"  backend: local `claude` CLI  ·  auth: {'x-api-key required' if API_KEY else 'open (local)'}", file=sys.stderr)
    print(f"  point your client at  base_url={base}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down.", file=sys.stderr)
        httpd.shutdown()
