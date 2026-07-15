"""Structured environment diagnosis: is this install able to serve requests?

One source of truth for "what state are we in", consumed by three surfaces:
the menu-bar status item, the dashboard's Doctor page, and the onboarding
wizard. Three checks, cheapest first:

  binary  — can we locate the `claude` executable, and how was it found?
  version — what does `claude --version` say? (also proves the binary runs)
  login   — can it actually generate? A tiny real completion, because the CLI
            has no stable "auth status" command; this is the only honest probe.
            Expensive (seconds), so it runs only on demand or from the health
            loop, and its verdict is cached with a timestamp.

status() collapses the checks into one word the UI can key off:
  "ok" | "no_binary" | "not_logged_in" | "unknown" (binary fine, login unprobed)
"""

import os
import subprocess
import threading
import time

from . import claude

_lock = threading.Lock()
_version_cache = {}     # {path: (version, checked_at)}
_login_cache = {"ok": None, "detail": "", "checked_at": None}

VERSION_TTL_S = 15 * 60
_PROBE_TIMEOUT_S = 45


def _binary_check():
    path = claude.claude_bin()
    found = bool(claude.claude_available())
    return {
        "found": found,
        "path": path if found else None,
        "source": claude.resolution_source() if found else None,
    }


def _version_check(path):
    now = time.time()
    with _lock:
        cached = _version_cache.get(path)
        if cached and now - cached[1] < VERSION_TTL_S:
            return cached[0]
    version = None
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True,
                             timeout=10, env=claude._child_env())
        if out.returncode == 0:
            version = (out.stdout or out.stderr).strip().splitlines()[0][:120]
    except Exception:
        pass
    with _lock:
        _version_cache[path] = (version, now)
    return version


def probe_login(force=False, max_age_s=600):
    """Run (or reuse) the login probe: one minimal haiku completion.

    Returns {"ok": bool|None, "detail": str, "checked_at": iso|None}. A cached
    verdict younger than max_age_s is reused unless force=True.
    """
    with _lock:
        cached = dict(_login_cache)
    if (not force and cached["checked_at"] is not None
            and time.time() - cached["checked_at"] < max_age_s):
        return _login_view(cached)

    ok, detail = None, ""
    try:
        wrapper = claude.run_blocking(
            "Reply with the single word: ok",
            model="haiku", system="Reply with the single word: ok",
            timeout=_PROBE_TIMEOUT_S,
        )
        ok = bool(wrapper.get("result"))
        detail = "generated a reply" if ok else "empty reply"
    except claude.ClaudeError as e:
        ok = False
        detail = str(e)[:500]
    except Exception as e:
        ok = False
        detail = f"probe failed: {e}"[:500]
    with _lock:
        _login_cache.update(ok=ok, detail=detail, checked_at=time.time())
        cached = dict(_login_cache)
    return _login_view(cached)


def _login_view(cached):
    ts = cached.get("checked_at")
    return {
        "ok": cached.get("ok"),
        "detail": cached.get("detail", ""),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else None,
    }


def login_looks_like_auth_error(detail):
    """Heuristic: does a probe/other failure read as 'not logged in'?"""
    m = (detail or "").lower()
    return any(s in m for s in (
        "not logged in", "login", "log in", "authentication", "unauthorized",
        "api key", "credentials", "oauth", "please run /login", "invalid bearer",
    ))


def rescan():
    """Forget every cache and re-run discovery (dashboard 'Re-scan' button)."""
    claude.reset_resolution()
    with _lock:
        _version_cache.clear()
        _login_cache.update(ok=None, detail="", checked_at=None)
    return snapshot()


def snapshot(probe=False):
    """The full diagnosis. Cheap unless probe=True (which runs the login probe)."""
    binary = _binary_check()
    version = _version_check(binary["path"]) if binary["found"] else None
    login = probe_login() if (probe and binary["found"]) else _login_view(dict(_login_cache))

    if not binary["found"]:
        status = "no_binary"
    elif login["ok"] is False and login_looks_like_auth_error(login["detail"]):
        status = "not_logged_in"
    elif login["ok"] is False:
        status = "error"
    elif login["ok"] is None:
        status = "unknown"   # binary present, login not yet probed
    else:
        status = "ok"

    return {
        "status": status,
        "binary": binary,
        "cli_version": version,
        "login": login,
        "home": str(os.environ.get("MISANTHROPIC_HOME", "~/.misanthropic")),
    }
