"""Anthropic-API-compatible HTTP server, backed by the local Claude Code CLI.

Implements just enough of the Messages API that an unmodified Anthropic SDK (or
curl) pointed at this server's base URL works as if it were talking to the
hosted API:

  POST /v1/messages              non-streaming and streaming (SSE)
  POST /v1/messages/count_tokens approximate token count
  GET  /health                   liveness

Two modes, chosen by whether any approved keys are configured (see sessions.py):

  * Stateless mode (no approved keys): each request is ephemeral, like the
    hosted API. Optional single-secret gate via BREAKTHROUGH_API_KEY.
  * Session mode (approved keys configured): the x-api-key both authorizes the
    client and *names a conversation*. The first request under a key starts a
    persistent claude session; later requests `--resume` it, so the chat
    accumulates in one session visible in the Claude Code CLI / desktop app.
    Clients send only the new turn (the session holds prior history).

Stdlib only — no web framework. ThreadingHTTPServer handles concurrent clients.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, sessions, translate
from .claude import ClaudeError, DEFAULT_MODEL, run_blocking, stream_events

# Single shared secret for stateless mode. Ignored when approved keys exist.
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

    def _client_key(self):
        key = self.headers.get("x-api-key") or ""
        auth = self.headers.get("authorization") or ""
        if not key and auth.lower().startswith("bearer "):
            key = auth[7:]
        return key.strip()

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
                "mode": "session" if sessions.session_mode_enabled() else "stateless",
            })
        else:
            self._send_error(404, "not_found_error", f"Unknown path: {self.path}")

    def do_POST(self):
        path = self.path.split("?")[0]

        # Auth differs by mode. In session mode the key must be approved; in
        # stateless mode the optional single secret applies.
        key = self._client_key()
        if sessions.session_mode_enabled():
            if not sessions.is_approved(key):
                return self._send_error(401, "authentication_error", "Unknown or missing API key.")
        elif API_KEY and key != API_KEY:
            return self._send_error(401, "authentication_error", "Invalid API key.")

        try:
            body = self._read_body()
        except (json.JSONDecodeError, ValueError):
            return self._send_error(400, "invalid_request_error", "Request body is not valid JSON.")

        if path == "/v1/messages":
            return self._handle_messages(body, key)
        if path == "/v1/messages/count_tokens":
            return self._send_json(200, translate.count_tokens(body))
        return self._send_error(404, "not_found_error", f"Unknown path: {self.path}")

    # ---- messages -----------------------------------------------------------

    def _handle_messages(self, body, key):
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._send_error(400, "invalid_request_error", "`messages` must be a non-empty array.")

        model = body.get("model") or DEFAULT_MODEL
        system = translate.extract_system(body)
        prompt = translate.messages_to_prompt(messages)
        linked = sessions.session_mode_enabled()  # key-linked session?

        if body.get("stream"):
            return self._stream_messages(prompt, model, system, key if linked else None)

        if linked:
            return self._blocking_session(prompt, model, system, key)

        try:
            wrapper = run_blocking(prompt, model=model, system=system)
        except ClaudeError as e:
            return self._send_error(500, "api_error", str(e))
        self._send_json(200, translate.wrapper_to_message(wrapper, model))

    def _blocking_session(self, prompt, model, system, key):
        """Run linked to the key's session: resume it, persist, serialize."""
        cwd = str(sessions.WORKSPACE)
        with sessions.key_lock(key):
            resume_id = sessions.get_session_id(key)
            try:
                wrapper = run_blocking(prompt, model=model, system=system,
                                       resume=resume_id, persist=True, cwd=cwd)
            except ClaudeError as e:
                if not resume_id:
                    return self._send_error(500, "api_error", str(e))
                # Stale/invalid session id — drop it and start fresh once.
                sessions.forget_session(key)
                try:
                    wrapper = run_blocking(prompt, model=model, system=system,
                                           resume=None, persist=True, cwd=cwd)
                except ClaudeError as e2:
                    return self._send_error(500, "api_error", str(e2))
            sid = wrapper.get("session_id")
            if sid:
                sessions.record_session(key, sid)
        self._send_json(200, translate.wrapper_to_message(wrapper, model))

    def _stream_messages(self, prompt, model, system, key):
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

        # key is None in stateless mode; only acquire the lock when session-linked.
        lock = sessions.key_lock(key) if key else None
        if lock:
            lock.acquire()
        try:
            resume_id = sessions.get_session_id(key) if key else None
            captured_sid = None
            saw_error = False
            for kind, obj in stream_events(prompt, model=model, system=system,
                                           resume=resume_id, persist=bool(key),
                                           cwd=str(sessions.WORKSPACE) if key else None):
                if kind == "session":
                    captured_sid = obj
                elif kind == "event":
                    sse(obj.get("type", "message_delta"), obj)
                elif kind == "error":
                    saw_error = True
                    sse("error", {"type": "error", "error": {"type": "api_error", "message": obj.get("message", "")}})
            if key:
                if saw_error and resume_id and captured_sid is None:
                    sessions.forget_session(key)  # likely a stale session id
                elif captured_sid:
                    sessions.record_session(key, captured_sid)
        except ClaudeError as e:
            sse("error", {"type": "error", "error": {"type": "api_error", "message": str(e)}})
        except (BrokenPipeError, ConnectionResetError):
            pass  # client hung up mid-stream
        finally:
            if lock:
                lock.release()


def serve(host="127.0.0.1", port=8787):
    # Refuse to run session mode on a non-local interface without a workspace —
    # agentic persistence over the network is a foot-gun.
    session_mode = sessions.session_mode_enabled()
    if session_mode and host not in ("127.0.0.1", "localhost", "::1"):
        print(f"refusing to bind session mode to {host}: only approved keys gate access; "
              f"expose deliberately.", file=sys.stderr)

    httpd = ThreadingHTTPServer((host, port), Handler)
    base = f"http://{host}:{port}"
    print(f"breakthrough {__version__} — Anthropic-compatible API on {base}", file=sys.stderr)
    if session_mode:
        print(f"  mode: session  ·  {len(sessions.approved_keys())} approved key(s)  ·  "
              f"sessions persist & resume per key", file=sys.stderr)
    else:
        print(f"  mode: stateless  ·  auth: {'x-api-key required' if API_KEY else 'open (local)'}", file=sys.stderr)
    print(f"  point your client at  base_url={base}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down.", file=sys.stderr)
        httpd.shutdown()
