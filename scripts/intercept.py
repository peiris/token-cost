#!/usr/bin/env python3
"""Answer /token-cost without waking the model.

A slash command reaches the user only after a model has retyped its
output, and this report is a page of table -- measured at 1252 output
tokens, arriving a line at a time. None of it needs a model: report.py
has already produced the finished text before the model ever sees it.

So catch the invocation at prompt submit, print the report here, and stop
the turn. No tokens, and the page arrives whole instead of crawling.

This runs on every prompt in every project, so a prompt that is not this
command must cost no more than one interpreter start and a string
compare -- nothing heavier is imported until the text matches. Anything
unexpected exits quietly and lets the prompt through, where the slash
command still answers it the slow way; set TOKEN_COST_NO_INTERCEPT=1 to
always take that route.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# What the command's own frontmatter asks for, so the intercepted report
# and the slash command's are the same page.
BUDGET = 28000

# ESC[0m. Claude Code colours a stop message amber; this closes it again so
# the page reads as a page. Zero width, so nothing it prefixes moves.
PLAIN = "\x1b[0m"

# Both ways Claude Code writes this command's name: bare, and qualified by
# the plugin it came from.
NAMES = ("/token-cost:token-cost", "/token-cost")


def invocation(prompt: str):
    """The arguments this prompt passes to /token-cost, or None.

    Deliberately strict about the whole prompt, not just its opening: a
    message that merely mentions the command -- "/token-cost is slow, fix
    it" -- is a question for the model, and hijacking it would be worse
    than being slow.
    """
    text = (prompt or "").strip()
    for name in NAMES:
        if text == name:
            return []
        if text.startswith(name + " "):
            return text[len(name):].split()
    return None


def understood(args: list[str]) -> bool:
    """Every argument is one views.parse_args actually reads.

    Same words, read the same way, so an argument this says yes to is one
    the report can act on. Anything else belongs to the model.
    """
    import views
    words = set(views.PERIODS) | {"ui", "tui"}
    return all(a.lower() in words
               or a.lower().startswith(("task", "session", "day", "date"))
               for a in args)


def render(cwd: str, args: list[str]) -> str:
    """report.py's own output, captured rather than printed."""
    import contextlib
    import io

    import report
    buffer = io.StringIO()
    argv = sys.argv
    sys.argv = ["report.py", "--cwd", cwd, "--budget", str(BUDGET), *args]
    try:
        with contextlib.redirect_stdout(buffer):
            report.main()
    finally:
        sys.argv = argv
    return buffer.getvalue().rstrip("\n")


def terminal() -> bool:
    """True when this session is drawn on a real terminal.

    Only there is PLAIN worth sending: a terminal reads the escape, while a
    frontend that renders Claude Code's output some other way might print it.
    report.py already looks for the terminal the page is about to land on --
    an ancestor of ours owns it, or nothing does.
    """
    import report
    return report.ancestor_columns() > 0


def main() -> int:
    if os.environ.get("TOKEN_COST_NO_INTERCEPT"):
        return 0
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    args = invocation(event.get("prompt", ""))
    if args is None:
        return 0

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        if not understood(args):
            return 0
        page = render(event.get("cwd") or os.getcwd(), args)
    except Exception:
        return 0  # let the slash command have it

    if not page:
        return 0
    # Claude Code paints a stop message amber and re-opens that colour at
    # the start of every line, so a report inherits it and ends up looking
    # like a warning about itself. Our text is emitted verbatim inside each
    # line, so each line can close the colour again. The leading newline is
    # for the line Claude Code prints above us: a frame sharing a row with
    # someone else's text is a broken frame.
    if terminal():
        page = "\n".join(PLAIN + line for line in page.split("\n"))
    print(json.dumps({"continue": False, "stopReason": "\n" + page}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
