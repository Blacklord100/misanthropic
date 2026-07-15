"""Check a public appcast for a newer Misanthropic release.

The menu-bar app polls this so people who installed the `.dmg` learn when a newer
build ships. v1 only *notifies* (and opens the download page) — it never downloads
or replaces anything. Pure stdlib, so the package keeps its zero runtime deps.

Every failure path is swallowed and returns ``None``: a flaky network, a missing
feed, or malformed JSON must never break or hang the app.

The feed is a small JSON manifest (`appcast.json`) committed at the root of the
public repo and served via raw.githubusercontent.com. Schema::

    {
      "version": "0.7.0",
      "download_page": "https://github.com/.../releases/tag/v0.7.0",
      "dmg_url": "https://github.com/.../Misanthropic-0.7.0.dmg",  # reserved (auto-install phase)
      "sha256": "<dmg sha256>",                                    # reserved
      "notes": "short summary",
      "min_macos": "11.0"
    }

Point `MISANTHROPIC_APPCAST_URL` at a local ``file://`` manifest to test.
"""

import json
import os
import ssl
import urllib.request
from datetime import datetime, timezone

from . import __version__
from .sessions import CONFIG_DIR

APPCAST_URL = os.environ.get(
    "MISANTHROPIC_APPCAST_URL",
    "https://raw.githubusercontent.com/Blacklord100/misanthropic/master/appcast.json",
)
STATE_FILE = CONFIG_DIR / "updater.json"
_TIMEOUT_S = 6


# ---- version comparison -----------------------------------------------------

def parse_version(s):
    """'v1.2.3' / '1.2.3-rc1' -> a comparable tuple of ints.

    Splits on '.' after dropping any pre-release suffix, so '1.2.3-rc1' compares
    as (1, 2, 3) — slightly lenient, fine for our simple MAJOR.MINOR.PATCH tags.
    Non-numeric chunks degrade to 0 rather than raising.
    """
    s = (s or "").strip().lstrip("vV")
    main = s.split("-", 1)[0].split("+", 1)[0]
    parts = []
    for chunk in main.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts) or (0,)


def is_newer(remote, current):
    return parse_version(remote) > parse_version(current)


# ---- feed fetch -------------------------------------------------------------

def _fetch_json(url, timeout=_TIMEOUT_S):
    """GET the appcast and parse it. Returns a dict, or None on any failure.

    A time-bucketed cache-buster is appended: raw.githubusercontent.com's CDN
    caches ~5 minutes per URL (query string included in the cache key), which
    otherwise makes "Check for Updates" report stale news right after a
    release. Bucketing to 60s keeps the CDN mostly warm while capping staleness
    at a minute."""
    try:
        import time as _time
        if "://" in url and "?" not in url:
            url = f"{url}?t={int(_time.time() // 60)}"
        req = urllib.request.Request(
            url, headers={"User-Agent": f"misanthropic/{__version__}"}
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# ---- persisted prefs (~/.misanthropic/updater.json) -------------------------
#
# Mirrors the atomic-write pattern in sessions.py so a crash can't corrupt it.

def _load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE_FILE)  # atomic


def auto_check_enabled():
    return bool(_load_state().get("auto_check", True))


def auto_install_enabled():
    """Silently install updates found by the periodic check (default on).
    Only meaningful when running frozen in a .app — pip installs update via pip."""
    return bool(_load_state().get("auto_install", True))


def set_auto_install(enabled):
    s = _load_state()
    s["auto_install"] = bool(enabled)
    _save_state(s)
    return bool(enabled)


def set_auto_check(enabled):
    s = _load_state()
    s["auto_check"] = bool(enabled)
    _save_state(s)
    return bool(enabled)


def is_skipped(version):
    return _load_state().get("skipped_version") == version


def mark_skipped(version):
    s = _load_state()
    s["skipped_version"] = version
    _save_state(s)


def already_notified(version):
    return _load_state().get("last_notified_version") == version


def mark_notified(version):
    s = _load_state()
    s["last_notified_version"] = version
    _save_state(s)


# ---- in-place install ---------------------------------------------------------
#
# Builds are Developer ID-signed and notarized (v1.0.1+), so a downloaded app
# passes Gatekeeper and we can swap the bundle Sparkle-style instead of just
# opening the release page. Only attempted when running frozen inside a .app;
# a pip/pipx install updates through pip, not here.

def _running_bundle():
    import sys
    from pathlib import Path
    if not getattr(sys, "frozen", False):
        return None
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def can_install_in_place():
    return _running_bundle() is not None


def download_and_install(info, progress=None):
    """Download the release DMG, verify its sha256, replace the running bundle,
    and relaunch. Returns an error string on failure, None on success (at which
    point a new instance is launching and the caller should quit).

    Every step is verified before anything is touched: the swap happens only
    after the checksum matches and the mounted DMG contains a .app.
    """
    import hashlib
    import subprocess
    import tempfile
    from pathlib import Path

    def report(msg):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    bundle = _running_bundle()
    if bundle is None:
        return "not running from a .app bundle"
    url = info.get("dmg_url")
    expected_sha = (info.get("sha256") or "").strip().lower()
    if not url or not expected_sha:
        return "appcast is missing dmg_url or sha256"

    tmp = Path(tempfile.mkdtemp(prefix="misanthropic-update-"))
    dmg = tmp / "update.dmg"
    mnt = tmp / "mnt"
    try:
        report("Downloading update…")
        req = urllib.request.Request(url, headers={"User-Agent": f"misanthropic/{__version__}"})
        ctx = ssl.create_default_context()
        h = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=120, context=ctx) as resp, open(dmg, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                h.update(chunk)
                f.write(chunk)
        if h.hexdigest().lower() != expected_sha:
            return "checksum mismatch — download discarded"

        report("Installing…")
        mnt.mkdir()
        r = subprocess.run(["hdiutil", "attach", str(dmg), "-nobrowse", "-quiet",
                            "-mountpoint", str(mnt)], capture_output=True, text=True)
        if r.returncode != 0:
            return f"could not mount update: {r.stderr.strip() or r.returncode}"
        try:
            apps = list(mnt.glob("*.app"))
            if not apps:
                return "update image contains no .app"
            staged = bundle.with_suffix(".app.update")
            subprocess.run(["rm", "-rf", str(staged)], check=False)
            r = subprocess.run(["ditto", str(apps[0]), str(staged)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                return f"copy failed: {r.stderr.strip() or r.returncode}"
            old = bundle.with_suffix(".app.old")
            subprocess.run(["rm", "-rf", str(old)], check=False)
            os.rename(bundle, old)
            try:
                os.rename(staged, bundle)
            except OSError:
                os.rename(old, bundle)  # roll back — never leave no app at all
                raise
            subprocess.run(["rm", "-rf", str(old)], check=False)
        finally:
            subprocess.run(["hdiutil", "detach", str(mnt), "-quiet"], check=False)

        report("Relaunching…")
        # Delay the open until after the caller has quit — `open` on an app
        # that's still running would just focus the dying instance.
        subprocess.Popen(["/bin/sh", "-c",
                          f'sleep 1; /usr/bin/open "{bundle}"'],
                         start_new_session=True)
        return None
    except Exception as e:
        return f"update failed: {e}"
    finally:
        subprocess.run(["rm", "-rf", str(tmp)], check=False)


# ---- the check --------------------------------------------------------------

def check_for_update(current=None, url=None, respect_skip=True):
    """Return the appcast dict if a newer release exists, else None. Never raises.

    `current` defaults to the running version, `url` to APPCAST_URL. A manual
    check should pass ``respect_skip=False`` so an explicitly-requested check
    still surfaces a version the user previously chose to skip.
    """
    current = current or __version__
    data = _fetch_json(url or APPCAST_URL)
    if not data:
        return None
    remote = data.get("version")
    if not remote or not is_newer(remote, current):
        return None
    if respect_skip and is_skipped(remote):
        return None
    try:
        s = _load_state()
        s["last_check"] = datetime.now(timezone.utc).isoformat()
        _save_state(s)
    except Exception:
        pass
    return data
