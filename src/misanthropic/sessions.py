"""Approved API keys and the key -> claude-session mapping.

The design: an API key both *authorizes* a client and *names* a conversation.
The first request under a key starts a persistent claude session; later requests
under the same key `--resume` it, so the whole chat accumulates in one session
that's visible and resumable in the Claude Code CLI / desktop app.

State lives under ~/.misanthropic (override with MISANTHROPIC_HOME):
  keys.json      list of approved keys (managed via `misanthropic keys ...`)
  sessions.json  { "<key>": {"session_id", "turns", "updated"} }
  workspace/     a stable cwd so `claude --resume` (project-scoped) resolves
"""

import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("MISANTHROPIC_HOME", Path.home() / ".misanthropic"))
KEYS_FILE = CONFIG_DIR / "keys.json"
SESSIONS_FILE = CONFIG_DIR / "sessions.json"
WORKSPACE = CONFIG_DIR / "workspace"

_file_lock = threading.Lock()          # guards reads/writes of the json stores
_key_locks = {}                        # one lock per key -> serialize same-key requests
_key_locks_guard = threading.Lock()


def _ensure_dirs():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)


def _read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path, data):
    _ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)  # atomic


# ---- approved keys ----------------------------------------------------------
#
# keys.json is a dict: { "<key>": {"label": str, "created": iso} }. Older
# list-format files (["k1", "k2"]) are still read and migrated on next write.

KEY_PREFIX = "sk-ant-local-"


def _read_keys():
    """Return the keys store as a {key: meta} dict, migrating legacy list form."""
    stored = _read_json(KEYS_FILE, {})
    if isinstance(stored, list):
        return {str(k): {"label": "", "created": ""} for k in stored}
    if isinstance(stored, dict):
        return stored
    return {}


def approved_keys():
    """The set of approved keys, from MISANTHROPIC_KEYS env and keys.json."""
    keys = set(_read_keys().keys())
    env = os.environ.get("MISANTHROPIC_KEYS")
    if env:
        keys |= {k.strip() for k in env.split(",") if k.strip()}
    return keys


def keys_detail():
    """{key: {label, created}} for every stored key (excludes env-only keys)."""
    return _read_keys()


def session_mode_enabled():
    """Session/key mode is on whenever any approved key is configured."""
    return bool(approved_keys())


def is_approved(key):
    return key in approved_keys()


def generate_key():
    """A fresh random key that looks like an Anthropic key (drops into tooling)."""
    return KEY_PREFIX + secrets.token_urlsafe(24)


def add_key(key, label=""):
    with _file_lock:
        store = _read_keys()
        if key not in store:
            store[key] = {"label": label, "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            _write_json(KEYS_FILE, store)


def create_key(label=""):
    """Generate, store, and return a new labeled key (for the dashboard / CLI)."""
    key = generate_key()
    add_key(key, label)
    return key


def key_failover(key):
    """This key's failover override: "on" | "off" | None (inherit the global
    failover_policy setting). Lets each connected project decide whether its
    requests may hop accounts on a usage limit."""
    with _file_lock:
        return _read_keys().get(key, {}).get("failover")


def set_key_failover(key, mode):
    with _file_lock:
        store = _read_keys()
        if key not in store:
            raise KeyError(key)
        if mode in ("on", "off"):
            store[key]["failover"] = mode
        else:
            store[key].pop("failover", None)  # "default" = inherit global
        _write_json(KEYS_FILE, store)


def remove_key(key):
    with _file_lock:
        store = _read_keys()
        if key in store:
            store.pop(key)
            _write_json(KEYS_FILE, store)
        sessions = _read_json(SESSIONS_FILE, {})
        if key in sessions:
            sessions.pop(key)
            _write_json(SESSIONS_FILE, sessions)


# ---- key -> session mapping -------------------------------------------------

def get_session_id(key):
    with _file_lock:
        return _read_json(SESSIONS_FILE, {}).get(key, {}).get("session_id")


def get_session_account(key):
    """Which account this key's session lives on (sessions are account-bound:
    a claude session id only resumes under the login that created it)."""
    with _file_lock:
        return _read_json(SESSIONS_FILE, {}).get(key, {}).get("account_id")


def record_session(key, session_id, account_id=None):
    """Store/refresh the session id for a key and bump its turn counter."""
    with _file_lock:
        sessions = _read_json(SESSIONS_FILE, {})
        rec = sessions.get(key, {})
        rec["session_id"] = session_id
        if account_id:
            rec["account_id"] = account_id
        rec["turns"] = rec.get("turns", 0) + 1
        rec["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sessions[key] = rec
        _write_json(SESSIONS_FILE, sessions)


def forget_session(key):
    """Drop the mapping so the key's next request starts a fresh session."""
    with _file_lock:
        sessions = _read_json(SESSIONS_FILE, {})
        if key in sessions:
            sessions.pop(key)
            _write_json(SESSIONS_FILE, sessions)


def all_sessions():
    with _file_lock:
        return _read_json(SESSIONS_FILE, {})


# ---- per-key serialization --------------------------------------------------

def key_lock(key):
    """A lock unique to this key, so concurrent same-key requests don't both
    resume (and clobber) the one underlying session."""
    with _key_locks_guard:
        lock = _key_locks.get(key)
        if lock is None:
            lock = _key_locks[key] = threading.Lock()
        return lock
