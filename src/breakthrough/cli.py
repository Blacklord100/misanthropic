"""`breakthrough` command.

  breakthrough serve [--host H --port P]   run the Anthropic-compatible server
  breakthrough chat "..." [--model --system]   one-off completion (quick test)

With no subcommand, `breakthrough` starts the server.
"""

import argparse
import os
import sys

from . import __version__, server, translate
from .claude import ClaudeError, DEFAULT_MODEL, run_blocking


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

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
