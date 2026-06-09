"""Persistent "money you didn't spend" tally.

Every successful generation adds the hosted-API list price of its tokens to a
running total, persisted under ~/.misanthropic/savings.json so it survives
restarts (the request log is in-memory and clears; this does not). The dashboard
reads summary() to show "you'd have paid $X on the API — Misanthropic charged $0."

Atomic writes mirror sessions.py so a crash can't corrupt the file. Every failure
path is swallowed: a broken savings file must never take down a generation.
"""

import json
import os
import threading
from datetime import datetime, timezone

from . import pricing
from .sessions import CONFIG_DIR

SAVINGS_FILE = CONFIG_DIR / "savings.json"
_lock = threading.Lock()


def _empty():
    return {"all_time": _bucket(), "months": {}, "since": None}


def _bucket():
    return {"usd": 0.0, "input_tokens": 0, "output_tokens": 0, "requests": 0}


def _load():
    try:
        data = json.loads(SAVINGS_FILE.read_text())
        if isinstance(data, dict) and "all_time" in data:
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return _empty()


def _save(state):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SAVINGS_FILE.with_suffix(SAVINGS_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        os.replace(tmp, SAVINGS_FILE)  # atomic
    except OSError:
        pass


def _add(bucket, usd, in_tokens, out_tokens):
    bucket["usd"] = round(bucket.get("usd", 0.0) + usd, 6)
    bucket["input_tokens"] = bucket.get("input_tokens", 0) + (in_tokens or 0)
    bucket["output_tokens"] = bucket.get("output_tokens", 0) + (out_tokens or 0)
    bucket["requests"] = bucket.get("requests", 0) + 1


def record(model, input_tokens=0, output_tokens=0, web_search_requests=0):
    """Add one generation's would-have-cost to the tally. Returns the USD added."""
    usd = pricing.estimated_cost(model, input_tokens, output_tokens, web_search_requests)
    if usd <= 0 and not input_tokens and not output_tokens:
        return 0.0
    now = datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")
    with _lock:
        state = _load()
        _add(state["all_time"], usd, input_tokens, output_tokens)
        bucket = state.setdefault("months", {}).setdefault(month, _bucket())
        _add(bucket, usd, input_tokens, output_tokens)
        if not state.get("since"):
            state["since"] = now.isoformat(timespec="seconds")
        _save(state)
    return usd


def summary():
    """A JSON-safe snapshot for the dashboard: all-time + current-month totals."""
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    with _lock:
        state = _load()
    all_time = state.get("all_time", _bucket())
    this_month = state.get("months", {}).get(month, _bucket())
    return {
        "all_time_usd": round(all_time.get("usd", 0.0), 2),
        "month_usd": round(this_month.get("usd", 0.0), 2),
        "month": month,
        "all_time_requests": all_time.get("requests", 0),
        "month_requests": this_month.get("requests", 0),
        "input_tokens": all_time.get("input_tokens", 0),
        "output_tokens": all_time.get("output_tokens", 0),
        "since": state.get("since"),
    }
