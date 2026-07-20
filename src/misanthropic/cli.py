"""`misanthropic` command.

  misanthropic serve [--host H --port P]        run the Anthropic-compatible server
  misanthropic chat "..." [--model --system]    one-off completion (quick test)
  misanthropic keys add|remove|list [KEY]       manage approved keys (= sessions)
  misanthropic sessions list|forget [KEY]       inspect / reset key->session links
  misanthropic accounts ACTION [ID]             manage backend accounts (claude/codex)
  misanthropic savings                          what you'd have paid on the hosted API

With no subcommand, `misanthropic` starts the server.
"""

import argparse
import os
import sys

from . import __version__, accounts, doctor, savings, server, sessions, translate
from .claude import ClaudeError, DEFAULT_MODEL, run_blocking


def _cmd_keys(args):
    if args.action == "add":
        if not args.key:
            print("usage: misanthropic keys add <key>", file=sys.stderr)
            return 1
        sessions.add_key(args.key)
        print(f"approved key added: {args.key}")
    elif args.action == "remove":
        if not args.key:
            print("usage: misanthropic keys remove <key>", file=sys.stderr)
            return 1
        sessions.remove_key(args.key)
        print(f"key removed (and its session forgotten): {args.key}")
    elif args.action == "list":
        keys = sorted(sessions.approved_keys())
        if not keys:
            print("no approved keys. add one with: misanthropic keys add <key>")
        else:
            for k in keys:
                print(k)
    return 0


def _cmd_sessions(args):
    if args.action == "list":
        store = sessions.all_sessions()
        if not store:
            print("no active sessions.")
        else:
            for key, rec in store.items():
                print(f"{key}\t{rec.get('session_id', '-')}\tturns={rec.get('turns', 0)}\t{rec.get('updated', '')}")
    elif args.action == "forget":
        if not args.key:
            print("usage: misanthropic sessions forget <key>", file=sys.stderr)
            return 1
        sessions.forget_session(args.key)
        print(f"session forgotten; next request under '{args.key}' starts fresh.")
    return 0


def _cmd_accounts(args):
    if args.action == "list":
        cds, logged_out = accounts.cooldown_state()
        pinned = accounts.pinned()
        serving = accounts.serving({"text": True})
        for acc in accounts.list_accounts():
            marks = []
            if serving and acc["id"] == serving["id"]:
                marks.append("serving")
            if acc["id"] == pinned:
                marks.append("pinned")
            if not acc.get("enabled", True):
                marks.append("disabled")
            if acc["id"] in cds:
                marks.append(f"limited ~{cds[acc['id']]['seconds_left']}s")
            if acc["id"] in logged_out:
                marks.append("logged out")
            print(f"{acc['id']}\t{acc['backend']}\t{acc['label']}"
                  f"\t{', '.join(marks) or '-'}")
        return 0
    if args.action == "add":
        if args.id not in ("claude", "codex"):
            print("usage: misanthropic accounts add claude|codex [--label L]",
                  file=sys.stderr)
            return 1
        acc = accounts.add(args.label or "", args.id)
        print(f"added {acc['backend']} account {acc['id']} ({acc['label']})")
        auth = acc.get("auth") or {}
        if acc["backend"] == "codex":
            print(f"log it in with:\n  CODEX_HOME={auth.get('path')} codex login")
        else:
            print(f"log it in with:\n  CLAUDE_CONFIG_DIR={auth.get('path')} claude "
                  f"# then /login inside")
        return 0
    if not args.id:
        print(f"usage: misanthropic accounts {args.action} <id>", file=sys.stderr)
        return 1
    acc = accounts.get(args.id)
    if args.action != "unpin" and acc is None:
        print(f"unknown account: {args.id}", file=sys.stderr)
        return 1
    if args.action == "remove":
        accounts.remove(args.id)
        print("removed.")
    elif args.action == "pin":
        accounts.set_pinned(args.id)
        print(f"pinned {acc['label']} — it now serves first.")
    elif args.action == "unpin":
        accounts.set_pinned(None)
        print("unpinned — priority order applies.")
    elif args.action == "enable":
        accounts.update(args.id, enabled=True)
        print("enabled.")
    elif args.action == "disable":
        accounts.update(args.id, enabled=False)
        print("disabled.")
    elif args.action == "probe":
        if acc["backend"] == "claude":
            doctor.probe_login(force=True, account=acc)
        st = doctor.account_status(acc, probe=acc["backend"] == "codex")
        print(f"{acc['label']}: {st['status']}"
              + (f" — {st['detail']}" if st.get("detail") else ""))
    return 0


def _money(n):
    n = float(n or 0)
    return f"${n:,.4f}" if 0 < n < 0.01 else f"${n:,.2f}"


def _cmd_savings():
    s = savings.summary()
    print(f"You'd have paid {_money(s['all_time_usd'])} on the hosted API.")
    print(f"Misanthropic charged you $0.00.")
    print(f"  this month: {_money(s['month_usd'])} ({s['month']})")
    print(f"  requests:   {s['all_time_requests']:,}  ·  "
          f"tokens: {s['input_tokens']:,} in / {s['output_tokens']:,} out")
    if s.get("since"):
        print(f"  since:      {s['since']}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="misanthropic",
        description="Anthropic-API-compatible server backed by your local Claude Code CLI. No API key.",
    )
    parser.add_argument("--version", action="version", version=f"misanthropic {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="Run the Anthropic-compatible API server")
    p_serve.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    p_serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8787")))

    p_chat = sub.add_parser("chat", help="One-off completion through the local CLI (quick test)")
    p_chat.add_argument("prompt", help="The user prompt")
    p_chat.add_argument("--model", default=DEFAULT_MODEL, help="Model alias or id (default: sonnet)")
    p_chat.add_argument("--system", help="Optional system prompt")

    p_keys = sub.add_parser("keys", help="Manage approved keys (each key names a session)")
    p_keys.add_argument("action", choices=["add", "remove", "list"])
    p_keys.add_argument("key", nargs="?", help="The API key")

    p_sessions = sub.add_parser("sessions", help="Inspect or reset key->session links")
    p_sessions.add_argument("action", choices=["list", "forget"])
    p_sessions.add_argument("key", nargs="?", help="The API key")

    p_accounts = sub.add_parser("accounts", help="Manage backend accounts (claude/codex)")
    p_accounts.add_argument("action", choices=["list", "add", "remove", "pin",
                                               "unpin", "enable", "disable", "probe"])
    p_accounts.add_argument("id", nargs="?",
                            help="Account id (or backend name for `add`)")
    p_accounts.add_argument("--label", help="Display label for `add`")

    sub.add_parser("savings", help="Show what you'd have paid on the hosted API")

    args = parser.parse_args(argv)

    if args.cmd in (None, "serve"):
        host = getattr(args, "host", os.environ.get("HOST", "127.0.0.1"))
        port = getattr(args, "port", int(os.environ.get("PORT", "8787")))
        server.serve(host, port)
        return 0

    if args.cmd == "chat":
        try:
            wrapper = run_blocking(args.prompt, model=args.model, system=args.system)
        except ClaudeError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        print(translate.wrapper_to_message(wrapper, args.model)["content"][0]["text"])
        return 0

    if args.cmd == "keys":
        return _cmd_keys(args)

    if args.cmd == "sessions":
        return _cmd_sessions(args)

    if args.cmd == "accounts":
        return _cmd_accounts(args)

    if args.cmd == "savings":
        return _cmd_savings()

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
