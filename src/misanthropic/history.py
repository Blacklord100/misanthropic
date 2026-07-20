"""Durable request history (SQLite) + in-process change feed.

Replaces the restart-losing in-memory ring as the dashboard's source of truth.
Every finished /v1/messages request is inserted into ~/.misanthropic/history.db
(same MISANTHROPIC_HOME override as the rest of the state), and any subscriber
(the /admin/events SSE endpoint) is notified so the UI updates without polling.

The estimated hosted-API cost is computed once at insert time and stored per
row, so per-key stats and the savings sparkline are a single SQL query.

Stdlib only. One connection, serialized by a lock — write volume here is one
row per generation, far below anything SQLite would notice. The DB path is
resolved lazily through sessions.CONFIG_DIR so tests that patch it are
isolated automatically.
"""

import json
import queue
import sqlite3
import threading
import time

from . import pricing, sessions

_lock = threading.Lock()
_conn = None
_conn_path = None

_subscribers = []                      # list[queue.Queue] for the SSE feed
_subscribers_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    key_label     TEXT DEFAULT '',
    model         TEXT DEFAULT '',
    mode          TEXT DEFAULT '',
    stream        INTEGER DEFAULT 0,
    status        INTEGER,
    duration_ms   INTEGER,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_write   INTEGER DEFAULT 0,
    cache_read    INTEGER DEFAULT 0,
    web_requests  INTEGER DEFAULT 0,
    usd           REAL DEFAULT 0,
    prompt_text   TEXT DEFAULT '',
    response_text TEXT DEFAULT '',
    error         TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests (ts);
CREATE INDEX IF NOT EXISTS idx_requests_key ON requests (key_label);
"""

# Columns added after v1.2 ship as ALTER TABLE migrations in _connect() (there
# is no migration framework; keep these appended LAST so _COLUMNS order matches
# the on-disk layout of migrated databases).
_MIGRATIONS = (
    "ALTER TABLE requests ADD COLUMN account TEXT DEFAULT ''",
    "ALTER TABLE requests ADD COLUMN backend TEXT DEFAULT ''",
)

_COLUMNS = ("id", "ts", "key_label", "model", "mode", "stream", "status",
            "duration_ms", "input_tokens", "output_tokens", "cache_write",
            "cache_read", "web_requests", "usd", "prompt_text",
            "response_text", "error", "account", "backend")


def _db_path():
    return sessions.CONFIG_DIR / "history.db"


def _connect():
    """(Re)open the connection if the resolved path changed (tests patch it)."""
    global _conn, _conn_path
    path = str(_db_path())
    if _conn is not None and _conn_path == path:
        return _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    sessions._ensure_dirs()
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    for ddl in _MIGRATIONS:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    _conn, _conn_path = conn, path
    return conn


def _row_to_dict(row):
    d = dict(zip(_COLUMNS, row))
    d["stream"] = bool(d["stream"])
    return d


# ---- change feed (SSE) -------------------------------------------------------

def subscribe():
    """Register a queue that receives {"event": ..., "data": ...} dicts."""
    q = queue.Queue(maxsize=256)
    with _subscribers_lock:
        _subscribers.append(q)
    return q


def unsubscribe(q):
    with _subscribers_lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def notify(event, data=None):
    """Push a change event to every live subscriber. Never blocks or raises."""
    with _subscribers_lock:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait({"event": event, "data": data or {}})
        except queue.Full:
            pass  # a stalled client skips events; it re-syncs on next fetch


# ---- writes -------------------------------------------------------------------

def append(rec):
    """Insert one finished request record; returns the row id (or None).

    Failures are swallowed — history must never take down a generation. The
    hosted-API cost is priced here so it lives with the row forever, immune to
    future pricing-table changes.
    """
    try:
        usd = pricing.estimated_cost(
            rec.get("model"),
            rec.get("input_tokens", 0) or 0,
            rec.get("output_tokens", 0) or 0,
            rec.get("web_requests", 0) or 0,
            rec.get("cache_write", 0) or 0,
            rec.get("cache_read", 0) or 0,
        ) if rec.get("status") == 200 else 0.0
        with _lock:
            conn = _connect()
            cur = conn.execute(
                "INSERT INTO requests (ts, key_label, model, mode, stream, status,"
                " duration_ms, input_tokens, output_tokens, cache_write, cache_read,"
                " web_requests, usd, prompt_text, response_text, error, account, backend)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rec.get("ts") or time.time(),
                    rec.get("key_label", ""),
                    rec.get("model", ""),
                    rec.get("mode", ""),
                    1 if rec.get("stream") else 0,
                    rec.get("status"),
                    rec.get("duration_ms"),
                    rec.get("input_tokens", 0) or 0,
                    rec.get("output_tokens", 0) or 0,
                    rec.get("cache_write", 0) or 0,
                    rec.get("cache_read", 0) or 0,
                    rec.get("web_requests", 0) or 0,
                    usd,
                    rec.get("prompt_text", "") or "",
                    rec.get("response_text", "") or "",
                    rec.get("error", "") or "",
                    rec.get("account", "") or "",
                    rec.get("backend", "") or "",
                ),
            )
            conn.commit()
            row_id = cur.lastrowid
        summary = {k: rec.get(k) for k in
                   ("ts", "key_label", "model", "mode", "status", "duration_ms",
                    "input_tokens", "output_tokens")}
        summary["id"] = row_id
        notify("request", summary)
        return row_id
    except Exception:
        return None


# ---- reads ---------------------------------------------------------------------

def recent(limit=50, before_id=None, key_label=None, model=None,
           status=None, q=None, account=None):
    """Filtered page of requests, newest first. `before_id` paginates backwards.

    `status` filters a class: "ok" (200) or "error" (non-200). `q` substring-
    matches prompt/response text.
    """
    sql = f"SELECT {','.join(_COLUMNS)} FROM requests"
    where, params = [], []
    if before_id is not None:
        where.append("id < ?"); params.append(before_id)
    if key_label:
        where.append("key_label = ?"); params.append(key_label)
    if model:
        where.append("model LIKE ?"); params.append(f"%{model}%")
    if account:
        where.append("account = ?"); params.append(account)
    if status == "ok":
        where.append("status = 200")
    elif status == "error":
        where.append("status != 200")
    if q:
        where.append("(prompt_text LIKE ? OR response_text LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    try:
        with _lock:
            rows = _connect().execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception:
        return []


def get(row_id):
    try:
        with _lock:
            row = _connect().execute(
                f"SELECT {','.join(_COLUMNS)} FROM requests WHERE id = ?", (row_id,)
            ).fetchone()
        return _row_to_dict(row) if row else None
    except Exception:
        return None


def count():
    try:
        with _lock:
            return _connect().execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    except Exception:
        return 0


def daily_series(days=30):
    """[{day, requests, usd, output_tokens}] for the last `days` days (UTC),
    zero-filled so sparklines have a stable x-axis."""
    days = max(1, min(int(days), 365))
    since = time.time() - days * 86400
    try:
        with _lock:
            rows = _connect().execute(
                "SELECT date(ts, 'unixepoch') d, COUNT(*), COALESCE(SUM(usd),0),"
                " COALESCE(SUM(output_tokens),0)"
                " FROM requests WHERE ts >= ? GROUP BY d ORDER BY d", (since,)
            ).fetchall()
    except Exception:
        rows = []
    by_day = {r[0]: r for r in rows}
    out = []
    for i in range(days - 1, -1, -1):
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - i * 86400))
        r = by_day.get(day)
        out.append({
            "day": day,
            "requests": r[1] if r else 0,
            "usd": round(r[2], 6) if r else 0.0,
            "output_tokens": r[3] if r else 0,
        })
    return out


def key_stats():
    """{key_label: {requests, usd}} across all time — powers the Keys page."""
    try:
        with _lock:
            rows = _connect().execute(
                "SELECT key_label, COUNT(*), COALESCE(SUM(usd),0)"
                " FROM requests GROUP BY key_label"
            ).fetchall()
        return {r[0]: {"requests": r[1], "usd": round(r[2], 4)} for r in rows}
    except Exception:
        return {}


def account_stats():
    """Per-account usage for the Accounts page — tokens and cost are tracked
    separately per account here; every other surface stays aggregate.
    "in" tokens = input + cache write + cache read (what a prompt costs).
    Pre-accounts rows (account='') bucket under ''."""
    midnight = time.mktime(time.strptime(
        time.strftime("%Y-%m-%d", time.gmtime()), "%Y-%m-%d"))
    try:
        with _lock:
            rows = _connect().execute(
                "SELECT account, COUNT(*), COALESCE(SUM(usd),0),"
                " COALESCE(SUM(output_tokens),0),"
                " COALESCE(SUM(input_tokens + cache_write + cache_read),0),"
                " SUM(CASE WHEN ts >= ? THEN 1 ELSE 0 END),"
                " COALESCE(SUM(CASE WHEN ts >= ? THEN output_tokens ELSE 0 END),0),"
                " COALESCE(SUM(CASE WHEN ts >= ? THEN usd ELSE 0 END),0)"
                " FROM requests GROUP BY account",
                (midnight, midnight, midnight)
            ).fetchall()
        return {r[0]: {"requests": r[1], "usd": round(r[2], 4),
                       "output_tokens": r[3], "input_tokens": r[4],
                       "today_requests": r[5], "today_output_tokens": r[6],
                       "today_usd": round(r[7], 4)} for r in rows}
    except Exception:
        return {}


def _percentile(sorted_vals, p):
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1, int(round(p / 100.0 * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def analytics(days=30):
    """Everything the Analytics page shows, in one pass over the window.

    Aggregated in Python rather than N SQL queries — a window is thousands of
    small rows at most, and this keeps percentiles/derived shares trivial.
    """
    days = max(1, min(int(days), 365))
    now = time.time()
    since = now - days * 86400
    try:
        with _lock:
            rows = _connect().execute(
                "SELECT ts, status, duration_ms, input_tokens, output_tokens,"
                " cache_write, cache_read, web_requests, usd, model, mode,"
                " account, backend, stream FROM requests WHERE ts >= ?",
                (since,)).fetchall()
    except Exception:
        rows = []

    # Pre-account-tracking rows were all served by the default Claude login;
    # attribute them to it (same rule as the Accounts page).
    try:
        from . import accounts as accounts_mod
        default_acc = next((a for a in accounts_mod.list_accounts()
                            if a["backend"] == "claude"
                            and (a.get("auth") or {}).get("kind") == "default"), None)
        legacy_label = default_acc["label"] if default_acc else "(pre-accounts)"
    except Exception:
        legacy_label = "(pre-accounts)"

    daily = {}
    groups = {"account": {}, "model": {}, "mode": {}, "backend": {}}
    durations = []
    tot = {"requests": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0,
           "cache_write": 0, "cache_read": 0, "web_requests": 0, "usd": 0.0,
           "streamed": 0}

    def bump(bucket, row_ok, dur, i, o, usd):
        bucket["requests"] += 1
        if not row_ok:
            bucket["errors"] += 1
        bucket["input_tokens"] += i
        bucket["output_tokens"] += o
        bucket["usd"] += usd
        if dur:
            bucket["durations"].append(dur)

    def fresh():
        return {"requests": 0, "errors": 0, "input_tokens": 0,
                "output_tokens": 0, "usd": 0.0, "durations": []}

    for (ts, status, dur, i, o, cw, cr, web, usd, model, mode, account,
         backend, stream) in rows:
        ok = status == 200
        i, o, cw, cr = i or 0, o or 0, cw or 0, cr or 0
        usd = usd or 0.0
        day = time.strftime("%Y-%m-%d", time.gmtime(ts))
        d = daily.setdefault(day, {"requests": 0, "errors": 0,
                                   "input_tokens": 0, "output_tokens": 0,
                                   "usd": 0.0})
        d["requests"] += 1
        d["errors"] += 0 if ok else 1
        d["input_tokens"] += i
        d["output_tokens"] += o
        d["usd"] += usd

        for kind, key in (("account", account or legacy_label),
                          ("model", model or "?"),
                          ("mode", mode or "?"),
                          ("backend", backend or "claude")):
            bump(groups[kind].setdefault(key, fresh()), ok, dur, i, o, usd)

        tot["requests"] += 1
        tot["errors"] += 0 if ok else 1
        tot["input_tokens"] += i
        tot["output_tokens"] += o
        tot["cache_write"] += cw
        tot["cache_read"] += cr
        tot["web_requests"] += web or 0
        tot["usd"] += usd
        tot["streamed"] += 1 if stream else 0
        if dur:
            durations.append(dur)

    series = []
    for n in range(days - 1, -1, -1):
        day = time.strftime("%Y-%m-%d", time.gmtime(now - n * 86400))
        d = daily.get(day) or {"requests": 0, "errors": 0, "input_tokens": 0,
                               "output_tokens": 0, "usd": 0.0}
        series.append({"day": day, **{k: (round(v, 4) if k == "usd" else v)
                                      for k, v in d.items()}})

    def finish(bucket):
        durs = sorted(bucket.pop("durations"))
        bucket["usd"] = round(bucket["usd"], 4)
        bucket["avg_ms"] = int(sum(durs) / len(durs)) if durs else 0
        bucket["p95_ms"] = int(_percentile(durs, 95))
        return bucket

    durations.sort()
    prompt_total = tot["input_tokens"] + tot["cache_write"] + tot["cache_read"]
    return {
        "days": days,
        "series": series,
        "by_account": {k: finish(v) for k, v in groups["account"].items()},
        "by_model": {k: finish(v) for k, v in groups["model"].items()},
        "by_mode": {k: finish(v) for k, v in groups["mode"].items()},
        "by_backend": {k: finish(v) for k, v in groups["backend"].items()},
        "totals": {
            **{k: (round(v, 4) if k == "usd" else v) for k, v in tot.items()},
            "error_rate": round(tot["errors"] / tot["requests"], 4) if tot["requests"] else 0,
            "avg_ms": int(sum(durations) / len(durations)) if durations else 0,
            "p50_ms": int(_percentile(durations, 50)),
            "p95_ms": int(_percentile(durations, 95)),
            "cache_read_share": round(tot["cache_read"] / prompt_total, 4) if prompt_total else 0,
            "stream_share": round(tot["streamed"] / tot["requests"], 4) if tot["requests"] else 0,
        },
    }


def prune(keep_days=None):
    """Optional retention: delete rows older than keep_days."""
    if not keep_days:
        return 0
    cutoff = time.time() - float(keep_days) * 86400
    try:
        with _lock:
            conn = _connect()
            cur = conn.execute("DELETE FROM requests WHERE ts < ?", (cutoff,))
            conn.commit()
            return cur.rowcount
    except Exception:
        return 0
