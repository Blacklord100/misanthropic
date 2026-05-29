"""Breakthrough menu-bar app (macOS).

Launches the local Anthropic-compatible server in a background thread and exposes
start/stop, the dashboard, and a "start at login" toggle from the menu bar. The
server is the same one as `breakthrough serve` — this is just a native shell.

Requires `rumps` (an optional dependency): pip install "breakthrough[app]".
"""

import os
import shutil
import subprocess
import threading
import webbrowser
from pathlib import Path

from . import __version__, server, sessions

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8787"))
BASE_URL = f"http://{HOST}:{PORT}"

LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.breakthrough.app.plist"


def _claude_ready():
    """Is the `claude` CLI installed? (Auth itself is checked on first request.)"""
    return shutil.which(os.environ.get("CLAUDE_BIN", "claude")) is not None


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
            super().__init__("◐ BT", quit_button=None)
            self.httpd = None
            self.thread = None
            self.toggle_item = rumps.MenuItem("Stop server", callback=self.toggle)
            self.login_item = rumps.MenuItem("Start at login", callback=self.toggle_login)
            self.login_item.state = LAUNCH_AGENT.exists()
            self.menu = [
                self.toggle_item,
                rumps.MenuItem("Open dashboard", callback=self.open_dashboard),
                rumps.MenuItem("Copy base URL", callback=self.copy_base_url),
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

        # ---- server lifecycle ----
        def start_server(self):
            if self.httpd:
                return
            self.httpd = server.make_httpd(HOST, PORT)
            self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self.thread.start()
            self.title = "◐ BT"
            self.toggle_item.title = "Stop server"

        def stop_server(self):
            if not self.httpd:
                return
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
            self.thread = None
            self.title = "○ BT"
            self.toggle_item.title = "Start server"

        def toggle(self, _):
            self.stop_server() if self.httpd else self.start_server()

        # ---- menu actions ----
        def open_dashboard(self, _):
            webbrowser.open(BASE_URL)

        def copy_base_url(self, _):
            import rumps as _r
            _pbcopy(BASE_URL)
            _r.notification("Breakthrough", "Copied", BASE_URL)

        def toggle_login(self, sender):
            if sender.state:
                LAUNCH_AGENT.unlink(missing_ok=True)
                sender.state = False
            else:
                self._install_login_agent()
                sender.state = True

        def _install_login_agent(self):
            LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
            plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.breakthrough.app</string>
  <key>ProgramArguments</key><array><string>/usr/bin/open</string><string>-a</string><string>Breakthrough</string></array>
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
