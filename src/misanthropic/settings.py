"""Persisted user settings (~/.misanthropic/settings.json).

Everything here was previously env-var-only, which a .app user can never set.
Env vars still win at startup (they're explicit), but changes made in the
dashboard persist here and apply live where the code reads through accessors.

Known keys:
  default_model    "sonnet" | "opus" | "haiku" (or any CLI-accepted alias)
  web_policy       "auto" | "on" | "off"
  onboarded        wizard completed (suppresses first-run)
  retention_days   history retention; 0/absent = keep forever
"""

import json
import os
import threading

from .sessions import CONFIG_DIR, _write_json

SETTINGS_FILE = CONFIG_DIR / "settings.json"
_lock = threading.Lock()

_ALLOWED = {"default_model", "web_policy", "onboarded", "retention_days"}


def _settings_path():
    # Re-derive from sessions.CONFIG_DIR so tests that patch it are isolated.
    from . import sessions
    return sessions.CONFIG_DIR / "settings.json"


def load():
    try:
        data = json.loads(_settings_path().read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def get(key, default=None):
    return load().get(key, default)


def update(changes):
    """Merge validated changes into the store; returns the new settings dict."""
    clean = {k: v for k, v in changes.items() if k in _ALLOWED}
    with _lock:
        data = load()
        data.update(clean)
        _write_json(_settings_path(), data)
    return data


def apply_startup():
    """Apply persisted settings at process start (env vars take precedence)."""
    from . import claude
    data = load()
    if "MISANTHROPIC_WEB" not in os.environ and data.get("web_policy") in ("auto", "on", "off"):
        try:
            claude.set_web_policy(data["web_policy"])
        except ValueError:
            pass
    if ("MISANTHROPIC_MODEL" not in os.environ and "MODEL" not in os.environ
            and data.get("default_model")):
        claude.DEFAULT_MODEL = str(data["default_model"])
