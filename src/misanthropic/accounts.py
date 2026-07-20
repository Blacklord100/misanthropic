"""Account registry, eligibility routing, and limit cooldowns.

Misanthropic can fulfill requests from several accounts across two backends:

  * "claude" — the Claude Code CLI. Multi-account via CLAUDE_CONFIG_DIR: a
    fresh config dir has its own login (spike-verified on v2.1.177 — a fresh
    dir answers "Not logged in", so auth does NOT fall back to the global
    Keychain). Auth kinds: "default" (the ~/.claude login) or
    "config_dir" {path}.
  * "codex" — the OpenAI Codex CLI. Multi-account via CODEX_HOME
    (auth.json/config.toml live there). Auth kind: "codex_home" {path}.

The registry lives in ~/.misanthropic/accounts.json (0600 — it names auth
dirs). With no file, a single implicit default Claude account is used, so
upgraders see zero change; the file materializes on first mutation.

Failover: when the serving account hits its usage limit the router hands the
request to the next eligible account. Cooldowns are in-memory only — a
restart retrying a limited account once is self-healing. Capability routing:
tools / web / key-linked sessions are Claude-only; a request needing them
never routes to codex (and 529s when no Claude account is eligible — no
silent degradation).
"""

import copy
import os
import re
import threading
import time
import uuid

from .sessions import _write_json

COOLDOWN_S = float(os.environ.get("MISANTHROPIC_COOLDOWN_S", "900"))          # 15 min
COOLDOWN_MAX_S = float(os.environ.get("MISANTHROPIC_COOLDOWN_MAX_S", "14400"))  # 4 h
_STRIKE_WINDOW_S = 7200.0  # strikes older than this don't escalate

_lock = threading.Lock()
_cooldowns = {}   # id -> {"until": float, "reason": str, "strikes": int, "last_hit": float}
_logged_out = {}  # id -> {"detail": str, "at": float}

DEFAULT_ACCOUNT = {
    "id": "claude-default",
    "label": "Claude (default)",
    "backend": "claude",
    "auth": {"kind": "default"},
    "priority": 0,
    "enabled": True,
}

# What each backend can serve. Tools/web/sessions are Claude-only in v1:
# tools ride the MCP-shim parking machinery, web the WebSearch remap, and
# sessions the --resume flow — all claude-specific.
_CAPS = {
    "claude": {"tools", "web", "session", "images", "thinking", "text"},
    "codex": {"images", "thinking", "text"},
}


def _accounts_path():
    from . import sessions  # lazy: tests repoint sessions.CONFIG_DIR
    return sessions.CONFIG_DIR / "accounts.json"


def _load():
    import json
    try:
        data = json.loads(_accounts_path().read_text())
        if isinstance(data, dict) and isinstance(data.get("accounts"), list):
            return data
    except (FileNotFoundError, ValueError, OSError):
        pass
    return None


def _save(data):
    path = _accounts_path()
    _write_json(path, data)
    try:
        os.chmod(path, 0o600)  # auth dirs/tokens are named in here
    except OSError:
        pass


def list_accounts():
    """All accounts, priority-ordered. Falls back to the implicit default."""
    data = _load()
    if data is None:
        return [copy.deepcopy(DEFAULT_ACCOUNT)]
    accs = [a for a in data["accounts"] if isinstance(a, dict) and a.get("id")]
    return sorted(accs, key=lambda a: (a.get("priority", 0)))


def get(account_id):
    for a in list_accounts():
        if a["id"] == account_id:
            return a
    return None


def _mutate(fn):
    """Load-or-materialize the registry, apply fn(data), save."""
    with _lock:
        data = _load()
        if data is None:
            data = {"version": 1, "pinned": None,
                    "accounts": [copy.deepcopy(DEFAULT_ACCOUNT)]}
        result = fn(data)
        _save(data)
        _notify()
        return result


def add(label, backend, auth=None):
    if backend not in _CAPS:
        raise ValueError(f"unknown backend: {backend}")
    account_id = uuid.uuid4().hex[:12]
    from . import sessions
    if backend == "codex" and not (auth or {}).get("path"):
        auth = {"kind": "codex_home",
                "path": str(sessions.CONFIG_DIR / "codex" / account_id)}
    elif backend == "claude" and auth is None:
        auth = {"kind": "config_dir",
                "path": str(sessions.CONFIG_DIR / "claude" / account_id)}
    if auth.get("path"):
        os.makedirs(os.path.expanduser(auth["path"]), exist_ok=True)
    acc = {"id": account_id, "label": label or f"{backend} account",
           "backend": backend, "auth": auth, "priority": len(list_accounts()),
           "enabled": True}

    def _do(data):
        data["accounts"].append(acc)
        return acc
    return _mutate(_do)


def update(account_id, label=None, priority=None, enabled=None):
    def _do(data):
        for a in data["accounts"]:
            if a["id"] == account_id:
                if label is not None:
                    a["label"] = label
                if priority is not None:
                    a["priority"] = int(priority)
                if enabled is not None:
                    a["enabled"] = bool(enabled)
                return a
        raise KeyError(account_id)
    return _mutate(_do)


def remove(account_id):
    def _do(data):
        data["accounts"] = [a for a in data["accounts"] if a["id"] != account_id]
        if data.get("pinned") == account_id:
            data["pinned"] = None
    _mutate(_do)
    with _lock:
        _cooldowns.pop(account_id, None)
        _logged_out.pop(account_id, None)


def pinned():
    data = _load()
    return data.get("pinned") if data else None


def set_pinned(account_id):
    def _do(data):
        data["pinned"] = account_id if account_id and any(
            a["id"] == account_id for a in data["accounts"]) else None
        return data["pinned"]
    return _mutate(_do)


# ---- eligibility & routing ---------------------------------------------------

def backend_supports(backend, caps):
    need = {k for k, v in (caps or {}).items() if v}
    return need <= _CAPS.get(backend, set())


def cooling(account_id, now=None):
    now = now if now is not None else time.time()
    with _lock:
        cd = _cooldowns.get(account_id)
        return bool(cd and cd["until"] > now)


def eligible(caps=None):
    """Accounts able to serve a request with these capability needs, in the
    order they should be tried: pinned first, then priority."""
    now = time.time()
    pin = pinned()
    out = []
    for a in list_accounts():
        if not a.get("enabled", True):
            continue
        if not backend_supports(a["backend"], caps):
            continue
        if cooling(a["id"], now):
            continue
        with _lock:
            if a["id"] in _logged_out:
                continue
        out.append(a)
    if pin:
        out.sort(key=lambda a: (a["id"] != pin,))  # stable: pinned to front
    return out


def first_choice(caps=None):
    """The account that WOULD serve by pin/priority, ignoring cooldowns and
    logged-out marks — what "failover off" pins the request to. None only when
    no enabled account supports the caps at all."""
    pin = pinned()
    candidates = [a for a in list_accounts()
                  if a.get("enabled", True) and backend_supports(a["backend"], caps)]
    if pin:
        for a in candidates:
            if a["id"] == pin:
                return a
    return candidates[0] if candidates else None


def failover_enabled(key_override=None):
    """Whether THIS request may hop accounts on a usage limit. The per-key
    override ("on"/"off" from keys.json) wins; otherwise the global
    failover_policy setting decides — default "off": never automatic unless
    the user picked it."""
    if key_override in ("on", "off"):
        return key_override == "on"
    from . import settings
    return settings.get("failover_policy") == "auto"


def serving(caps=None):
    order = eligible(caps)
    if order:
        return order[0]
    # Everything cooling/logged-out: the UI's "serving" is still the account
    # requests would try first.
    return first_choice(caps)


def claude_only(caps):
    """True when these caps can only ever be served by the claude backend."""
    return not backend_supports("codex", caps)


# ---- error classification & cooldown bookkeeping -----------------------------

# Account-exhaustion signals. Deliberately EXCLUDES "overloaded"/"capacity"/
# "529" — those are provider-side incidents that hit every account equally;
# cooling an account for them would help nothing.
_LIMIT_PATTERNS = ("usage limit", "rate limit", "too many requests", "quota",
                   "plan limit", "limit reached", "limit will reset", "429")

_AUTH_PATTERNS = ("not logged in", "please run /login", "authentication",
                  "unauthorized", "invalid api key", "credentials", "oauth",
                  "codex login", "log in", "login")


def classify(backend, message):
    """"limit" | "auth" | "other" — strict, used ONLY for failover/cooldown
    decisions (server.classify_claude_error keeps doing HTTP status mapping)."""
    m = (message or "").lower()
    if any(p in m for p in _LIMIT_PATTERNS):
        return "limit"
    if any(p in m for p in _AUTH_PATTERNS):
        return "auth"
    return "other"


_RESET_RE = re.compile(r"reset[s]?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.I)


def _parse_reset_seconds(message, now=None):
    """Best-effort: claude limit errors sometimes say 'resets at 3pm'. Returns
    seconds until then (local time, next occurrence), or None."""
    m = _RESET_RE.search(message or "")
    if not m:
        return None
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
    minute = int(m.group(2) or 0)
    now = now if now is not None else time.time()
    lt = time.localtime(now)
    target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, minute, 0,
                          lt.tm_wday, lt.tm_yday, -1))
    if target <= now:
        target += 86400
    return target - now


def report_limited(account_id, message=""):
    """Escalating cooldown: 15 min -> 1 h -> 4 h (cap), reset when strikes are
    old. An explicit reset time in the error wins over backoff."""
    now = time.time()
    with _lock:
        cd = _cooldowns.get(account_id) or {"strikes": 0, "last_hit": 0.0}
        if now - cd["last_hit"] > _STRIKE_WINDOW_S:
            cd["strikes"] = 0
        cd["strikes"] += 1
        cd["last_hit"] = now
        backoff = min(COOLDOWN_S * (4 ** (cd["strikes"] - 1)), COOLDOWN_MAX_S)
        explicit = _parse_reset_seconds(message, now)
        cd["until"] = now + (explicit if explicit is not None else backoff)
        cd["reason"] = (message or "usage limit")[:200]
        _cooldowns[account_id] = cd
    _notify()


def mark_logged_out(account_id, detail=""):
    with _lock:
        _logged_out[account_id] = {"detail": (detail or "")[:200], "at": time.time()}
    _notify()


def report_ok(account_id):
    """A successful request/probe clears cooldown strikes and logged-out."""
    changed = False
    with _lock:
        changed = bool(_cooldowns.pop(account_id, None)) | bool(
            _logged_out.pop(account_id, None))
    if changed:
        _notify()


def cooldown_state():
    """Snapshot for the dashboard: {id: {until, seconds_left, reason}}."""
    now = time.time()
    out = {}
    with _lock:
        for aid, cd in _cooldowns.items():
            if cd["until"] > now:
                out[aid] = {"until": cd["until"],
                            "seconds_left": int(cd["until"] - now),
                            "reason": cd["reason"]}
        logged_out = {aid: dict(v) for aid, v in _logged_out.items()}
    return out, logged_out


def child_env_overlay(account):
    """Env vars that select this account in a spawned backend CLI."""
    if not account:
        return {}
    auth = account.get("auth") or {}
    kind = auth.get("kind")
    if kind == "config_dir":
        return {"CLAUDE_CONFIG_DIR": os.path.expanduser(auth.get("path", ""))}
    if kind == "codex_home":
        return {"CODEX_HOME": os.path.expanduser(auth.get("path", ""))}
    return {}  # "default"


def _notify():
    try:
        from . import history
        history.notify("state")
    except Exception:
        pass


def _reset():
    """Tests: drop all runtime state."""
    with _lock:
        _cooldowns.clear()
        _logged_out.clear()
