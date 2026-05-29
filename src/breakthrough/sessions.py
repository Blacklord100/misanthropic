"""Approved API keys and the key -> claude-session mapping.

The design: an API key both *authorizes* a client and *names* a conversation.
The first request under a key starts a persistent claude session; later requests
under the same key `--resume` it, so the whole chat accumulates in one session
that's visible and resumable in the Claude Code CLI / desktop app.

State lives under ~/.breakthrough (override with BREAKTHROUGH_HOME):
  keys.json      list of approved keys (managed via `breakthrough keys ...`)
  sessions.json  { "<key>": {"session_id", "turns", "updated"} }
  workspace/     a stable cwd so `claude --resume` (project-scoped) resolves
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("BREAKTHROUGH_HOME", Path.home() / ".breakthrough"))
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

def approved_keys():
    """The set of approved keys, from BREAKTHROUGH_KEYS env and keys.json."""
    keys = set()
    env = os.environ.get("BREAKTHROUGH_KEYS")
    if env:
        keys |= {k.strip() for k in env.split(",") if k.strip()}
    stored = _read_json(KEYS_FILE, [])
    if isinstance(stored, list):
        keys |= {str(k) for k in stored}
    return keys


def session_mode_enabled():
    """Session/key mode is on whenever any approved key is configured."""
    return bool(approved_keys())


def is_approved(key):
    return key in approved_keys()


def add_key(key):
    with _file_lock:
        stored = _read_json(KEYS_FILE, [])
        if not isinstance(stored, list):
            stored = []
        if key not in stored:
            stored.append(key)
            _write_json(KEYS_FILE, stored)


def remove_key(key):
    with _file_lock:
        stored = _read_json(KEYS_FILE, [])
        if isinstance(stored, list) and key in stored:
            stored.remove(key)
            _write_json(KEYS_FILE, stored)
        sessions = _read_json(SESSIONS_FILE, {})
        if key in sessions:
            sessions.pop(key)
            _write_json(SESSIONS_FILE, sessions)


# ---- key -> session mapping -------------------------------------------------

def get_session_id(key):
    with _file_lock:
        return _read_json(SESSIONS_FILE, {}).get(key, {}).get("session_id")

def record_session(key, session_id):
    """Store/refresh the session id for a key and bump its turn counter."""
    with _file_lock:
        sessions = _read_json(SESSIONS_FILE, {})
        rec = sessions.get(key, {})
        rec["session_id"] = session_id
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
