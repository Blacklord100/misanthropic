"""In-memory ring buffer of recent /v1/messages requests, for the dashboard.

Cleared on restart — this is observability for live debugging, not auditing.
Capped at MAX_ENTRIES so a long-running server can't grow unbounded. Thread-safe;
the server's ThreadingHTTPServer handles requests in parallel.

The dashboard polls /admin/requests (localhost-only) every few seconds and
renders entries here; see dashboard.py.
"""
import threading
from collections import deque

MAX_ENTRIES = 200

_lock = threading.Lock()
_buf = deque(maxlen=MAX_ENTRIES)


def append(entry):
    """Append a finished request record. Newest entries replace oldest at cap."""
    with _lock:
        _buf.append(entry)


def recent():
    """Snapshot, newest-first. Each entry is a JSON-safe dict."""
    with _lock:
        return list(reversed(_buf))


def clear():
    with _lock:
        _buf.clear()
