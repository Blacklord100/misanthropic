"""Anthropic-API-compatible HTTP server, backed by the local Claude Code CLI.

Implements just enough of the Messages API that an unmodified Anthropic SDK (or
curl) pointed at this server's base URL works as if it were talking to the
hosted API:

  POST /v1/messages              non-streaming and streaming (SSE)
  POST /v1/messages/count_tokens approximate token count
  GET  /health                   liveness

Two modes, chosen by whether any approved keys are configured (see sessions.py):

  * Stateless mode (no approved keys): each request is ephemeral, like the
    hosted API. Optional single-secret gate via MISANTHROPIC_API_KEY.
  * Session mode (approved keys configured): the x-api-key both authorizes the
    client and *names a conversation*. The first request under a key starts a
    persistent claude session; later requests `--resume` it, so the chat
    accumulates in one session visible in the Claude Code CLI / desktop app.
    Clients send only the new turn (the session holds prior history).

Stdlib only — no web framework. ThreadingHTTPServer handles concurrent clients.
"""

import json
import mimetypes
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import (__version__, dashboard, doctor, history, request_log, savings,
               sessions, settings, translate)
from . import claude as claude_mod
from .claude import ClaudeError, resolve_web, run_blocking, run_web, stream_events, web_policy

# Single shared secret for stateless mode. Ignored when approved keys exist.
API_KEY = os.environ.get("MISANTHROPIC_API_KEY")

# Compiled dashboard assets (see frontend/). When present they are served at /;
# the legacy self-contained page in dashboard.py remains the fallback so a
# source checkout without a frontend build still gets a working UI.
STATIC_DIR = Path(__file__).parent / "static"

# ---- concurrency governor ----------------------------------------------------
#
# Every request spawns a full `claude` process (a node app), so unbounded
# concurrency means a request burst forks a process storm. Cap concurrent CLI
# runs; a request that can't get a slot within the queue window is refused with
# the API's own 529 overloaded_error, which official SDKs back off and retry —
# exactly the hosted API's behavior under load.
#
# The limit is adjustable at runtime (Settings page / POST /admin/settings), so
# it's a Condition-based counter rather than a semaphore: raising the limit
# immediately wakes queued waiters, lowering it drains naturally as in-flight
# runs finish. Startup order: MISANTHROPIC_MAX_CONCURRENCY env wins, then the
# persisted setting (applied in make_httpd), then the default of 8.
DEFAULT_MAX_CONCURRENCY = 8
QUEUE_WAIT_S = float(os.environ.get("MISANTHROPIC_QUEUE_WAIT_MS", "30000")) / 1000.0


class Governor:
    """A resizable concurrency gate. acquire() blocks up to `timeout` for a
    slot; set_limit() applies live without disturbing holders."""

    def __init__(self, limit):
        self._limit = max(1, int(limit))
        self._active = 0
        self._cond = threading.Condition()

    def acquire(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while self._active >= self._limit:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._cond.wait(remaining)
            self._active += 1
            return True

    def release(self):
        with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify_all()

    def set_limit(self, n):
        with self._cond:
            self._limit = max(1, min(int(n), 64))
            self._cond.notify_all()
        return self._limit

    @property
    def limit(self):
        return self._limit

    @property
    def in_flight(self):
        return self._active


_governor = Governor(os.environ.get("MISANTHROPIC_MAX_CONCURRENCY",
                                    DEFAULT_MAX_CONCURRENCY))


def requests_in_flight():
    """How many CLI runs hold a governor slot right now. The auto-updater uses
    this to swap the bundle only when nothing would be killed mid-generation."""
    return _governor.in_flight


def classify_claude_error(message):
    """Map a CLI failure onto the hosted API's error taxonomy, so SDK retry
    logic behaves identically to api.anthropic.com."""
    m = (message or "").lower()
    if doctor.login_looks_like_auth_error(m):
        return 401, "authentication_error"
    if any(s in m for s in ("rate limit", "overloaded", "usage limit", "429", "529",
                            "too many requests", "capacity")):
        return 529, "overloaded_error"
    if "timed out" in m:
        return 504, "api_error"
    return 500, "api_error"


def _request_wants_web(body):
    """True if the client's request asked for web search — i.e. the API's
    web_search server tool appears in `tools`. This is the signal the "auto" web
    policy honors, mirroring the hosted Messages API where web search is a
    per-request tool, not a server-wide setting. Matches every web_search tool
    version (e.g. web_search_20260209, web_search_20250305)."""
    tools = body.get("tools")
    if not isinstance(tools, list):
        return False
    return any(
        isinstance(t, dict) and str(t.get("type", "")).startswith("web_search")
        for t in tools
    )

# The activity log keeps the full prompt/response text so the dashboard can show
# it in full when a row is expanded. This cap is only a runaway guard for a
# pathologically huge message — normal text passes through untouched.
MAX_LOG_TEXT = 100_000


def _is_invalid_session_error(message):
    """True if the error looks like a bad/missing session id (vs. transient)."""
    m = message.lower()
    return "resume" in m or "session id" in m or "no conversation found" in m


class Handler(BaseHTTPRequestHandler):
    server_version = f"misanthropic/{__version__}"
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
        # Finalize the request log if one is in flight (only set by
        # /v1/messages; admin/health responses are no-ops here).
        if getattr(self, "_log_rec", None) is not None:
            if status == 200:
                self._log_finalize(status, message_obj=obj if isinstance(obj, dict) else None)
            else:
                err = obj.get("error") if isinstance(obj, dict) else None
                self._log_finalize(status, error_msg=(err or {}).get("message") if isinstance(err, dict) else None)

    def _send_error(self, status, etype, message):
        self._send_json(status, {"type": "error", "error": {"type": etype, "message": message}})

    # ---- request log helpers (see request_log.py and the dashboard) -------

    def _key_label(self, key):
        if not key:
            return "stateless"
        meta = (sessions.keys_detail() or {}).get(key) or {}
        return meta.get("label") or "(unnamed)"

    def _request_mode(self, linked, web):
        if linked and web:
            return "session+web"
        if linked:
            return "session"
        if web:
            return "web"
        return "stateless"

    def _last_user_text(self, body):
        """The full text of the last user message — shown in the activity log
        when a row is expanded. Multiple text blocks are joined; non-text blocks
        (e.g. images) are skipped."""
        msgs = body.get("messages")
        if not isinstance(msgs, list) or not msgs:
            return ""
        last = msgs[-1]
        if not isinstance(last, dict):
            return ""
        content = last.get("content")
        if isinstance(content, str):
            return content[:MAX_LOG_TEXT]
        if isinstance(content, list):
            parts = [blk.get("text") or "" for blk in content
                     if isinstance(blk, dict) and blk.get("type") == "text"]
            return "\n".join(p for p in parts if p)[:MAX_LOG_TEXT]
        return ""

    def _log_start(self, body, key, linked, web):
        self._log_rec = {
            "ts": time.time(),
            "key_label": self._key_label(key) if linked else "stateless",
            "model": body.get("model") or claude_mod.DEFAULT_MODEL,
            "mode": self._request_mode(linked, web),
            "stream": bool(body.get("stream")),
            "prompt_text": self._last_user_text(body),
        }

    def _log_finalize(self, status, message_obj=None, error_msg=None,
                      in_tokens=None, out_tokens=None, response_text=None,
                      web_requests=None, cache_write=None, cache_read=None):
        rec = getattr(self, "_log_rec", None)
        if rec is None:
            return
        rec["duration_ms"] = int((time.time() - rec["ts"]) * 1000)
        rec["status"] = status
        if isinstance(message_obj, dict) and message_obj.get("type") == "message":
            usage = message_obj.get("usage") or {}
            rec["input_tokens"] = usage.get("input_tokens", 0)
            rec["output_tokens"] = usage.get("output_tokens", 0)
            # Claude Code auto-caches; these carry the bulk of large prompts.
            if cache_write is None:
                cache_write = usage.get("cache_creation_input_tokens", 0)
            if cache_read is None:
                cache_read = usage.get("cache_read_input_tokens", 0)
            if web_requests is None:
                web_requests = (usage.get("server_tool_use") or {}).get("web_search_requests")
            parts = [blk.get("text") or "" for blk in (message_obj.get("content") or [])
                     if isinstance(blk, dict) and blk.get("type") == "text"]
            rec["response_text"] = "\n".join(p for p in parts if p)[:MAX_LOG_TEXT]
        if in_tokens is not None:
            rec["input_tokens"] = in_tokens
        if out_tokens is not None:
            rec["output_tokens"] = out_tokens
        if web_requests is not None:
            rec["web_requests"] = web_requests
        if cache_write is not None:
            rec["cache_write"] = cache_write
        if cache_read is not None:
            rec["cache_read"] = cache_read
        if response_text is not None:
            rec["response_text"] = response_text[:MAX_LOG_TEXT]
        if error_msg:
            rec["error"] = str(error_msg)[:MAX_LOG_TEXT]
        request_log.append(rec)
        history.append(rec)  # durable copy + SSE change notification
        # Tally the hosted-API price we dodged — successful generations only.
        if status == 200:
            try:
                savings.record(
                    rec.get("model"),
                    rec.get("input_tokens", 0),
                    rec.get("output_tokens", 0),
                    rec.get("web_requests", 0),
                    rec.get("cache_write", 0),
                    rec.get("cache_read", 0),
                )
            except Exception:
                pass  # a savings hiccup must never fail the request
        self._log_rec = None

    def _send_html(self, html):
        data = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _is_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "localhost")

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

    def _query(self):
        from urllib.parse import parse_qs, urlsplit
        q = parse_qs(urlsplit(self.path).query)
        return {k: v[0] for k, v in q.items() if v}

    def _send_static(self, rel):
        """Serve a compiled dashboard asset. Path is resolved and must stay
        inside STATIC_DIR (no traversal)."""
        target = (STATIC_DIR / rel.lstrip("/")).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return self._send_error(404, "not_found_error", "Not found.")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            # SPA fallback: unknown non-asset paths get index.html so hash-less
            # deep links still land in the app.
            index = STATIC_DIR / "index.html"
            if not index.is_file():
                return self._send_error(404, "not_found_error", "Not found.")
            target = index
        data = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if "/assets/" in str(target):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            if (STATIC_DIR / "index.html").is_file():
                return self._send_static("index.html")
            return self._send_html(dashboard.PAGE)
        if path == "/health":
            snap = doctor.snapshot()
            return self._send_json(200, {
                "status": "ok",
                "service": "misanthropic",
                "version": __version__,
                "backend": "claude-code-cli",
                "mode": "session" if sessions.session_mode_enabled() else "stateless",
                "claude": snap["status"],
            })
        if path.startswith("/admin/"):
            if not self._is_local():
                return self._send_error(403, "permission_error", "Admin API is local-only.")
            if path == "/admin/state":
                return self._send_json(200, self._admin_state())
            if path == "/admin/requests":
                q = self._query()
                rows = history.recent(
                    limit=int(q.get("limit", 50)),
                    before_id=int(q["before_id"]) if q.get("before_id") else None,
                    key_label=q.get("key"), model=q.get("model"),
                    status=q.get("status"), q=q.get("q"),
                )
                return self._send_json(200, {"requests": rows,
                                              "total": history.count(),
                                              "savings": savings.summary()})
            if path == "/admin/requests/live":
                # The legacy in-memory ring still sees in-flight/most-recent
                # entries first; useful for the live feed's optimistic rows.
                return self._send_json(200, {"requests": request_log.recent()})
            if path == "/admin/series":
                days = int(self._query().get("days", 30))
                return self._send_json(200, {"series": history.daily_series(days)})
            if path == "/admin/doctor":
                probe = self._query().get("probe") in ("1", "true")
                return self._send_json(200, doctor.snapshot(probe=probe))
            if path == "/admin/settings":
                return self._send_json(200, {
                    "settings": settings.load(),
                    "web_policy": web_policy(),
                    "default_model": claude_mod.DEFAULT_MODEL,
                    "max_concurrency": _governor.limit,
                })
            if path == "/admin/events":
                return self._stream_admin_events()
            return self._send_error(404, "not_found_error", f"Unknown path: {self.path}")
        if path.startswith("/assets/") or path in ("/favicon.svg", "/favicon.ico"):
            return self._send_static(path)
        self._send_error(404, "not_found_error", f"Unknown path: {self.path}")

    def _stream_admin_events(self):
        """SSE change feed for the dashboard: request completions and state
        changes push instantly; a heartbeat comment every 15s keeps proxies and
        EventSource happy. The client re-fetches on each event."""
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        sub = history.subscribe()
        try:
            self.wfile.write(b"event: hello\ndata: {}\n\n")
            self.wfile.flush()
            while True:
                try:
                    item = sub.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                chunk = f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"
                self.wfile.write(chunk.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client closed the tab
        finally:
            history.unsubscribe(sub)

    def _admin_state(self):
        detail = sessions.keys_detail()
        sess = sessions.all_sessions()
        stats = history.key_stats()
        keys = [{
            "key": k,
            "label": meta.get("label", ""),
            "created": meta.get("created", ""),
            "turns": sess.get(k, {}).get("turns", 0),
            "requests": stats.get(meta.get("label", ""), {}).get("requests", 0),
            "usd": stats.get(meta.get("label", ""), {}).get("usd", 0.0),
        } for k, meta in detail.items()]
        return {
            "mode": "session" if sessions.session_mode_enabled() else "stateless",
            "version": __version__,
            "keys": keys,
            "web_policy": web_policy(),
            "default_model": claude_mod.DEFAULT_MODEL,
            "base_url": f"http://{self.headers.get('Host') or '127.0.0.1:8787'}",
            # First run = the wizard hasn't completed and nothing has ever
            # happened here. Keys, request history, or a savings tally from a
            # pre-1.1 install (history.db didn't exist yet) all clear it —
            # upgraders are not new users.
            "first_run": (not settings.get("onboarded")
                          and not keys and history.count() == 0
                          and savings.summary().get("all_time_requests", 0) == 0),
        }

    def do_POST(self):
        path = self.path.split("?")[0]

        # Admin (management) routes are localhost-only and bypass API-key auth.
        if path.startswith("/admin/"):
            if not self._is_local():
                return self._send_error(403, "permission_error", "Admin API is local-only.")
            try:
                body = self._read_body()
            except (json.JSONDecodeError, ValueError):
                body = {}
            if path == "/admin/keys":
                key = sessions.create_key(str(body.get("label", "")).strip())
                history.notify("state")
                return self._send_json(200, {"key": key})
            if path == "/admin/keys/delete":
                sessions.remove_key(str(body.get("key", "")))
                history.notify("state")
                return self._send_json(200, {"ok": True})
            if path == "/admin/sessions/forget":
                sessions.forget_session(str(body.get("key", "")))
                history.notify("state")
                return self._send_json(200, {"ok": True})
            if path == "/admin/doctor/rescan":
                return self._send_json(200, doctor.rescan())
            if path == "/admin/doctor/probe":
                # The wizard's "verify login" button: force a fresh probe.
                doctor.probe_login(force=True)
                return self._send_json(200, doctor.snapshot())
            if path == "/admin/settings":
                new = settings.update(body if isinstance(body, dict) else {})
                if "web_policy" in new and new["web_policy"] in ("auto", "on", "off"):
                    try:
                        claude_mod.set_web_policy(new["web_policy"])
                    except ValueError:
                        pass
                if body.get("default_model"):
                    claude_mod.DEFAULT_MODEL = str(body["default_model"])
                if body.get("max_concurrency"):
                    try:
                        _governor.set_limit(int(body["max_concurrency"]))
                    except (TypeError, ValueError):
                        pass
                history.notify("state")
                return self._send_json(200, {"settings": new,
                                              "web_policy": web_policy(),
                                              "default_model": claude_mod.DEFAULT_MODEL,
                                              "max_concurrency": _governor.limit})
            return self._send_error(404, "not_found_error", f"Unknown path: {self.path}")

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

        model = body.get("model") or claude_mod.DEFAULT_MODEL
        system = translate.extract_system(body)
        # build_cli_input picks the wire format: plain text by default, or
        # stream-json (Anthropic content blocks) when the request carries images.
        input_format, prompt = translate.build_cli_input(messages)
        linked = sessions.session_mode_enabled()  # key-linked session?

        # Decide web per request: the "auto" policy honors the web_search tool in
        # the request (faithful to the hosted API); "on"/"off" force/deny it.
        use_web = resolve_web(_request_wants_web(body))

        # Start a request-log entry; finalized in _send_json or the streaming
        # paths' end-of-emit hooks. Visible at GET /admin/requests.
        self._log_start(body, key, linked, use_web)

        # Take a CLI slot (or refuse with the API's own overload signal, which
        # SDKs retry with backoff). Held for the full run, streaming included.
        if not _governor.acquire(timeout=QUEUE_WAIT_S):
            return self._send_error(529, "overloaded_error",
                                    "Local server is at capacity; retry shortly.")
        try:
            # Web mode runs the agentic loop and reshapes its tool blocks into
            # the API's `web_search` content. Stream and non-stream both use it.
            if use_web:
                if body.get("stream"):
                    return self._stream_web(prompt, model, system, key if linked else None, input_format)
                return self._blocking_web(prompt, model, system, key if linked else None, input_format)

            if body.get("stream"):
                return self._stream_messages(prompt, model, system, key if linked else None, input_format)

            if linked:
                return self._blocking_session(prompt, model, system, key, input_format)

            try:
                wrapper = run_blocking(prompt, model=model, system=system, input_format=input_format)
            except ClaudeError as e:
                return self._send_claude_error(e)
            self._send_json(200, translate.wrapper_to_message(wrapper, model))
        finally:
            _governor.release()

    def _send_claude_error(self, e):
        status, etype = classify_claude_error(str(e))
        return self._send_error(status, etype, str(e))

    # ---- web search (opt-in) ------------------------------------------------

    def _run_web_linked(self, prompt, model, system, key, input_format="text"):
        """Drive a web-enabled run, handling key-session lock/resume/retry.

        `key` is None in stateless mode. Mirrors the stale-session recovery of
        _blocking_session: a bad session id is reset and retried once; a
        transient error propagates without destroying the link."""
        linked = key is not None
        cwd = str(sessions.WORKSPACE) if linked else None
        lock = sessions.key_lock(key) if linked else None
        if lock:
            lock.acquire()
        try:
            resume_id = sessions.get_session_id(key) if linked else None
            try:
                blocks, wrapper, sid = run_web(prompt, model=model, system=system,
                                               resume=resume_id, persist=linked, cwd=cwd,
                                               input_format=input_format)
            except ClaudeError as e:
                if not (linked and resume_id and _is_invalid_session_error(str(e))):
                    raise
                sessions.forget_session(key)
                blocks, wrapper, sid = run_web(prompt, model=model, system=system,
                                               resume=None, persist=True, cwd=cwd,
                                               input_format=input_format)
            if linked and sid:
                sessions.record_session(key, sid)
            return blocks, wrapper
        finally:
            if lock:
                lock.release()

    def _blocking_web(self, prompt, model, system, key, input_format="text"):
        try:
            blocks, wrapper = self._run_web_linked(prompt, model, system, key, input_format)
        except ClaudeError as e:
            return self._send_claude_error(e)
        self._send_json(200, translate.web_message(blocks, wrapper, model))

    def _stream_web(self, prompt, model, system, key, input_format="text"):
        # The web run is buffered (the agentic loop can't be a single live
        # stream), so do the work first; a failure here can still be a clean
        # JSON error since no SSE headers have been sent yet.
        try:
            blocks, wrapper = self._run_web_linked(prompt, model, system, key, input_format)
        except ClaudeError as e:
            return self._send_claude_error(e)

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

        content = translate.web_blocks_to_content(blocks)
        usage = translate.web_usage(wrapper)
        stop_reason = wrapper.get("stop_reason") or "end_turn"
        message_id = translate._message_id(wrapper)
        # Full assistant text (all text blocks joined) for the activity log.
        response_text = "\n".join(
            blk["text"] for blk in content
            if blk.get("type") == "text" and blk.get("text")
        )
        try:
            for event_type, data in translate.web_sse_events(content, usage, stop_reason, model, message_id):
                sse(event_type, data)
            self._log_finalize(200,
                               in_tokens=usage.get("input_tokens"),
                               out_tokens=usage.get("output_tokens"),
                               web_requests=(usage.get("server_tool_use") or {}).get("web_search_requests"),
                               cache_write=usage.get("cache_creation_input_tokens", 0),
                               cache_read=usage.get("cache_read_input_tokens", 0),
                               response_text=response_text)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client hung up mid-stream
        except Exception as e:
            # Translation bug post-headers: surface it as an SSE error instead
            # of silently truncating the stream after 200 has shipped.
            try:
                sse("error", {"type": "error", "error": {"type": "api_error", "message": str(e)}})
            except Exception:
                pass
            self._log_finalize(500, error_msg=str(e))

    def _blocking_session(self, prompt, model, system, key, input_format="text"):
        """Run linked to the key's session: resume it, persist, serialize."""
        cwd = str(sessions.WORKSPACE)
        with sessions.key_lock(key):
            resume_id = sessions.get_session_id(key)
            try:
                wrapper = run_blocking(prompt, model=model, system=system,
                                       resume=resume_id, persist=True, cwd=cwd,
                                       input_format=input_format)
            except ClaudeError as e:
                # Only start over if the session id is genuinely bad — a transient
                # error (rate limit, backend hiccup) must NOT destroy the link.
                if not resume_id or not _is_invalid_session_error(str(e)):
                    return self._send_claude_error(e)
                sessions.forget_session(key)
                try:
                    wrapper = run_blocking(prompt, model=model, system=system,
                                           resume=None, persist=True, cwd=cwd,
                                           input_format=input_format)
                except ClaudeError as e2:
                    return self._send_claude_error(e2)
            sid = wrapper.get("session_id")
            if sid:
                sessions.record_session(key, sid)
        self._send_json(200, translate.wrapper_to_message(wrapper, model))

    def _stream_messages(self, prompt, model, system, key, input_format="text"):
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
            log_in = log_out = None
            text_parts = []  # accumulate streamed text for the activity log
            for kind, obj in stream_events(prompt, model=model, system=system,
                                           resume=resume_id, persist=bool(key),
                                           cwd=str(sessions.WORKSPACE) if key else None,
                                           input_format=input_format):
                if kind == "session":
                    captured_sid = obj
                elif kind == "event":
                    et = obj.get("type")
                    if et == "message_start":
                        u = ((obj.get("message") or {}).get("usage") or {})
                        log_in = u.get("input_tokens", log_in)
                    elif et == "message_delta":
                        ot = (obj.get("usage") or {}).get("output_tokens")
                        if ot is not None:
                            log_out = ot
                    elif et == "content_block_delta":
                        delta = obj.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            text_parts.append(delta["text"])
                    sse(et or "message_delta", obj)
                elif kind == "error":
                    saw_error = True
                    sse("error", {"type": "error", "error": {"type": "api_error", "message": obj.get("message", "")}})
            if key:
                if saw_error and resume_id and captured_sid is None:
                    sessions.forget_session(key)  # likely a stale session id
                elif captured_sid:
                    sessions.record_session(key, captured_sid)
            self._log_finalize(
                500 if saw_error else 200,
                in_tokens=log_in, out_tokens=log_out,
                response_text="".join(text_parts) if not saw_error else None,
                error_msg="upstream stream error" if saw_error else None,
            )
        except ClaudeError as e:
            sse("error", {"type": "error", "error": {"type": "api_error", "message": str(e)}})
            self._log_finalize(500, error_msg=str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass  # client hung up mid-stream
        except Exception as e:
            # A bug here (e.g. a malformed event from the CLI) would otherwise
            # truncate the stream silently after the 200/headers have shipped.
            try:
                sse("error", {"type": "error", "error": {"type": "api_error", "message": str(e)}})
            except Exception:
                pass
        finally:
            if lock:
                lock.release()


def make_httpd(host="127.0.0.1", port=8787):
    """Create (but don't start) the server — lets the menu-bar app supervise it."""
    settings.apply_startup()
    pruned = history.prune(settings.get("retention_days"))
    if pruned:
        print(f"  history: pruned {pruned} entries past retention", file=sys.stderr)
    return ThreadingHTTPServer((host, port), Handler)


def serve(host="127.0.0.1", port=8787):
    # Refuse to run session mode on a non-local interface without a workspace —
    # agentic persistence over the network is a foot-gun.
    session_mode = sessions.session_mode_enabled()
    if session_mode and host not in ("127.0.0.1", "localhost", "::1"):
        print(f"refusing to bind session mode to {host}: only approved keys gate access; "
              f"expose deliberately.", file=sys.stderr)

    httpd = make_httpd(host, port)
    base = f"http://{host}:{port}"
    print(f"misanthropic {__version__} — Anthropic-compatible API on {base}", file=sys.stderr)
    if session_mode:
        print(f"  mode: session  ·  {len(sessions.approved_keys())} approved key(s)  ·  "
              f"sessions persist & resume per key", file=sys.stderr)
    else:
        print(f"  mode: stateless  ·  auth: {'x-api-key required' if API_KEY else 'open (local)'}", file=sys.stderr)
    _web_desc = {
        "auto": "auto — per request, honors the web_search tool (like the hosted API)",
        "on": "on — forced for every request (MISANTHROPIC_WEB=1)",
        "off": "off — hard kill-switch, no internet (MISANTHROPIC_WEB=off)",
    }[web_policy()]
    print(f"  web search: {_web_desc}", file=sys.stderr)
    print(f"  point your client at  base_url={base}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down.", file=sys.stderr)
        httpd.shutdown()
