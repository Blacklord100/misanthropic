"""`breakthrough` command — collect inputs, generate the brief, print it.

Pure terminal app. No web server, no API key. Generation runs through the
user's local Claude Code CLI (see claude.py).
"""

import argparse
import sys

from . import __version__
from .claude import ClaudeError, extract_json, run_local_claude, DEFAULT_MODEL
from .prompts import SYSTEM, build_user_prompt

# ---- tiny ANSI helpers (no dependency on a color lib) -----------------------

_USE_COLOR = sys.stdout.isatty()


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def bold(t):
    return _c("1", t)


def dim(t):
    return _c("2", t)


def cyan(t):
    return _c("36", t)


def yellow(t):
    return _c("33", t)


def green(t):
    return _c("32", t)


def red(t):
    return _c("31", t)


def _prompt(label, required=False, default=None):
    """Read a single line from the user. Re-asks if required and empty."""
    suffix = f" {dim('[' + default + ']')}" if default else ""
    while True:
        try:
            val = input(f"{cyan(label)}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(130)
        if not val and default is not None:
            return default
        if val or not required:
            return val
        print(red("  (required)"))


def _score_color(score):
    if score >= 7:
        return green
    if score >= 4:
        return yellow
    return red


def render_brief(data, model):
    """Pretty-print the brief JSON the way the web UI lays it out."""
    angle = data.get("angle", {}) or {}
    message = data.get("message", {}) or {}
    check = data.get("selfCheck", {}) or {}

    print()
    print(bold("━━ THE ANGLE ━━"))
    print(f"  {bold('What they care about:')} {angle.get('whatTheyCareAbout', '—')}")
    objections = angle.get("likelyObjections") or []
    if objections:
        print(f"  {bold('Likely objections:')}")
        for o in objections:
            print(f"    • {o}")
    print(f"  {bold('Hook:')} {angle.get('hook', '—')}")

    print()
    print(bold("━━ THE DRAFT ━━"))
    channel = message.get("channel", "—")
    print(f"  {dim('Channel:')} {channel}")
    subject = message.get("subject") or ""
    if subject:
        print(f"  {dim('Subject:')} {subject}")
    print()
    body = message.get("body", "")
    for line in body.split("\n"):
        print(f"  {line}")

    timing = data.get("timing")
    if timing:
        print()
        print(bold("━━ WHEN TO SEND ━━"))
        print(f"  {timing}")

    print()
    print(bold("━━ WOULD A VC REPLY? ━━"))
    score = check.get("wouldReplyScore")
    if isinstance(score, int):
        col = _score_color(score)
        print(f"  {col(bold(str(score) + '/10'))}")
    works = check.get("whatWorks") or []
    if works:
        print(f"  {green('What works:')}")
        for w in works:
            print(f"    • {w}")
    flags = check.get("redFlags") or []
    if flags:
        print(f"  {red('Red flags:')}")
        for f in flags:
            print(f"    • {f}")
    verdict = check.get("verdict")
    if verdict:
        print(f"  {bold('Verdict:')} {verdict}")
    print()
    print(dim(f"  generated via local Claude Code · model: {model}"))
    print()


def _collect_interactive():
    print(bold("\nBreakthrough — one honest first-touch to one investor.\n"))
    vc = {
        "name": _prompt("Investor name", required=True),
        "firm": _prompt("Firm"),
        "notes": _prompt("What you know about them (thesis, deals, posts)"),
    }
    startup = {
        "oneLiner": _prompt("Your startup one-liner", required=True),
        "stage": _prompt("Stage"),
        "traction": _prompt("Traction / proof"),
        "ask": _prompt("The ask"),
    }
    tone = _prompt("Tone (warm/direct/corporate)", default="direct")
    return vc, startup, tone


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="breakthrough",
        description="Founder-to-VC outreach via your local Claude Code CLI. No API key.",
    )
    parser.add_argument("--version", action="version", version=f"breakthrough {__version__}")
    parser.add_argument("--investor", help="Target investor name")
    parser.add_argument("--firm", help="Investor's firm")
    parser.add_argument("--notes", help="What you know about the investor")
    parser.add_argument("--one-liner", dest="one_liner", help="Your startup one-liner")
    parser.add_argument("--stage", help="Startup stage")
    parser.add_argument("--traction", help="Traction / proof")
    parser.add_argument("--ask", help="The ask")
    parser.add_argument("--tone", default="direct", choices=["warm", "direct", "corporate"])
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model alias or id (default: sonnet)")
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of a formatted brief")
    args = parser.parse_args(argv)

    # Non-interactive if the two required fields are supplied via flags.
    if args.investor and args.one_liner:
        vc = {"name": args.investor, "firm": args.firm, "notes": args.notes}
        startup = {
            "oneLiner": args.one_liner,
            "stage": args.stage,
            "traction": args.traction,
            "ask": args.ask,
        }
        tone = args.tone
    else:
        if not sys.stdin.isatty():
            parser.error("Need --investor and --one-liner when not running interactively.")
        vc, startup, tone = _collect_interactive()

    print(dim("\nGenerating via your local Claude…"), file=sys.stderr)
    try:
        result_text = run_local_claude(
            SYSTEM,
            build_user_prompt(vc, startup, tone),
            model=args.model,
        )
    except ClaudeError as e:
        print(red(f"\nError: {e}"), file=sys.stderr)
        return 1

    data = extract_json(result_text)
    if data is None:
        print(red("\nLocal Claude did not return valid JSON. Try again."), file=sys.stderr)
        return 1

    if args.json:
        import json
        print(json.dumps(data, indent=2))
    else:
        render_brief(data, args.model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
