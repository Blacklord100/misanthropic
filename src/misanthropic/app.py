"""Misanthropic menu-bar app (macOS).

Launches the local Anthropic-compatible server in a background thread and exposes
start/stop, the dashboard, and a "start at login" toggle from the menu bar. The
server is the same one as `misanthropic serve` — this is just a native shell.

Requires `rumps` (an optional dependency): pip install "misanthropic[app]".
"""

import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from xml.sax.saxutils import escape

from . import __version__, claude, doctor, server, sessions, settings, updater

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8787"))
BASE_URL = f"http://{HOST}:{PORT}"

# Menu status line per doctor status. The app NEVER refuses to start: a broken
# environment shows amber/red here and a fix-it screen in the dashboard.
STATUS_LINES = {
    "ok":            "🟢 Serving on {base}",
    "unknown":       "🟢 Serving on {base}",
    "no_binary":     "🟠 Claude CLI not found — open dashboard to fix",
    "not_logged_in": "🔴 Claude CLI not logged in — open dashboard to fix",
    "error":         "🟠 Last check failed — open dashboard for details",
    "stopped":       "⚪ Server stopped",
}

LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.misanthropic.app.plist"
ICON_PATH = Path(__file__).parent / "resources" / "menubar.png"


def _claude_ready():
    """Is the `claude` CLI installed? (Auth itself is checked on first request.)

    Uses claude.claude_available() — which discovers claude even under the minimal
    PATH a Finder/login-launched .app inherits — not a bare which() on this PATH."""
    return claude.claude_available()


def _running_app_bundle():
    """If we're running inside a py2app-frozen .app, return its bundle path."""
    if not getattr(sys, "frozen", False):
        return None
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def _login_program_args():
    """LaunchAgent ProgramArguments that actually launch something that exists.

    The old code hardcoded `open -a Misanthropic` against a bundle that didn't
    exist (no packaging yet), so login launch silently did nothing. Now: prefer
    the real .app bundle — the one we're frozen inside, or a built one in
    /Applications or ~/Applications (see packaging/build.sh) — then fall back to
    the console script, then a bare module run."""
    bundles = [b for b in (
        _running_app_bundle(),
        Path("/Applications/Misanthropic.app"),
        Path.home() / "Applications" / "Misanthropic.app",
    ) if b and b.exists()]
    if bundles:
        return ["/usr/bin/open", str(bundles[0])]
    exe = shutil.which("misanthropic-app")
    if exe:
        return [exe]
    return [sys.executable, "-m", "misanthropic.app"]


def _pbcopy(text):
    try:
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
        return True
    except Exception:
        return False


def main():
    try:
        import rumps
    except ImportError:
        raise SystemExit(
            "The menu-bar app needs `rumps`. Install it with:\n"
            '    pip install "misanthropic[app]"\n'
            "or just run the server directly: misanthropic serve"
        )

    class Misanthropic(rumps.App):
        def __init__(self):
            # Icon-only menu bar: a skull silhouette with an Anthropic-style
            # asterisk on the forehead. Loaded as a template image so macOS
            # auto-tints it for light/dark. If the bundled asset is missing for
            # any reason, fall back to the old text title.
            if ICON_PATH.exists():
                super().__init__("Misanthropic", title=None,
                                 icon=str(ICON_PATH), template=True,
                                 quit_button=None)
            else:
                super().__init__("☠ MA", quit_button=None)
            self.httpd = None
            self.thread = None
            # Status line first: always visible, never a dead-end. Clicking it
            # opens the dashboard (which renders the matching fix-it screen).
            self.status_item = rumps.MenuItem("…", callback=self.open_dashboard)
            self.toggle_item = rumps.MenuItem("Stop server", callback=self.toggle)
            # Checked = force web on for every request; unchecked = "auto", where
            # each request decides for itself via the web_search tool (faithful to
            # the hosted API). The hard "off" kill-switch is env-only.
            self.web_item = rumps.MenuItem("Force web search on", callback=self.toggle_web)
            self.web_item.state = (claude.web_policy() == "on")
            self.login_item = rumps.MenuItem("Start at login", callback=self.toggle_login)
            self.login_item.state = LAUNCH_AGENT.exists()
            # Account picker: one checkmarked item per account plus "Auto".
            # Rebuilt on every health tick so cooldowns/serving state stay
            # fresh without a click.
            self.account_menu = rumps.MenuItem("Account")
            self._rebuild_account_menu()
            # Update checking. When a newer release is found the item becomes a
            # one-click "Install vX & Relaunch" (in-place, no browser) for .app
            # installs, or a download link for pip installs. With auto-install
            # on, the periodic check installs silently once the server is idle.
            self._update_url = None        # download page (pip installs / fallback)
            self._update_info = None       # full appcast dict (in-place install)
            self._update_result = None     # (manual, info) handed off from the worker thread
            self._installing = False
            self._silent_pending = False   # auto-install waiting for an idle server
            self.update_item = rumps.MenuItem("Check for Updates…", callback=self.on_update_item)
            self.autocheck_item = rumps.MenuItem("Auto-check for updates", callback=self.toggle_autocheck)
            self.autocheck_item.state = updater.auto_check_enabled()
            self.autoinstall_item = rumps.MenuItem("Install updates automatically",
                                                   callback=self.toggle_autoinstall)
            self.autoinstall_item.state = updater.auto_install_enabled()
            self.menu = [
                self.status_item,
                None,
                self.toggle_item,
                self.web_item,
                self.account_menu,
                rumps.MenuItem("Open dashboard", callback=self.open_dashboard),
                rumps.MenuItem("Copy base URL", callback=self.copy_base_url),
                None,
                self.update_item,
                self.autocheck_item,
                self.autoinstall_item,
                None,
                self.login_item,
                None,
                rumps.MenuItem("Buy me a coffee ☕", callback=lambda _:
                               webbrowser.open("https://paypal.me/Blacklord100")),
                rumps.MenuItem("Quit", callback=self.quit),
            ]
            settings.apply_startup()
            # The app starts no matter what. A missing/logged-out CLI is a
            # status, not a fatal error — the dashboard walks the user through
            # fixing it, and the health loop notices when it's fixed.
            self.start_server()
            self._doctor_status = None
            self._apply_status("unknown" if _claude_ready() else "no_binary")
            # A cheap drain timer applies worker-thread results on the main
            # thread (rumps UI is main-thread only); slow timers drive periodic
            # update checks and the environment health loop.
            self._drain_timer = rumps.Timer(self._drain_update_result, 2)
            self._drain_timer.start()
            self._autocheck_timer = rumps.Timer(self._autocheck_tick, 6 * 3600)
            self._autocheck_timer.start()
            self._health_timer = rumps.Timer(self._health_tick, 300)
            self._health_timer.start()
            self._spawn_health_check()
            if updater.auto_check_enabled():
                self._spawn_update_check(manual=False)

        # ---- account picker ----
        def _rebuild_account_menu(self):
            from . import accounts
            # A fresh MenuItem has no backing NSMenu yet; clear() would crash
            # on it. Only clear once items exist (add() creates the submenu).
            if len(self.account_menu):
                self.account_menu.clear()
            pinned = accounts.pinned()
            serving = accounts.serving({"text": True})
            auto = rumps.MenuItem("Auto (priority order)",
                                  callback=self._on_pick_account)
            auto.state = pinned is None
            auto._account_id = None
            self.account_menu.add(auto)
            self.account_menu.add(None)
            cds, logged_out = accounts.cooldown_state()
            for acc in accounts.list_accounts():
                suffix = ""
                if serving and acc["id"] == serving["id"]:
                    suffix = "  · serving"
                elif acc["id"] in cds:
                    suffix = "  · limited"
                elif acc["id"] in logged_out:
                    suffix = "  · logged out"
                elif not acc.get("enabled", True):
                    suffix = "  · disabled"
                item = rumps.MenuItem(f"{acc['label']}{suffix}",
                                      callback=self._on_pick_account)
                item.state = acc["id"] == pinned
                item._account_id = acc["id"]
                self.account_menu.add(item)

        def _on_pick_account(self, sender):
            from . import accounts
            accounts.set_pinned(getattr(sender, "_account_id", None))
            self._rebuild_account_menu()

        # ---- environment health loop ----
        def _apply_status(self, status):
            self._doctor_status = status
            line = STATUS_LINES.get(status) or STATUS_LINES["error"]
            self.status_item.title = line.format(base=f"{HOST}:{PORT}")
            try:
                self._rebuild_account_menu()
            except Exception:
                pass  # the picker must never take down the status light

        def _spawn_health_check(self):
            """Re-diagnose off the main thread; the drain timer applies it.
            Catches 'CLI moved after an update' and 'logged out mid-day' before
            a user request has to fail to reveal it."""
            def work():
                if not claude.claude_available():
                    claude.reset_resolution()  # maybe it moved — rediscover
                snap = doctor.snapshot()
                self._health_result = snap["status"]
            self._health_result = None
            threading.Thread(target=work, daemon=True).start()

        def _health_tick(self, _):
            self._spawn_health_check()

        # ---- server lifecycle ----
        def start_server(self):
            if self.httpd:
                return
            self.httpd = server.make_httpd(HOST, PORT)
            self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self.thread.start()
            self.toggle_item.title = "Stop server"
            if getattr(self, "_doctor_status", None) == "stopped":
                self._apply_status("unknown")
                self._spawn_health_check()

        def stop_server(self):
            if not self.httpd:
                return
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
            self.thread = None
            self.toggle_item.title = "Start server"
            self._apply_status("stopped")

        def toggle(self, _):
            self.stop_server() if self.httpd else self.start_server()

        # ---- menu actions ----
        def toggle_web(self, sender):
            force_on = not bool(sender.state)
            claude.set_web_policy("on" if force_on else "auto")
            sender.state = force_on
            rumps.notification(
                "Misanthropic",
                "Web search forced " + ("on" if force_on else "off (auto)"),
                "Every new request can search the web." if force_on
                else "Per request: only calls that include the web_search tool search the web.",
            )

        def open_dashboard(self, _):
            webbrowser.open(BASE_URL)

        def copy_base_url(self, _):
            import rumps as _r
            _pbcopy(BASE_URL)
            _r.notification("Misanthropic", "Copied", BASE_URL)

        # ---- updates ----
        def _can_self_update(self):
            info = self._update_info or {}
            return (updater.can_install_in_place()
                    and bool(info.get("sha256")) and bool(info.get("dmg_url")))

        def on_update_item(self, _):
            # One click, no browser: a known update installs in place when we
            # can self-swap. Pip installs (or a sha-less appcast) fall back to
            # the download page. No update known -> fresh manual check.
            if self._installing:
                return
            if self._update_info and self._can_self_update():
                self._install_update(self._update_info)
            elif self._update_url:
                webbrowser.open(self._update_url)
            else:
                self.update_item.title = "Checking…"
                self._spawn_update_check(manual=True)

        def toggle_autocheck(self, sender):
            new = not bool(sender.state)
            updater.set_auto_check(new)
            sender.state = new

        def toggle_autoinstall(self, sender):
            new = not bool(sender.state)
            updater.set_auto_install(new)
            sender.state = new

        def _spawn_update_check(self, manual=False):
            """Run the network check off the main thread; stash the result for the
            drain timer to apply. Never blocks the menu."""
            def work():
                info = updater.check_for_update(respect_skip=not manual)
                self._update_result = (manual, info)
            threading.Thread(target=work, daemon=True).start()

        def _autocheck_tick(self, _):
            if updater.auto_check_enabled():
                self._spawn_update_check(manual=False)

        def _drain_update_result(self, _):
            # Health verdicts ride the same main-thread drain as update checks.
            health = getattr(self, "_health_result", None)
            if health is not None:
                self._health_result = None
                if self.httpd:  # don't overwrite the explicit "stopped" state
                    self._apply_status(health)
            # A silent install that deferred (requests were in flight) retries
            # here until the server has a quiet moment.
            if getattr(self, "_silent_pending", False):
                self._try_silent_install()
            result = self._update_result
            if result is None:
                return
            self._update_result = None
            manual, info = result
            if info:
                version = info.get("version", "?")
                self._update_info = info
                self._update_url = info.get("download_page") or info.get("dmg_url")
                if self._can_self_update():
                    self.update_item.title = f"⬆ Install v{version} & Relaunch"
                else:
                    self.update_item.title = f"⬆ Download v{version}…"
                if manual:
                    self._prompt_update(info)
                elif updater.auto_install_enabled() and self._can_self_update():
                    # Hands-off path: install now if idle; otherwise wait for a
                    # quiet moment (re-checked every drain tick, i.e. ~2s).
                    self._try_silent_install()
                elif not updater.already_notified(version):
                    updater.mark_notified(version)
                    rumps.notification(
                        "Misanthropic",
                        f"Update available — v{version}",
                        (info.get("notes") or "One click in the menu bar installs it.").strip()[:200],
                    )
            elif manual:
                self.update_item.title = "Check for Updates…"
                rumps.alert("You're up to date", f"v{__version__} is the latest version.")

        def _try_silent_install(self):
            """Auto-install once no generation is in flight. Called when the
            periodic check finds an update, and again from the drain timer
            while one is pending — a busy server defers, never interrupts."""
            if self._installing or not self._update_info:
                return
            if server.requests_in_flight() > 0:
                self._silent_pending = True
                return
            self._silent_pending = False
            version = self._update_info.get("version", "?")
            rumps.notification("Misanthropic", f"Updating to v{version}…",
                               "Installing in the background; the app will relaunch itself.")
            self._install_update(self._update_info)

        def _prompt_update(self, info):
            version = info.get("version", "?")
            notes = (info.get("notes") or "").strip()
            message = f"Misanthropic v{version} is available."
            if notes:
                message += "\n\n" + notes
            in_place = updater.can_install_in_place() and info.get("sha256") and info.get("dmg_url")
            # rumps.alert -> legacy NSAlert returns: ok=1, cancel=0, other=-1.
            resp = rumps.alert(
                title="Update available",
                message=message,
                ok="Install & Relaunch" if in_place else "Download",
                cancel="Later",
                other="Skip This Version",
            )
            if resp == 1:
                if in_place:
                    self._install_update(info)
                elif self._update_url:
                    webbrowser.open(self._update_url)
            elif resp == -1:
                updater.mark_skipped(version)
                self.update_item.title = "Check for Updates…"
                self._update_url = None

        def _install_update(self, info):
            """Sparkle-style in-place update: download, verify sha256, swap the
            bundle, relaunch. Runs off the main thread; the menu shows progress."""
            def work():
                err = updater.download_and_install(
                    info, progress=lambda m: setattr(self.update_item, "title", m))
                self._install_error = err
                self._install_done = err is None
            self._installing = True
            self._install_error = None
            self._install_done = False
            self.update_item.title = "Downloading update…"
            def watch(_):
                if getattr(self, "_install_done", False):
                    self._watch_timer.stop()
                    self.quit(None)  # the new instance opens itself
                elif getattr(self, "_install_error", None):
                    self._watch_timer.stop()
                    self._installing = False
                    self.update_item.title = "Check for Updates…"
                    rumps.alert("Update failed", self._install_error)
            self._watch_timer = rumps.Timer(watch, 1)
            self._watch_timer.start()
            threading.Thread(target=work, daemon=True).start()

        def toggle_login(self, sender):
            if sender.state:
                LAUNCH_AGENT.unlink(missing_ok=True)
                sender.state = False
            else:
                self._install_login_agent()
                sender.state = True

        def _install_login_agent(self):
            LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
            args_xml = "".join(f"<string>{escape(a)}</string>" for a in _login_program_args())
            plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.misanthropic.app</string>
  <key>ProgramArguments</key><array>{args_xml}</array>
  <key>RunAtLoad</key><true/>
</dict></plist>
"""
            LAUNCH_AGENT.write_text(plist)

        def quit(self, _):
            import rumps as _r
            self.stop_server()
            _r.quit_application()

    Misanthropic().run()


if __name__ == "__main__":
    main()
