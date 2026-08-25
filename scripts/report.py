#!/usr/bin/env python3
"""Render the project's token ledger for Claude Code.

Usage: report.py [--cwd PATH] [--budget N] [days|tasks|sessions|ui] [today|week|month]

What a view contains lives in views.py; this file only knows how to print the
chat overview and its explicit plain-text tables. `--budget` caps how many
characters that print may occupy -- see budget_notice for why a report ever
declines to draw itself.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger  # noqa: E402
import views  # noqa: E402

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent

# Claude Code prints this report in a code block rather than a real terminal.
# Keep the overview comfortably inside that pane while preserving the same
# hierarchy as the full-screen UI: masthead, key figures, the model split,
# spend over time, and the tasks worth investigating.
OVERVIEW_WIDTH = 94


def version() -> str:
    """The installed plugin version for the chat overview masthead."""
    try:
        with open(PLUGIN_ROOT / ".claude-plugin" / "plugin.json",
                  encoding="utf-8") as fh:
            return "v" + json.load(fh)["version"]
    except (OSError, ValueError, KeyError):
        return ""


def frame(title: str, rows: list[str], width: int = OVERVIEW_WIDTH) -> list[str]:
    """A chat-safe titled panel, mirroring the full-screen UI's frames."""
    width = max(8, width)
    inner = width - 4
    title = title[:max(0, inner - 2)]
    head = f"╭─ {title} "
    lines = [head + "─" * max(0, width - len(head) - 1) + "╮"]
    lines.extend(f"│ {line[:inner].ljust(inner)} │" for line in rows)
    lines.append("╰" + "─" * (width - 2) + "╯")
    return lines


def paired_frames(left_title: str, left_rows: list[str], right_title: str,
                  right_rows: list[str]) -> list[str]:
    """Two panels on one line, as long as the report's fixed width allows."""
    left_width = 31
    right_width = OVERVIEW_WIDTH - left_width - 2
    content_height = max(len(left_rows), len(right_rows))
    left = frame(left_title, left_rows + [""] * (content_height - len(left_rows)),
                 left_width)
    right = frame(right_title,
                  right_rows + [""] * (content_height - len(right_rows)),
                  right_width)
    return [a + "  " + b for a, b in zip(left, right)]


def overview(rows: list[dict], project: str) -> str:
    """Render the default Claude Code report in the full-screen UI's shape.

    Curses cannot attach to Claude Code's captured shell, but its output is
    still monospaced. This is the static, scrollable counterpart to the UI's
    Overview tab rather than a second, unrelated presentation of the ledger.
    """
    tasks = views.count_tasks(rows)
    total_cost = sum(row.get("cost_usd") or 0.0 for row in rows)
    cost_known = all(row.get("cost_usd") is not None for row in rows)
    total_tokens = sum(
        sum(row.get(key, 0) for key in ledger.TOKEN_KEYS) for row in rows)
    days = [bucket for bucket in views.day_buckets(rows)
            if bucket["key"] != "unknown"]
    span = (f"{days[0]['key']} → {days[-1]['key']}" if days
            else "no dated rows")

    title_parts = ["▂▄▆█", "Claude Token Cost"]
    if current := version():
        title_parts.append(current)
    title_parts.append("·")
    title_parts.append(project)
    masthead = "  ".join(title_parts)
    nav = "▌ Overview     Today     This Week     This Month     All Tasks     Sessions"
    lines = frame(masthead, [nav])
    lines.append("")

    project_rows = [
        f"{tasks:,} Tasks",
        span,
        f"{ledger.fmt_tokens(total_tokens)} Tokens",
        ledger.fmt_usd(total_cost, cost_known),
    ]
    models = views.model_buckets(rows)
    model_costs = [ledger.fmt_usd(bucket["cost"], bucket["cost_known"])
                   for bucket in models]
    model_tokens = [f"{ledger.fmt_tokens(views.total_tokens(bucket))} Tokens"
                    for bucket in models]
    model_width = min(18, max([5] + [len(bucket["key"]) for bucket in models]))
    task_counts = [f"{bucket['tasks']:,} Tasks" for bucket in models]
    task_width = max([5] + [len(count) for count in task_counts])
    tokens_width = max([6] + [len(tokens) for tokens in model_tokens])
    cost_width = max([1] + [len(cost) for cost in model_costs])
    model_rows = [
        f"{bucket['key'][:model_width]:<{model_width}}  "
        f"{count:>{task_width}}  {tokens:>{tokens_width}}  {cost:>{cost_width}}"
        for bucket, count, tokens, cost in zip(
            models, task_counts, model_tokens, model_costs)
    ]
    paired_model_width = OVERVIEW_WIDTH - 31 - 2 - 4
    if all(len(row) <= paired_model_width for row in model_rows):
        lines.extend(paired_frames("Project", project_rows, "Models", model_rows))
    else:
        # Preserve unusually large figures instead of slicing their dollars
        # off at the narrow right-hand panel's edge.
        lines.extend(frame("Project", project_rows))
        lines.append("")
        lines.extend(frame("Models", model_rows))

    if days:
        lines.append("")
        day_costs = [ledger.fmt_usd(bucket["cost"], bucket["cost_known"])
                     for bucket in days]
        day_tokens = [ledger.fmt_tokens(views.total_tokens(bucket))
                      for bucket in days]
        cost_width = max(len(cost) for cost in day_costs)
        tokens_width = max(len(tokens) for tokens in day_tokens)
        # date, a space plus left edge, right edge, and the three spaces
        # around the trailing figures take eleven cells together.
        bar_width = OVERVIEW_WIDTH - 4 - 11 - tokens_width - cost_width
        peak = max(bucket["cost"] for bucket in days) or 1.0
        chart_rows = []
        for bucket, tokens, cost in zip(days, day_tokens, day_costs):
            filled = (max(1, round(bucket["cost"] / peak * bar_width))
                      if bucket["cost"] else 0)
            chart_rows.append(
                f"{bucket['key'][5:]} ▕{'▄' * filled}{'┈' * (bar_width - filled)}▏"
                f" {tokens:>{tokens_width}} {cost:>{cost_width}}")
        lines.extend(frame("Cost Per Day", chart_rows))

    expensive = sorted(views.task_buckets(rows),
                       key=lambda bucket: -bucket["cost"])[:5]
    if expensive:
        lines.append("")
        task_costs = [ledger.fmt_usd(bucket["cost"], bucket["cost_known"])
                      for bucket in expensive]
        task_tokens = [ledger.fmt_tokens(views.total_tokens(bucket))
                       for bucket in expensive]
        cost_width = max(len(cost) for cost in task_costs)
        tokens_width = max(len(tokens) for tokens in task_tokens)
        label_width = OVERVIEW_WIDTH - 4 - tokens_width - cost_width - 2
        task_rows = [
            f"{views.label_of(bucket, label_width, views.UNKNOWN_LONG):<{label_width}}"
            f" {tokens:>{tokens_width}} {cost:>{cost_width}}"
            for bucket, tokens, cost in zip(expensive, task_tokens, task_costs)
        ]
        lines.extend(frame("Most Expensive Tasks", task_rows))

    lines.extend([
        "",
        "Estimated from published API rates; subscription plans are not billed per token.",
        "Run /token-cost days for the day table, or /token-cost tasks for every task.",
    ])
    return "\n".join(lines)


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


def budget_notice(view, table: str, unit: str = "rows",
                  subject: str = "table") -> str:
    """Why a long chat result isn't here, and where to read it instead.

    Inline shell output reaches the conversation through the Bash tool, which
    carries about 30k characters; past that the model is handed a file path
    and a preview rather than a table. Printing 90k anyway doesn't produce a
    long table, it produces no table. So say so, keep the totals -- which are
    the part that still fits -- and point at the UI, which has no ceiling.
    """
    return "\n".join([
        f"{len(view.buckets):,} {unit} is {len(table):,} characters — past the"
        " ~30,000 a conversation",
        f"can carry, so the {subject} would arrive as a file preview rather than rows.",
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


def ensure_command() -> None:
    """Make sure `token-cost` is on PATH before anyone goes looking for it.

    The SessionStart hook is the normal path, but it can't be the only one:
    a plugin installed mid-session has no hooks registered until the next
    start, and hooks can be switched off entirely. Someone running a report
    is someone who wants this plugin, which is signal enough to install its
    command. Idempotent and quiet -- a no-op once the file is in place.
    """
    try:
        import install_shim
        install_shim.install()
    except Exception:
        pass  # never let this get between the user and their table


def main() -> int:
    ensure_command()
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

    # No arguments means the same Overview the full-screen UI opens on.
    # Explicit `days` retains the compact row-per-day table for people who
    # need to copy its individual counters into another tool.
    show_overview = not args
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
    if show_overview:
        output = overview(rows, project)
        if budget and len(output) > budget:
            print(f"Project: {project}    {view.tasks} tasks    {view.subtitle}")
            print()
            print(summary_only(view))
            print()
            print(budget_notice(view, output, "days", "overview"))
            return 0
        print(output)
        return 0
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
