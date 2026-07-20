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

import itertools

from . import (__version__, accounts, dashboard, doctor, history, limits,
               models, request_log, savings, sessions, settings, tool_bridge,
               translate)
from . import claude as claude_mod
from . import codex as codex_mod
from .claude import ClaudeError, resolve_web, run_blocking, run_web, stream_events, web_policy
from .errors import BackendError

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


class _Slot:
    """A governor slot with idempotent release, so the tools path can hand its
    slot back early (a parked process is idle — it must not count against the
    concurrency cap) while every other path keeps the plain try/finally."""

    def __init__(self, governor):
        self._governor = governor
        self._held = True

    def release(self):
        if self._held:
            self._held = False
            self._governor.release()


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

    def _request_mode(self, linked, web, tools=False):
        if tools:
            return "tools"
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

    def _log_start(self, body, key, linked, web, tools=False):
        self._log_rec = {
            "ts": time.time(),
            "key_label": self._key_label(key) if linked else "stateless",
            "model": body.get("model") or claude_mod.DEFAULT_MODEL,
            "mode": self._request_mode(linked, web, tools),
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
        if path == "/v1/models":
            q = self._query()
            return self._send_json(200, models.list_models(
                limit=q.get("limit", 20),
                before_id=q.get("before_id"), after_id=q.get("after_id")))
        if path.startswith("/v1/models/"):
            m = models.get_model(path[len("/v1/models/"):])
            if m is None:
                return self._send_error(404, "not_found_error",
                                        f"model: {path[len('/v1/models/'):]}")
            return self._send_json(200, m)
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
        # The codex backend takes a different payload shape: flat text plus
        # image blocks passed as files. Cheap to precompute for both.
        msgs = [m for m in messages if isinstance(m, dict)]
        codex_text = translate.messages_to_prompt(msgs)
        image_blocks = list(translate._iter_image_blocks(msgs))
        linked = sessions.session_mode_enabled()  # key-linked session?

        # Decide web per request: the "auto" policy honors the web_search tool in
        # the request (faithful to the hosted API); "on"/"off" force/deny it.
        use_web = resolve_web(_request_wants_web(body))

        # Client-side limits: stop_sequences always honored when supplied;
        # max_tokens only under the opt-in setting. Web runs are excluded — their
        # multi-block content already carries documented honest gaps.
        stop_seqs = body.get("stop_sequences")
        max_toks = body.get("max_tokens")

        # Extended thinking: surfaced only when the request opts in (the CLI
        # thinks regardless; we control whether the blocks reach the client).
        thinking_on = translate.thinking_requested(body)

        # Client-defined tools (function calling). When present they own the
        # request: web is ignored and the run is stateless even under an
        # approved key (a session client sends only the new turn, which the
        # tool loop's flatten-fallback can't work with).
        try:
            client_tools, tool_choice = translate.extract_client_tools(body)
        except translate.ToolRequestError as e:
            return self._send_error(400, "invalid_request_error", str(e))

        # What this request NEEDS — drives which accounts may serve it.
        # Tools/web/sessions are Claude-only; text/images/thinking can route
        # to any backend.
        caps = {
            "tools": bool(client_tools),
            "web": use_web and not client_tools,
            "session": linked and not client_tools,
            "images": input_format == "stream-json",
            "thinking": thinking_on,
            "text": True,
        }

        # Start a request-log entry; finalized in _send_json or the streaming
        # paths' end-of-emit hooks. Visible at GET /admin/requests.
        self._log_start(body, key, linked, use_web, tools=bool(client_tools))

        # Take a CLI slot (or refuse with the API's own overload signal, which
        # SDKs retry with backoff). Held for the full run, streaming included —
        # except tool runs, which hand the slot back while parked. Failover
        # retries happen inside the held slot.
        if not _governor.acquire(timeout=QUEUE_WAIT_S):
            return self._send_error(529, "overloaded_error",
                                    "Local server is at capacity; retry shortly.")
        slot = _Slot(_governor)
        try:
            if client_tools:
                if use_web:
                    sys.stderr.write("  tools+web in one request: client tools "
                                     "take precedence, web ignored\n")
                if linked:
                    sys.stderr.write("  tools under an approved key run "
                                     "stateless (sessions+tools unsupported)\n")
                return self._handle_tools(body, client_tools, tool_choice,
                                          stop_seqs, max_toks, thinking_on,
                                          slot, caps)

            # Web mode runs the agentic loop and reshapes its tool blocks into
            # the API's `web_search` content. Stream and non-stream both use it.
            if use_web:
                if body.get("stream"):
                    return self._stream_web(prompt, model, system, key if linked else None,
                                            input_format, caps)
                return self._blocking_web(prompt, model, system, key if linked else None,
                                          input_format, caps)

            if body.get("stream"):
                return self._stream_messages(prompt, model, system, key if linked else None, input_format,
                                             stop_seqs=stop_seqs, max_toks=max_toks,
                                             thinking=thinking_on, caps=caps,
                                             codex_payload=(codex_text, image_blocks))

            if linked:
                return self._blocking_session(prompt, model, system, key, input_format,
                                              stop_seqs=stop_seqs, max_toks=max_toks,
                                              thinking=thinking_on, caps=caps)

            # Stateless blocking: full failover across eligible accounts,
            # dispatched per backend.
            def attempt(acc):
                blocks = None
                if acc["backend"] == "codex":
                    wrapper, cblocks = codex_mod.run_blocking(
                        codex_text, model=model, system=system,
                        images=image_blocks, account=acc)
                    blocks = cblocks if thinking_on else [
                        b for b in cblocks if b.get("type") == "text"]
                elif thinking_on:
                    wrapper, blocks = run_blocking(prompt, model=model, system=system,
                                                   input_format=input_format,
                                                   collect_blocks=True, account=acc)
                else:
                    wrapper = run_blocking(prompt, model=model, system=system,
                                           input_format=input_format, account=acc)
                msg = translate.wrapper_to_message(wrapper, model, blocks=blocks)
                return limits.apply_to_message(msg, stop_seqs, max_toks)

            result, err = self._attempt_accounts(caps, attempt)
            if err is not None:
                return self._send_error(*err)
            self._send_json(200, result)
        finally:
            slot.release()

    def _send_claude_error(self, e):
        status, etype = classify_claude_error(str(e))
        return self._send_error(status, etype, str(e))

    # ---- account routing & failover ----------------------------------------

    def _no_accounts_error(self, caps):
        msg = "All accounts able to serve this request are rate-limited or unavailable"
        if accounts.claude_only(caps):
            msg += " (this request needs Claude-only capabilities: tools/web/session)"
        return (529, "overloaded_error", msg + ". Retry shortly.")

    def _note_backend_failure(self, acc, message):
        """Classify a backend failure for failover purposes and update the
        account's runtime state. Returns True when the next account should be
        tried (limit/auth), False for errors that failover can't help."""
        cls = accounts.classify(acc["backend"], message)
        if cls == "limit":
            accounts.report_limited(acc["id"], message)
            return True
        if cls == "auth":
            accounts.mark_logged_out(acc["id"], message)
            return True
        return False

    def _record_serving(self, acc):
        if getattr(self, "_log_rec", None) is not None:
            self._log_rec["account"] = acc["label"]
            self._log_rec["backend"] = acc["backend"]

    def _attempt_accounts(self, caps, attempt):
        """Run `attempt(account)` against eligible accounts in order, failing
        over on usage-limit/auth errors. Returns (result, None) on success or
        (None, (status, etype, message)) — the caller sends, never this."""
        order = accounts.eligible(caps)
        if not order:
            return None, self._no_accounts_error(caps)
        last = None
        for acc in order:
            try:
                result = attempt(acc)
            except BackendError as e:
                if self._note_backend_failure(acc, str(e)):
                    last = e
                    continue
                status, etype = classify_claude_error(str(e))
                return None, (status, etype, str(e))
            accounts.report_ok(acc["id"])
            self._record_serving(acc)
            return result, None
        status, etype = classify_claude_error(str(last))
        return None, (status, etype, str(last))

    def _resolve_session_account(self, key, caps):
        """The account a key-linked session must run on. Sessions are
        account-bound (a claude session id only resumes under the login that
        created it), so a cooling bound account means 529 — continuity wins
        over failover. A deleted/disabled bound account is the one case where
        continuity is impossible: forget the session and rebind fresh.
        Returns (account, None) or (None, (status, etype, message))."""
        aid = sessions.get_session_account(key)
        acc = accounts.get(aid) if aid else None
        if acc is not None and acc.get("enabled", True):
            if accounts.cooling(acc["id"]):
                cds, _ = accounts.cooldown_state()
                left = (cds.get(acc["id"]) or {}).get("seconds_left", 0)
                return None, (529, "overloaded_error",
                              f"This key's session account ({acc['label']}) is "
                              f"rate-limited; retry in ~{max(60, left)}s.")
            return acc, None
        if aid:  # bound account is gone/disabled — the old session can't resume
            sessions.forget_session(key)
        order = accounts.eligible(caps)
        if not order:
            return None, self._no_accounts_error(caps)
        return order[0], None

    def _open_stream(self, candidates, make_gen):
        """The streaming failover trick: pull each candidate's generator until
        its FIRST real event (buffering the session marker). Errors before the
        first event fail over; the first event commits the account. Returns
        (account, chained_iterator, None) or (None, None, (status, etype, msg)).
        SSE headers must not be sent until this returns an iterator."""
        if not candidates:
            return None, None, (529, "overloaded_error",
                                "All accounts are rate-limited or unavailable. Retry shortly.")
        last = None
        for acc in candidates:
            gen = make_gen(acc)
            buffered = []
            failed = None
            for kind, obj in gen:
                if kind == "event":
                    buffered.append((kind, obj))
                    accounts.report_ok(acc["id"])
                    self._record_serving(acc)
                    return acc, itertools.chain(buffered, gen), None
                if kind == "error":
                    failed = obj.get("message", "") or "backend error"
                    break
                buffered.append((kind, obj))   # "session" marker
            try:
                gen.close()  # kills the subprocess if still running
            except Exception:
                pass
            failed = failed if failed is not None else "backend produced no output"
            if self._note_backend_failure(acc, failed):
                last = failed
                continue
            status, etype = classify_claude_error(failed)
            return None, None, (status, etype, failed)
        status, etype = classify_claude_error(last or "")
        return None, None, (status, etype, last or "no eligible accounts")

    # ---- client tool use (function calling) --------------------------------

    def _handle_tools(self, body, tools, tool_choice, stop_seqs, max_toks,
                      thinking, slot, caps=None):
        """Route a tools request: continue a parked run when the last message
        is its matching tool_result set, else start a fresh shim run (which is
        also the recovery path for an expired/dead park — the client resends
        full history, so we flatten it and carry on). Fresh runs fail over
        across Claude accounts; a park continuation is bound to its process."""
        model = body.get("model") or claude_mod.DEFAULT_MODEL
        system_raw = translate.extract_system(body)
        # Continuations must match on what the CLIENT sent, so fingerprint the
        # raw system; the tool_choice nudge only decorates the spawned run.
        fp = tool_bridge.fingerprint(model, system_raw)
        system = system_raw
        nudge = translate.tool_choice_nudge(tool_choice)
        if nudge:
            system = f"{system_raw or claude_mod.DEFAULT_SYSTEM}\n\n{nudge}"
        stream = bool(body.get("stream"))

        results = translate.extract_tool_results(body)
        if results is not None:
            run, partial = tool_bridge.find_park(
                [r["tool_use_id"] for r in results])
            if run is not None and run.fingerprint == fp and run.claim_for_resume():
                try:
                    run.deliver_results(results)
                except ClaudeError as e:
                    run.destroy()
                    return self._send_claude_error(e)
                if run.account:
                    self._record_serving(run.account)
                fail = self._drive_tool_turn(run, model, stream, thinking,
                                             stop_seqs, max_toks, slot)
                if fail is not None:
                    # A continuation has exactly one process — no failover.
                    status, etype = classify_claude_error(fail)
                    return self._send_error(status, etype, fail)
                return
            if partial:
                return self._send_error(
                    400, "invalid_request_error",
                    "`tool_result` blocks must cover exactly the pending tool "
                    "calls of the previous `tool_use` turn.")
            # No live park (expired, evicted, restarted): fall through to a
            # fresh run — build_cli_input flattens the full history, including
            # tool calls/results, into the prompt.

        input_format, prompt = translate.build_cli_input(body.get("messages") or [])
        order = accounts.eligible(caps)
        if not order:
            return self._send_error(*self._no_accounts_error(caps or {"tools": True}))
        last = None
        for acc in order:
            try:
                run = tool_bridge.start_run(tools, model, system, prompt,
                                            input_format=input_format, fp=fp,
                                            account=acc)
            except BackendError as e:
                if self._note_backend_failure(acc, str(e)):
                    last = str(e)
                    continue
                return self._send_claude_error(e)
            self._record_serving(acc)
            fail = self._drive_tool_turn(run, model, stream, thinking,
                                         stop_seqs, max_toks, slot)
            if fail is None:
                accounts.report_ok(acc["id"])
                return
            if self._note_backend_failure(acc, fail):
                last = fail
                continue
            status, etype = classify_claude_error(fail)
            return self._send_error(status, etype, fail)
        status, etype = classify_claude_error(last or "")
        return self._send_error(status, etype,
                                last or "no eligible accounts for tool use")

    def _drive_tool_turn(self, run, model, stream, thinking, stop_seqs,
                         max_toks, slot):
        """Drive one turn of a tool run. Returns None once a response has been
        sent, or an error string when the run failed before anything shipped —
        the caller may then fail over to another account."""
        if stream:
            return self._stream_tool_turn(run, model, thinking, stop_seqs,
                                          max_toks, slot)
        return self._blocking_tool_turn(run, model, thinking, stop_seqs,
                                        max_toks, slot)

    def _park_or_destroy(self, run, slot):
        """A turn ended in tool calls: park the process once every call has
        reached the shim (else the continuation could never be answered —
        destroy instead and let the next request fall back to a fresh run).
        The park hands its governor slot back: an idle process must not count
        against the concurrency cap."""
        ids = [b.get("id") for b in run.turn_blocks
               if b.get("type") == "tool_use" and b.get("id")]
        if ids and run.wait_for_dispatch(ids):
            run.park()
            slot.release()
            return True
        run.destroy()
        return False

    def _blocking_tool_turn(self, run, model, thinking, stop_seqs, max_toks, slot):
        error = None
        for kind, obj in run.read_turn():
            if kind == "error":
                error = obj.get("message", "")
        if error is not None:
            run.destroy()
            return error or "backend error"   # nothing shipped — retryable

        if run.turn_stop_reason == "tool_use":
            blocks = run.turn_blocks if thinking else [
                b for b in run.turn_blocks
                if b.get("type") not in ("thinking", "redacted_thinking")]
            content = translate.tool_blocks_to_content(blocks)
            self._park_or_destroy(run, slot)
            self._send_json(200, translate.tool_use_message(
                content, run.turn_usage, model, run.message_id))
            return None

        wrapper = run.finish()
        run.destroy()
        if wrapper.get("is_error"):
            detail = wrapper.get("result")
            detail = detail if isinstance(detail, str) else (
                wrapper.get("subtype") or "Local Claude returned an error.")
            return detail                     # nothing shipped — retryable
        blocks = run.turn_blocks if thinking else [
            b for b in run.turn_blocks if b.get("type") == "text"]
        msg = translate.wrapper_to_message(wrapper, model, blocks=blocks)
        self._send_json(200, limits.apply_to_message(msg, stop_seqs, max_toks))
        return None

    def _stream_tool_turn(self, run, model, thinking, stop_seqs, max_toks, slot):
        # First-event delay: peek before sending headers so a run that dies
        # instantly (rate limit, logged out) can fail over to another account
        # with a clean JSON error path instead of a committed SSE stream.
        gen = run.read_turn()
        first = next(gen, None)
        if first is None:
            run.destroy()
            return "backend produced no output"
        if first[0] == "error":
            run.destroy()
            return first[1].get("message", "") or "backend error"

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

        gate = limits.LimitGate(stop_seqs, max_toks)
        tfilter = translate.ThinkingFilter(thinking)
        text_blocks = set()
        text_parts = []
        log_in = log_out = None

        def _end_stream_early(block_index):
            sse("content_block_stop", {"type": "content_block_stop", "index": block_index})
            sse("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": gate.stop_reason,
                          "stop_sequence": gate.stop_sequence},
                "usage": {"output_tokens": gate.emitted_tokens()},
            })
            sse("message_stop", {"type": "message_stop"})

        try:
            for kind, event in itertools.chain([first], gen):
                if kind == "error":
                    # Post-commit: headers shipped, SSE-error semantics.
                    run.destroy()
                    sse("error", {"type": "error", "error": {
                        "type": "api_error", "message": event.get("message", "")}})
                    self._log_finalize(500, error_msg=event.get("message"))
                    return
                event = tfilter.feed(event)
                if event is None:
                    continue
                event = translate.rewrite_tool_event(event, model)
                et = event.get("type")
                if et == "message_start":
                    u = ((event.get("message") or {}).get("usage") or {})
                    log_in = u.get("input_tokens", log_in)
                elif et == "message_delta":
                    ot = (event.get("usage") or {}).get("output_tokens")
                    if ot is not None:
                        log_out = ot
                elif et == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        if gate.active:
                            text_blocks.add(event.get("index", 0))
                            emit, done = gate.feed(delta["text"])
                            if emit:
                                text_parts.append(emit)
                                event = dict(event, delta=dict(delta, text=emit))
                                sse(et, event)
                            if done:
                                # A limit hit mid-tool-run ends the response —
                                # and the run: there is nothing to park.
                                _end_stream_early(event.get("index", 0))
                                run.destroy()
                                self._log_finalize(200, in_tokens=log_in,
                                                   out_tokens=gate.emitted_tokens(),
                                                   response_text="".join(text_parts))
                                return
                            continue
                        text_parts.append(delta["text"])
                elif (et == "content_block_stop" and gate.active
                      and event.get("index", 0) in text_blocks):
                    tail = gate.flush()
                    if tail:
                        text_parts.append(tail)
                        sse("content_block_delta", {
                            "type": "content_block_delta",
                            "index": event.get("index", 0),
                            "delta": {"type": "text_delta", "text": tail},
                        })
                    if gate.finished:
                        _end_stream_early(event.get("index", 0))
                        run.destroy()
                        self._log_finalize(200, in_tokens=log_in,
                                           out_tokens=gate.emitted_tokens(),
                                           response_text="".join(text_parts))
                        return
                sse(et, event)

            if run.turn_stop_reason == "tool_use":
                self._park_or_destroy(run, slot)
                self._log_finalize(200, in_tokens=log_in, out_tokens=log_out,
                                   response_text="".join(text_parts))
                return
            wrapper = run.finish()
            run.destroy()
            usage = wrapper.get("usage") or {}
            self._log_finalize(200,
                               in_tokens=usage.get("input_tokens", log_in),
                               out_tokens=usage.get("output_tokens", log_out),
                               cache_write=usage.get("cache_creation_input_tokens", 0),
                               cache_read=usage.get("cache_read_input_tokens", 0),
                               response_text="".join(text_parts))
        except (BrokenPipeError, ConnectionResetError):
            run.destroy()  # client hung up: a parked-to-be run is worthless now
        except Exception as e:
            run.destroy()
            try:
                sse("error", {"type": "error", "error": {"type": "api_error",
                                                         "message": str(e)}})
            except Exception:
                pass
            self._log_finalize(500, error_msg=str(e))

    # ---- web search (opt-in) ------------------------------------------------

    def _run_web_linked(self, prompt, model, system, key, input_format="text",
                        account=None):
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
                                               input_format=input_format, account=account)
            except ClaudeError as e:
                if not (linked and resume_id and _is_invalid_session_error(str(e))):
                    raise
                sessions.forget_session(key)
                blocks, wrapper, sid = run_web(prompt, model=model, system=system,
                                               resume=None, persist=True, cwd=cwd,
                                               input_format=input_format, account=account)
            if linked and sid:
                sessions.record_session(key, sid,
                                        account_id=account["id"] if account else None)
            return blocks, wrapper
        finally:
            if lock:
                lock.release()

    def _web_result(self, prompt, model, system, key, input_format, caps):
        """(blocks, wrapper) for a web run with account routing: stateless
        requests fail over across Claude accounts; linked ones are bound to
        the session's account. Returns (result, None) or (None, err_tuple)."""
        if key is None:
            return self._attempt_accounts(caps, lambda acc: self._run_web_linked(
                prompt, model, system, None, input_format, account=acc))
        acc, err = self._resolve_session_account(key, caps)
        if err is not None:
            return None, err
        try:
            result = self._run_web_linked(prompt, model, system, key,
                                          input_format, account=acc)
        except BackendError as e:
            self._note_backend_failure(acc, str(e))
            status, etype = classify_claude_error(str(e))
            return None, (status, etype, str(e))
        accounts.report_ok(acc["id"])
        self._record_serving(acc)
        return result, None

    def _blocking_web(self, prompt, model, system, key, input_format="text",
                      caps=None):
        result, err = self._web_result(prompt, model, system, key, input_format, caps)
        if err is not None:
            return self._send_error(*err)
        blocks, wrapper = result
        self._send_json(200, translate.web_message(blocks, wrapper, model))

    def _stream_web(self, prompt, model, system, key, input_format="text",
                    caps=None):
        # The web run is buffered (the agentic loop can't be a single live
        # stream), so do the work first; a failure here can still be a clean
        # JSON error since no SSE headers have been sent yet.
        result, err = self._web_result(prompt, model, system, key, input_format, caps)
        if err is not None:
            return self._send_error(*err)
        blocks, wrapper = result

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

    def _blocking_session(self, prompt, model, system, key, input_format="text",
                          stop_seqs=None, max_toks=None, thinking=False, caps=None):
        """Run linked to the key's session: resume it, persist, serialize.

        Sessions are account-bound — no failover here (continuity wins), but
        limit/auth failures still update the account's runtime state so other
        traffic routes around it."""
        acc, err = self._resolve_session_account(key, caps)
        if err is not None:
            return self._send_error(*err)
        cwd = str(sessions.WORKSPACE)
        blocks = None

        def _run(resume):
            out = run_blocking(prompt, model=model, system=system,
                               resume=resume, persist=True, cwd=cwd,
                               input_format=input_format, collect_blocks=thinking,
                               account=acc)
            return out if thinking else (out, None)

        with sessions.key_lock(key):
            resume_id = sessions.get_session_id(key)
            try:
                wrapper, blocks = _run(resume_id)
            except ClaudeError as e:
                # Only start over if the session id is genuinely bad — a transient
                # error (rate limit, backend hiccup) must NOT destroy the link.
                if not resume_id or not _is_invalid_session_error(str(e)):
                    self._note_backend_failure(acc, str(e))
                    return self._send_claude_error(e)
                sessions.forget_session(key)
                try:
                    wrapper, blocks = _run(None)
                except ClaudeError as e2:
                    self._note_backend_failure(acc, str(e2))
                    return self._send_claude_error(e2)
            sid = wrapper.get("session_id")
            if sid:
                sessions.record_session(key, sid, account_id=acc["id"])
        accounts.report_ok(acc["id"])
        self._record_serving(acc)
        msg = translate.wrapper_to_message(wrapper, model, blocks=blocks)
        self._send_json(200, limits.apply_to_message(msg, stop_seqs, max_toks))

    def _stream_messages(self, prompt, model, system, key, input_format="text",
                         stop_seqs=None, max_toks=None, thinking=False, caps=None,
                         codex_payload=None):
        """Streaming with account routing. _open_stream pulls each candidate
        generator to its first real event BEFORE any bytes ship, so a
        rate-limited account fails over invisibly; only a committed stream
        gets SSE headers."""
        lock = sessions.key_lock(key) if key else None
        if lock:
            lock.acquire()
        try:
            if key:
                acc, err = self._resolve_session_account(key, caps)
                if err is not None:
                    return self._send_error(*err)
                candidates = [acc]
            else:
                candidates = accounts.eligible(caps)
            resume_id = sessions.get_session_id(key) if key else None

            def make_gen(a):
                if a["backend"] == "codex":
                    # Codex "streaming" is a buffered single-message replay —
                    # the whole run happens before the first yielded event, so
                    # _open_stream's failover covers it for free.
                    text, images = codex_payload or (prompt, None)
                    return codex_mod.stream_shim(text, model, system=system,
                                                 images=images, thinking=thinking,
                                                 account=a)
                return stream_events(
                    prompt, model=model, system=system,
                    resume=resume_id, persist=bool(key),
                    cwd=str(sessions.WORKSPACE) if key else None,
                    input_format=input_format, account=a)

            acc, events, err = self._open_stream(candidates, make_gen)
            if events is None:
                return self._send_error(*err)
            self._emit_stream(events, key, resume_id, acc,
                              stop_seqs, max_toks, thinking)
        finally:
            if lock:
                lock.release()

    def _emit_stream(self, events, key, resume_id, acc,
                     stop_seqs=None, max_toks=None, thinking=False):
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

        # Limit gate: scans text deltas for stop_sequences / the token budget.
        # When it trips we synthesize the API's own ending (trimmed delta,
        # content_block_stop, message_delta with the real stop_reason,
        # message_stop) and abandon the CLI stream — closing the generator
        # kills the subprocess (see stream_events).
        gate = limits.LimitGate(stop_seqs, max_toks)
        # Thinking blocks are stripped (with re-indexing) unless requested.
        tfilter = translate.ThinkingFilter(thinking)
        text_blocks = set()  # indices (post-filter) that streamed text deltas

        def _end_stream_early(block_index):
            sse("content_block_stop", {"type": "content_block_stop", "index": block_index})
            sse("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": gate.stop_reason,
                          "stop_sequence": gate.stop_sequence},
                "usage": {"output_tokens": gate.emitted_tokens()},
            })
            sse("message_stop", {"type": "message_stop"})

        try:
            captured_sid = None
            saw_error = False
            log_in = log_out = None
            text_parts = []  # accumulate streamed text for the activity log
            for kind, obj in events:
                if kind == "session":
                    captured_sid = obj
                elif kind == "event":
                    obj = tfilter.feed(obj)
                    if obj is None:
                        continue
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
                            if gate.active:
                                text_blocks.add(obj.get("index", 0))
                                emit, done = gate.feed(delta["text"])
                                if emit:
                                    text_parts.append(emit)
                                    obj = dict(obj, delta=dict(delta, text=emit))
                                    sse(et, obj)
                                if done:
                                    log_out = gate.emitted_tokens()
                                    _end_stream_early(obj.get("index", 0))
                                    break
                                continue
                            text_parts.append(delta["text"])
                    elif (et == "content_block_stop" and gate.active
                          and obj.get("index", 0) in text_blocks):
                        # Release the scanner's withheld tail inside the block
                        # it belongs to, before forwarding the block close.
                        tail = gate.flush()
                        if tail:
                            text_parts.append(tail)
                            sse("content_block_delta", {
                                "type": "content_block_delta",
                                "index": obj.get("index", 0),
                                "delta": {"type": "text_delta", "text": tail},
                            })
                        if gate.finished:
                            # The budget ran out exactly on the withheld tail.
                            log_out = gate.emitted_tokens()
                            _end_stream_early(obj.get("index", 0))
                            break
                    sse(et or "message_delta", obj)
                elif kind == "error":
                    saw_error = True
                    sse("error", {"type": "error", "error": {"type": "api_error", "message": obj.get("message", "")}})
            if key:
                if saw_error and resume_id and captured_sid is None:
                    sessions.forget_session(key)  # likely a stale session id
                elif captured_sid:
                    sessions.record_session(key, captured_sid,
                                            account_id=acc["id"] if acc else None)
            self._log_finalize(
                500 if saw_error else 200,
                in_tokens=log_in, out_tokens=log_out,
                response_text="".join(text_parts) if not saw_error else None,
                error_msg="upstream stream error" if saw_error else None,
            )
        except BackendError as e:
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
