#!/usr/bin/env python3
"""Render the project's token ledger as a table.

Usage: report.py [--cwd PATH] [--budget N] [days|tasks|sessions|ui] [today|week|month]

What a view contains lives in views.py; this file only knows how to print one
as plain text. `--budget` caps how many characters that print may occupy --
see budget_notice for why a report ever declines to draw itself.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger  # noqa: E402
import views  # noqa: E402

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent


def render(columns, buckets, overrides=None) -> str:
    """`overrides` replaces a TOTAL cell by header name."""
    overrides = overrides or {}
    headers = [c[0] for c in columns]
    aligns = [c[1] for c in columns]
    rows = [[str(c[2](b)) for c in columns] for b in buckets]
    total = [
        str(overrides[c[0]]) if c[0] in overrides
        else (str(c[3](buckets)) if c[3] else "")
        for c in columns
    ]
    total[0] = "TOTAL"

    grid = [headers] + rows + [total]
    widths = [max(len(row[i]) for row in grid) for i in range(len(columns))]

    def line(cells):
        return "  ".join(
            cell.rjust(w) if a == ">" else cell.ljust(w)
            for cell, w, a in zip(cells, widths, aligns)
        ).rstrip()

    rule = "─" * (sum(widths) + 2 * (len(widths) - 1))
    return "\n".join([line(headers)] + [line(r) for r in rows] + [rule, line(total)])


def summary_only(view) -> str:
    """Headers and footer, for when the rows themselves cannot be printed.

    The header row comes along because a line of numbers with nothing naming
    the columns is a puzzle, not a summary.
    """
    lines = render(view.columns, view.buckets, view.overrides).split("\n")
    return "\n".join([lines[0], lines[-2], lines[-1]])


def budget_notice(view, table: str) -> str:
    """Why a table isn't here, and where to read it instead.

    Inline shell output reaches the conversation through the Bash tool, which
    carries about 30k characters; past that the model is handed a file path
    and a preview rather than a table. Printing 90k anyway doesn't produce a
    long table, it produces no table. So say so, keep the totals -- which are
    the part that still fits -- and point at the UI, which has no ceiling.
    """
    return "\n".join([
        f"{len(view.buckets):,} rows is {len(table):,} characters — past the"
        " ~30,000 a conversation",
        "can carry, so the table would arrive as a file preview rather than rows.",
        "",
        "  token-cost                 the full list, scrollable",
        "  /token-cost tasks week     the last 7 days, in chat",
    ])


def ui_command() -> str:
    """How to start the UI on this machine, preferring the short form."""
    import install_shim
    directory = install_shim.target_dir()
    shim = directory / "token-cost"
    if shim.is_file() and install_shim.on_path(directory):
        return "token-cost"
    return f"python3 {PLUGIN_ROOT / 'scripts' / 'tui.py'}"


def open_window(command: str, title: str) -> str | None:
    """Open the UI in a new terminal window. Returns the app used, or None.

    A slash command has no terminal to hand a full-screen app, but the
    desktop does. Better to open one than to print a command and make
    someone else do the work.
    """
    import shutil
    import subprocess
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return None
    app = "iTerm" if os.environ.get("TERM_PROGRAM") == "iTerm.app" else "Terminal"

    def quote(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"')

    if app == "Terminal":
        # Name the window after the project, so several of these are
        # telling apart at a glance.
        script = (f'tell application "Terminal"\n'
                  f'  set theTab to do script "{quote(command)}"\n'
                  f'  try\n'
                  f'    set custom title of theTab to "{quote(title)}"\n'
                  f'  end try\n'
                  f'  activate\n'
                  f'end tell')
    else:
        script = (f'tell application "iTerm" to create window with default '
                  f'profile command "{quote(command)}"\n'
                  f'tell application "iTerm" to activate')
    try:
        done = subprocess.run(["osascript", "-e", script],
                              capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return app if done.returncode == 0 else None


def launch_block(cwd: str) -> str:
    """What `/token-cost ui` prints: the UI, opened if that's possible.

    Whatever this says gets read by someone whose data is now in another
    window. Say that first and plainly -- there is nothing to read here.
    """
    import shlex
    project = Path(cwd).name
    command = ui_command()
    # A new window opens in the home directory, which is somebody else's
    # ledger. Name the project explicitly rather than inheriting a cwd.
    opened = open_window(f"{command} --cwd {shlex.quote(cwd)}",
                         f"token-cost · {project}")
    if opened:
        return "\n".join([
            f"A new {opened} window is now open with the token-cost UI for"
            f" {project}.",
            f"Switch to that window — your usage data is there, not here.",
            "",
            "  Tabs   Overview · Today · This Week · This Month · Tasks · Sessions",
            "  Keys   ←/→ or 1-6 change tab · ↑/↓ scroll · r refresh · q quit",
            "",
            f"Reopen it any time by running {command} in any terminal.",
        ])
    return "\n".join([
        f"The token-cost UI shows {project}'s usage across tabs for Overview,",
        "Today, This Week, This Month, Tasks and Sessions.",
        "",
        "It needs a terminal window of its own, and one could not be opened",
        "for you here. Run this in any terminal:",
        "",
        f"    {command}",
    ])


def main() -> int:
    args = sys.argv[1:]
    cwd = str(Path.cwd())
    budget = 0
    if "--cwd" in args:
        i = args.index("--cwd")
        cwd = args[i + 1]
        del args[i:i + 2]
    if "--budget" in args:
        i = args.index("--budget")
        budget = int(args[i + 1])
        del args[i:i + 2]

    if any(a.lower() in ("ui", "tui") for a in args):
        print(launch_block(cwd))
        return 0

    mode, period = views.parse_args(args)

    # Pull in any sessions that predate the plugin, or that another machine
    # recorded. Near-free once a project is synced, and it means the table is
    # never mysteriously empty on a project with history sitting on disk.
    imported = 0
    try:
        import record
        imported = record.sync(cwd)["imported"]
    except Exception:
        pass  # a sync failure must never stop the report rendering

    rows = ledger.read_ledger(ledger.ledger_path(cwd))
    project = Path(cwd).name

    if not rows:
        print(f"No token usage recorded yet for {project}.")
        print()
        print("Usage is recorded automatically as tasks complete —"
              " finish a task and run /token-cost again.")
        return 0

    scope = ""
    if period:
        rows, scope = views.in_window(rows, period)
        if not rows:
            print(f"No token usage recorded for {project} in that window ({scope}).")
            return 0

    view = views.build(rows, mode, scope)
    table = render(view.columns, view.buckets, view.overrides)

    if imported:
        print(f"Imported {imported} earlier session(s) from transcripts on disk.")
        print()
    print(f"Project: {project}    {view.tasks} tasks    {view.subtitle}")
    print()
    if budget and len(table) > budget:
        print(summary_only(view))
        print()
        print(budget_notice(view, table))
        return 0
    print(table)
    print()
    print("Estimated from published API rates; subscription plans are not billed per token.")
    if view.hint:
        print(view.hint)
    return 0


if __name__ == "__main__":
    sys.exit(main())
