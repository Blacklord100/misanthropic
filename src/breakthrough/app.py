"""Breakthrough menu-bar app (macOS).

Launches the local Anthropic-compatible server in a background thread and exposes
start/stop, the dashboard, and a "start at login" toggle from the menu bar. The
server is the same one as `breakthrough serve` — this is just a native shell.

Requires `rumps` (an optional dependency): pip install "breakthrough[app]".
"""

import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from xml.sax.saxutils import escape

from . import __version__, claude, server, sessions, updater

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8787"))
BASE_URL = f"http://{HOST}:{PORT}"

LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.breakthrough.app.plist"
ICON_PATH = Path(__file__).parent / "resources" / "menubar.png"


def _claude_ready():
    """Is the `claude` CLI installed? (Auth itself is checked on first request.)"""
    return shutil.which(os.environ.get("CLAUDE_BIN", "claude")) is not None


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

    The old code hardcoded `open -a Breakthrough` against a bundle that didn't
    exist (no packaging yet), so login launch silently did nothing. Now: prefer
    the real .app bundle — the one we're frozen inside, or a built one in
    /Applications or ~/Applications (see packaging/build.sh) — then fall back to
    the console script, then a bare module run."""
    bundles = [b for b in (
        _running_app_bundle(),
        Path("/Applications/Breakthrough.app"),
        Path.home() / "Applications" / "Breakthrough.app",
    ) if b and b.exists()]
    if bundles:
        return ["/usr/bin/open", str(bundles[0])]
    exe = shutil.which("breakthrough-app")
    if exe:
        return [exe]
    return [sys.executable, "-m", "breakthrough.app"]


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
            '    pip install "breakthrough[app]"\n'
            "or just run the server directly: breakthrough serve"
        )

    class Breakthrough(rumps.App):
        def __init__(self):
            # Icon-only menu bar: a skull silhouette with an Anthropic-style
            # asterisk on the forehead. Loaded as a template image so macOS
            # auto-tints it for light/dark. If the bundled asset is missing for
            # any reason, fall back to the old text title.
            if ICON_PATH.exists():
                super().__init__("Breakthrough", title=None,
                                 icon=str(ICON_PATH), template=True,
                                 quit_button=None)
            else:
                super().__init__("◐ BT", quit_button=None)
            self.httpd = None
            self.thread = None
            self.toggle_item = rumps.MenuItem("Stop server", callback=self.toggle)
            self.web_item = rumps.MenuItem("Web search (internet)", callback=self.toggle_web)
            self.web_item.state = claude.web_enabled()
            self.login_item = rumps.MenuItem("Start at login", callback=self.toggle_login)
            self.login_item.state = LAUNCH_AGENT.exists()
            # Update checking: the item retitles to "Download vX…" when a newer
            # release is found; clicking it then opens the download page.
            self._update_url = None        # set when an update is available
            self._update_result = None     # (manual, info) handed off from the worker thread
            self.update_item = rumps.MenuItem("Check for Updates…", callback=self.on_update_item)
            self.autocheck_item = rumps.MenuItem("Auto-check for updates", callback=self.toggle_autocheck)
            self.autocheck_item.state = updater.auto_check_enabled()
            self.menu = [
                self.toggle_item,
                self.web_item,
                rumps.MenuItem("Open dashboard", callback=self.open_dashboard),
                rumps.MenuItem("Copy base URL", callback=self.copy_base_url),
                None,
                self.update_item,
                self.autocheck_item,
                None,
                self.login_item,
                None,
                rumps.MenuItem("Quit", callback=self.quit),
            ]
            if not _claude_ready():
                rumps.alert(
                    "Claude Code not found",
                    "Breakthrough needs the `claude` CLI installed and logged in. "
                    "Install Claude Code, then restart this app.",
                )
            self.start_server()
            # A cheap drain timer applies update-check results on the main thread
            # (rumps UI is main-thread only); a slow timer triggers periodic checks.
            self._drain_timer = rumps.Timer(self._drain_update_result, 2)
            self._drain_timer.start()
            self._autocheck_timer = rumps.Timer(self._autocheck_tick, 6 * 3600)
            self._autocheck_timer.start()
            if updater.auto_check_enabled():
                self._spawn_update_check(manual=False)

        # ---- server lifecycle ----
        def start_server(self):
            if self.httpd:
                return
            self.httpd = server.make_httpd(HOST, PORT)
            self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self.thread.start()
            self.toggle_item.title = "Stop server"

        def stop_server(self):
            if not self.httpd:
                return
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
            self.thread = None
            self.toggle_item.title = "Start server"

        def toggle(self, _):
            self.stop_server() if self.httpd else self.start_server()

        # ---- menu actions ----
        def toggle_web(self, sender):
            new = not bool(sender.state)
            claude.set_web_enabled(new)
            sender.state = new
            rumps.notification(
                "Breakthrough",
                "Web search " + ("enabled" if new else "disabled"),
                "New requests can search the web." if new else "Back to text-only (bare API).",
            )

        def open_dashboard(self, _):
            webbrowser.open(BASE_URL)

        def copy_base_url(self, _):
            import rumps as _r
            _pbcopy(BASE_URL)
            _r.notification("Breakthrough", "Copied", BASE_URL)

        # ---- updates ----
        def on_update_item(self, _):
            # Once an update is known, this item opens the download page;
            # otherwise it triggers a fresh manual check.
            if self._update_url:
                webbrowser.open(self._update_url)
            else:
                self.update_item.title = "Checking…"
                self._spawn_update_check(manual=True)

        def toggle_autocheck(self, sender):
            new = not bool(sender.state)
            updater.set_auto_check(new)
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
            result = self._update_result
            if result is None:
                return
            self._update_result = None
            manual, info = result
            if info:
                version = info.get("version", "?")
                self._update_url = info.get("download_page") or info.get("dmg_url")
                self.update_item.title = f"⬆ Download v{version}…"
                if manual:
                    self._prompt_update(info)
                elif not updater.already_notified(version):
                    updater.mark_notified(version)
                    rumps.notification(
                        "Breakthrough",
                        f"Update available — v{version}",
                        (info.get("notes") or "Click the menu-bar item to download.").strip()[:200],
                    )
            elif manual:
                self.update_item.title = "Check for Updates…"
                rumps.alert("You're up to date", f"v{__version__} is the latest version.")

        def _prompt_update(self, info):
            version = info.get("version", "?")
            notes = (info.get("notes") or "").strip()
            message = f"Breakthrough v{version} is available."
            if notes:
                message += "\n\n" + notes
            # rumps.alert -> legacy NSAlert returns: ok=1, cancel=0, other=-1.
            resp = rumps.alert(
                title="Update available",
                message=message,
                ok="Download",
                cancel="Later",
                other="Skip This Version",
            )
            if resp == 1 and self._update_url:
                webbrowser.open(self._update_url)
            elif resp == -1:
                updater.mark_skipped(version)
                self.update_item.title = "Check for Updates…"
                self._update_url = None

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
  <key>Label</key><string>com.breakthrough.app</string>
  <key>ProgramArguments</key><array>{args_xml}</array>
  <key>RunAtLoad</key><true/>
</dict></plist>
"""
            LAUNCH_AGENT.write_text(plist)

        def quit(self, _):
            import rumps as _r
            self.stop_server()
            _r.quit_application()

    Breakthrough().run()


if __name__ == "__main__":
    main()
