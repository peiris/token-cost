#!/usr/bin/env python3
"""Render the project's token ledger for Claude Code.

Usage: report.py [--cwd PATH] [--budget N] [--width N]
                 [days|tasks|sessions|ui] [today|week|month]

What a view contains lives in views.py; this file only knows how to print the
chat overview and its explicit plain-text tables. `--budget` caps how many
characters that print may occupy -- see budget_notice for why a report ever
declines to draw itself. `--width` pins the overview to a column count
instead of working one out; so does TOKEN_COST_WIDTH in the environment.
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

# --------------------------------------------------------------------------
# how wide the report may be
# --------------------------------------------------------------------------

# Claude Code prints this report into a chat pane rather than a terminal, so
# stdout here is a pipe and the width never arrives the usual way. A fixed
# width is not an answer: assume more columns than the pane has and every
# framed line wraps onto the next row, which is how a box drawing becomes
# confetti. But the pane is as wide as the terminal Claude Code itself is
# attached to, and that process is one of our own ancestors -- so walk up to
# it and ask its TTY.
CHAT_RESERVE = 10    # message indent, the code block's padding, autowrap slack
MIN_WIDTH, MAX_WIDTH = 44, 100
FALLBACK_WIDTH = 74  # a plain 80-column terminal, less that same reserve

# The frames, the tab bar and the arithmetic that places every figure are the
# full-screen UI's, mirrored here so the two read as one thing seen twice.
# Each constant below names the tui.py one it tracks.
PAD_X = 2            # tui.PAD_X: columns of air inside a frame
GAP = 2              # columns between two panels sharing a row
STACK_BELOW = 68     # tui.draw_overview: narrower than this, panels stack
LABEL_INSET = 2      # tui.LABEL_INSET: the marker column, then a space
NAV_GAP = 3          # tui.NAV_GAP
STATS_MIN = 30       # tui.draw_overview: the Project panel's floor


def _process_tree() -> dict:
    """{pid: (ppid, tty name)} for every process, in one call to ps."""
    import subprocess
    try:
        done = subprocess.run(["ps", "-Ao", "pid=,ppid=,tty="],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {}
    tree = {}
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            tree[int(parts[0])] = (int(parts[1]), parts[2])
    return tree


def tty_columns(name: str) -> int:
    """How wide a terminal is that this process is not attached to.

    Opened O_NOCTTY, so asking never makes it ours.
    """
    if not name or "?" in name:
        return 0
    try:
        fd = os.open(f"/dev/{name}", os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError:
        return 0
    try:
        return os.get_terminal_size(fd).columns
    except OSError:
        return 0
    finally:
        os.close(fd)


def ancestor_columns() -> int:
    """The width of the nearest ancestor process that owns a terminal.

    Claude Code runs this script from a shell it spawned, so the terminal the
    report is about to be drawn on is a couple of hops up the parent chain.
    Nothing here is Claude-specific: run from a pipeline in an ordinary
    shell, the same walk finds the same terminal.
    """
    tree = _process_tree()
    seen = set()
    pid = os.getpid()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        parent, tty = tree.get(pid, (0, ""))
        if columns := tty_columns(tty):
            return columns
        pid = parent
    return 0


def report_width(explicit: int = 0) -> int:
    """How many columns the overview may draw into."""
    if explicit > 0:
        return max(MIN_WIDTH, explicit)
    override = (os.environ.get("TOKEN_COST_WIDTH") or "").strip()
    if override.isdigit() and int(override) > 0:
        return max(MIN_WIDTH, int(override))
    if sys.stdout.isatty():
        # Printed straight into a terminal: all of it is ours but the column
        # autowrap needs kept in hand.
        try:
            return max(MIN_WIDTH,
                       min(MAX_WIDTH, os.get_terminal_size(1).columns - 1))
        except OSError:
            pass
    env = (os.environ.get("COLUMNS") or "").strip()
    columns = int(env) if env.isdigit() else 0
    if columns <= 0:
        columns = ancestor_columns()
    if columns <= 0:
        return FALLBACK_WIDTH
    return max(MIN_WIDTH, min(MAX_WIDTH, columns - CHAT_RESERVE))


# --------------------------------------------------------------------------
# the shapes the UI draws
# --------------------------------------------------------------------------

def version() -> str:
    """The installed plugin version for the chat overview masthead."""
    try:
        with open(PLUGIN_ROOT / ".claude-plugin" / "plugin.json",
                  encoding="utf-8") as fh:
            return "v" + json.load(fh)["version"]
    except (OSError, ValueError, KeyError):
        return ""


def fit(text: str, width: int) -> str:
    """tui.fit: trim to width, marking the cut so a clipped label never
    reads as complete."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[:width - 1] + "…" if width > 1 else "…"


def caps(text: str) -> str:
    """tui.caps: capitalise each word, leaving the rest of it alone."""
    return " ".join(w[:1].upper() + w[1:] if w else w for w in text.split(" "))


def centre(text: str, width: int) -> str:
    """tui.centred, as a string: the run placed, then padded out."""
    at = max(0, (width - len(text)) // 2)
    return (" " * at + fit(text, width - at)).ljust(width)


def frame(title: str, rows: list[str], width: int) -> list[str]:
    """A titled panel in the shape tui.panel draws: rounded corners, the
    title set into the top edge, two columns of air each side, and the blank
    line top and bottom that tui.inside gives every box with room for it."""
    width = max(2 * PAD_X + 4, width)
    inner = width - 2 - 2 * PAD_X
    head = f"╭─ {fit(caps(title), width - 6)} " if title else "╭"
    pad = " " * PAD_X
    lines = [head + "─" * max(0, width - len(head) - 1) + "╮"]
    lines += [f"│{pad}{fit(row, inner):<{inner}}{pad}│"
              for row in ["", *rows, ""]]
    lines.append("╰" + "─" * (width - 2) + "╯")
    return lines


def side_by_side(left: list[str], right: list[str]) -> list[str]:
    """Two frames on one row, the shorter running out under the taller.

    The left column keeps its width once it closes, because the frame beside
    it still has to start in the same place. Past the right-hand frame there
    is nothing to hold a column open for, so the line ends there rather than
    trailing whitespace off the edge of the panel.
    """
    height = max(len(left), len(right))
    left = left + [" " * len(left[0])] * (height - len(left))
    right = right + [""] * (height - len(right))
    return [(a + " " * GAP + b).rstrip() for a, b in zip(left, right)]


def nav_row(width: int) -> str:
    """The tab bar with Overview marked, laid out the way tui.draw_nav lays
    it: each name in its own gutter -- the marker column, then a space -- so
    the row keeps one rhythm whichever tab is selected.

    The qualifier goes before the spacing does, and the spacing before the
    row does: "Week" in its place tells you more than one name and a counter.
    """
    labels = [label for label, _, _ in views.TABS]
    short = [label.replace("This ", "") for label in labels]

    def span(names, gap):
        return sum(LABEL_INSET + len(n) for n in names) + gap * (len(names) - 1)

    for names, gap in ((labels, NAV_GAP), (short, NAV_GAP),
                       (short, NAV_GAP - 1), (short, 1)):
        if span(names, gap) <= width:
            cells = [("▌ " if i == 0 else "  ") + name
                     for i, name in enumerate(names)]
            return (" " * gap).join(cells).ljust(width)
    return centre(f"‹ {labels[0]}  1/{len(labels)} ›", width)


def chrome(width: int, project: str) -> list[str]:
    """The masthead and the tab bar, as tui.draw_chrome draws them.

    One piece of chrome rather than two stacked boxes: the frames share a
    rule, and the shared row's ends become tees. Two lines a row apart read
    as a gap between them.
    """
    inner = width - 2 - 2 * PAD_X
    pad = " " * PAD_X
    mark = "▂▄▆█  Claude Token Cost"
    stamp = version()
    tag = f" {stamp}" if stamp else ""
    # Shed the trailing detail rather than run into the frame: the project
    # goes first, then the version, and the name always survives.
    title = next((text for text in (mark + tag + f"  ·  {project}", mark + tag,
                                    mark) if len(text) <= inner), mark)
    lines = frame("", [centre(title, inner)], width)[:-1]   # keep it open
    lines.append("├" + "─" * (width - 2) + "┤")
    lines.append(f"│{pad}{nav_row(inner)}{pad}│")
    lines.append("╰" + "─" * (width - 2) + "╯")
    return lines


def figure_row(left: str, tokens: str, cost: str, width: int,
               token_w: int, cost_w: int, gap: int) -> str:
    """One row: `left`, then the two figures hard against the right edge.

    tui.draw_overview places both by column rather than by offset from the
    text, so a figure never moves because the one beside it got shorter.
    `gap` is the least air kept between the label and them.
    """
    tail = f"{tokens:>{token_w}}   {cost:>{cost_w}}"
    room = max(0, width - len(tail) - gap)
    return f"{fit(left, room):<{room}}{' ' * gap}{tail}"


def bar(value: float, peak: float, width: int):
    """tui.bar: (filled, remainder). Any non-zero value keeps a cell, so a
    day that cost something never renders as nothing at all."""
    if peak <= 0 or width <= 0:
        return "", ""
    cells = min(width, max(1, round(value / peak * width))) if value > 0 else 0
    return "▄" * cells, "┈" * (width - cells)


def model_rows(models: list[dict], width: int) -> list[str]:
    """The model split: name, task count, tokens and spend.

    Which of those a row can hold is views.model_figures' call, so this
    panel and the UI's shed the same things at the same widths.
    """
    name_w, counts, tokens, costs = views.model_figures(models, width)
    token_w = max(len(t) for t in tokens)
    cost_w = max(len(c) for c in costs)
    return [
        figure_row(f"{fit(b['key'], name_w):<{name_w}}{tally}",
                   token, cost, width, token_w, cost_w, GAP)
        for b, tally, token, cost in zip(models, counts, tokens, costs)
    ]


def day_rows(days: list[dict], width: int) -> list[str]:
    """Spend per day as bars, the figures hard against the right edge."""
    costs = [ledger.fmt_usd(b["cost"], b["cost_known"]) for b in days]
    counts = [ledger.fmt_tokens(views.total_tokens(b)) for b in days]
    cost_w = max(len(c) for c in costs)
    count_w = max(len(c) for c in counts)
    # date, its space, the two edges and the air before the figures take
    # thirteen cells together -- the same reservation tui.draw_overview makes.
    room = max(6, width - 13 - cost_w - count_w)
    peak = max(b["cost"] for b in days) or 1.0
    rows = []
    for b, count, cost in zip(days, counts, costs):
        filled, rest = bar(b["cost"], peak, room)
        rows.append(figure_row(f"{b['key'][5:]} ▕{filled}{rest}▏",
                               count, cost, width, count_w, cost_w, GAP))
    return rows


def task_rows(tasks: list[dict], width: int) -> list[str]:
    """The priciest tasks, named the way the UI names them."""
    costs = [ledger.fmt_usd(b["cost"], b["cost_known"]) for b in tasks]
    counts = [ledger.fmt_tokens(views.total_tokens(b)) for b in tasks]
    cost_w = max(len(c) for c in costs)
    count_w = max(len(c) for c in counts)
    room = max(10, width - cost_w - count_w - 6)
    return [
        figure_row(views.label_of(b, room, views.UNKNOWN_LONG),
                   count, cost, width, count_w, cost_w, 3)
        for b, count, cost in zip(tasks, counts, costs)
    ]


def wrap(text: str, width: int) -> list[str]:
    """Break a sentence on spaces so a note can't overrun the frames.

    A line already inside the width comes back untouched, spacing and all:
    the header above a table lines its fields up with runs of spaces, and
    rebuilding it word by word would close them.
    """
    if len(text) <= width:
        return [text] if text else []
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    return lines + [line] if line else lines


def overview(rows: list[dict], project: str, width: int = 0) -> str:
    """Render the default Claude Code report in the full-screen UI's shape.

    Curses cannot attach to Claude Code's captured shell, but its output is
    still monospaced and its pane still has a width we can find out. So this
    is the static, scrollable counterpart to the UI's Overview tab -- same
    chrome, same panels, same rows, sized to whatever room it is given --
    rather than a second, unrelated presentation of the ledger.
    """
    width = report_width(width)
    inner = width - 2 - 2 * PAD_X
    tasks = views.count_tasks(rows)
    total_cost = sum(row.get("cost_usd") or 0.0 for row in rows)
    cost_known = all(row.get("cost_usd") is not None for row in rows)
    total_tokens = sum(
        sum(row.get(key, 0) for key in ledger.TOKEN_KEYS) for row in rows)
    days = [bucket for bucket in views.day_buckets(rows)
            if bucket["key"] != "unknown"]
    span = (f"{days[0]['key']} → {days[-1]['key']}" if days
            else "no dated rows")

    lines = chrome(width, project)
    lines.append("")

    project_rows = [
        f"{tasks:,} Tasks",
        span,
        f"{ledger.fmt_tokens(total_tokens)} Tokens",
        ledger.fmt_usd(total_cost, cost_known),
    ]
    models = views.model_buckets(rows)
    # Side by side needs room for both; below a certain width they stack, so
    # a narrow pane loses the layout rather than the content.
    stacked = width < STACK_BELOW
    stats_w = width if stacked else max(STATS_MIN, width // 3)
    models_w = width if stacked else width - stats_w - GAP
    built = model_rows(models, models_w - 2 - 2 * PAD_X) if models else []
    if stacked:
        lines += frame("Project", project_rows, stats_w)
        lines += frame("Models", built, models_w)
    else:
        # Each box keeps its own height and they hang from the same line, the
        # way two panels of different depths sit beside each other in the UI.
        lines += side_by_side(frame("Project", project_rows, stats_w),
                              frame("Models", built, models_w))

    if days:
        lines.append("")
        lines += frame("Cost Per Day", day_rows(days, inner), width)

    expensive = sorted(views.task_buckets(rows),
                       key=lambda bucket: -bucket["cost"])[:5]
    if expensive:
        lines.append("")
        lines += frame("Most Expensive Tasks", task_rows(expensive, inner),
                       width)

    lines.append("")
    for note in ("Estimated from published API rates; subscription plans are"
                 " not billed per token.",
                 "Run /token-cost days for the day table, or /token-cost"
                 " tasks for every task."):
        lines += wrap(note, width)
    return "\n".join(lines)


def render(view, width: int = 0) -> str:
    """A view's table, narrowed to `width` when it doesn't already fit.

    The same fitting the UI does, from the same place: the prose column
    gives space back first, then whole columns go in views.DROP_ORDER, and
    the header still names the ones that survived. Rows are never dropped to
    make a table fit -- a table you can see is short is better than one that
    quietly isn't all there.

    A table that already fits is left at its natural size. The UI stretches
    those out to its right edge, but a chat message is not a pane with an
    edge to reach, and padding a six-column table across ninety would only
    put air between figures that belong side by side.
    """
    table = views.rendered(view)
    cols, widths, total = table.fit(width or MAX_WIDTH * 4, grow=False)
    headers = [c[0] for c in cols]
    aligns = [c[1] for c in cols]

    def line(cells):
        return (" " * views.GAP).join(
            fit(cell, w).rjust(w) if a == ">" else fit(cell, w).ljust(w)
            for cell, w, a in zip(cells, widths, aligns)
        ).rstrip()

    rows = ([table.cells[header][i] for header in headers]
            for i in range(table.count))
    rule = "─" * (sum(widths) + views.GAP * (len(widths) - 1))
    return "\n".join([line(headers)] + [line(row) for row in rows]
                     + [rule, line(total)])


def summary_only(view, width: int = 0) -> str:
    """Headers and footer, for when the rows themselves cannot be printed.

    The header row comes along because a line of numbers with nothing naming
    the columns is a puzzle, not a summary.
    """
    lines = render(view, width).split("\n")
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
    width = 0
    if "--width" in args:
        i = args.index("--width")
        width = int(args[i + 1])
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
    # Resolved, because a relative --cwd has no name of its own to show.
    project = Path(cwd).resolve().name

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
    # Every table gets the same width the overview draws into: the tables
    # land in the same pane and wrap in the same way when they overrun it.
    width = report_width(width)
    table = render(view, width)

    if imported:
        print(f"Imported {imported} earlier session(s) from transcripts on disk.")
        print()
    if show_overview:
        output = overview(rows, project, width)
        if budget and len(output) > budget:
            for line in wrap(f"Project: {project}    {view.tasks} tasks"
                             f"    {view.subtitle}", width):
                print(line)
            print()
            print(summary_only(view, width))
            print()
            print(budget_notice(view, output, "days", "overview"))
            return 0
        print(output)
        return 0
    for line in wrap(f"Project: {project}    {view.tasks} tasks   "
                     f" {view.subtitle}", width):
        print(line)
    print()
    if budget and len(table) > budget:
        print(summary_only(view, width))
        print()
        print(budget_notice(view, table))
        return 0
    print(table)
    print()
    for note in ("Estimated from published API rates; subscription plans are"
                 " not billed per token.", view.hint):
        for line in wrap(note, width):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
