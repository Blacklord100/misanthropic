"""`breakthrough` command.

  breakthrough serve [--host H --port P]        run the Anthropic-compatible server
  breakthrough chat "..." [--model --system]    one-off completion (quick test)
  breakthrough keys add|remove|list [KEY]       manage approved keys (= sessions)
  breakthrough sessions list|forget [KEY]       inspect / reset key->session links

With no subcommand, `breakthrough` starts the server.
"""

import argparse
import os
import sys

from . import __version__, server, sessions, translate
from .claude import ClaudeError, DEFAULT_MODEL, run_blocking


def _cmd_keys(args):
    if args.action == "add":
        if not args.key:
            print("usage: breakthrough keys add <key>", file=sys.stderr)
            return 1
        sessions.add_key(args.key)
        print(f"approved key added: {args.key}")
    elif args.action == "remove":
        if not args.key:
            print("usage: breakthrough keys remove <key>", file=sys.stderr)
            return 1
        sessions.remove_key(args.key)
        print(f"key removed (and its session forgotten): {args.key}")
    elif args.action == "list":
        keys = sorted(sessions.approved_keys())
        if not keys:
            print("no approved keys. add one with: breakthrough keys add <key>")
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
            print("usage: breakthrough sessions forget <key>", file=sys.stderr)
            return 1
        sessions.forget_session(args.key)
        print(f"session forgotten; next request under '{args.key}' starts fresh.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="breakthrough",
        description="Anthropic-API-compatible server backed by your local Claude Code CLI. No API key.",
    )
    parser.add_argument("--version", action="version", version=f"breakthrough {__version__}")
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

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
