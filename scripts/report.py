#!/usr/bin/env python3
"""Render the project's token ledger for Claude Code.

Usage: report.py [--cwd PATH] [--budget N] [--width N]
                 [days|tasks|sessions|ui|html] [today|week|month]

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
CHAT_ROWS = 50       # rows a table keeps when the whole list won't fit
MIN_WIDTH, MAX_WIDTH = 44, 100
FALLBACK_WIDTH = 74  # a plain 80-column terminal, less that same reserve

# The frames and the arithmetic that places every figure are the full-screen
# UI's, mirrored here so the two read as one thing seen twice. Each constant
# below names the tui.py one it tracks.
PAD_X = 2            # tui.PAD_X: columns of air inside a frame
GAP = 2              # columns between two panels sharing a row
STACK_BELOW = 68     # tui.draw_overview: narrower than this, panels stack
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


def stretch(box: list[str], height: int) -> list[str]:
    """A drawn frame grown to `height` rows, the air going under its rows.

    The blank line the frame keeps below its content is what the box grows
    by, so its rows stay where they were and its edges stay its own.
    """
    return (box if len(box) >= height
            else box[:-1] + [box[1]] * (height - len(box)) + box[-1:])


def side_by_side(left: list[str], right: list[str]) -> list[str]:
    """Two frames on one row, closing on the same line.

    A box beside a taller one grows to meet it rather than stopping short:
    the pair reads as one band across the page instead of a step down the
    middle of it. Which of the two is the taller depends on the ledger --
    four facts against however many models it holds -- so neither can be
    the one that sets the height.
    """
    height = max(len(left), len(right))
    return [a + " " * GAP + b
            for a, b in zip(stretch(left, height), stretch(right, height))]


def chrome(width: int, project: str) -> list[str]:
    """The masthead, which is as much chrome as a printed report has.

    The UI carries a tab bar under a shared rule here, because it has a
    keyboard and five other tabs to reach with it. A page in a chat has
    neither: the bar would be six words you cannot press, sitting above the
    one view you actually asked for. So the masthead closes, and each panel
    keeps saying what it holds in its own title.
    """
    inner = width - 2 - 2 * PAD_X
    mark = "▂▄▆█  Claude Token Cost"
    stamp = version()
    tag = f" {stamp}" if stamp else ""
    # Shed the trailing detail rather than run into the frame: the project
    # goes first, then the version, and the name always survives.
    title = next((text for text in (mark + tag + f"  ·  {project}", mark + tag,
                                    mark) if len(text) <= inner), mark)
    return frame("", [centre(title, inner)], width)


def figure_row(left: str, tokens: str, cost: str, width: int,
               token_w: int, cost_w: int, gap: int) -> str:
    """One row: `left`, then the two figures hard against the right edge.

    The columns come from views.figure_slots, which is where the UI gets
    them too, so a panel of a given width lays out identically in either.
    """
    label_w, token_x, cost_x = views.figure_slots(width, token_w, cost_w, gap)
    row = fit(left, label_w).ljust(token_x) + tokens.rjust(token_w)
    return (row.ljust(cost_x) + cost.rjust(cost_w))[:width]


# A model retypes this page before anyone sees it, and a box-drawing glyph
# costs about a token of its own: 2929 output tokens for the overview, 1234
# once the chart runs are ASCII. The frames stay -- long runs of ─ merge
# cheaply, and they are what makes this read as the UI seen twice. The bars
# were the whole bill, so here alone they speak ASCII. tui.py draws its own,
# on a terminal that charges nothing for a nicer glyph.
BAR_FILL, BAR_TRACK = "=", "-"
BAR_LEFT, BAR_RIGHT = "[", "]"


def bar(value: float, peak: float, width: int):
    """tui.bar: (filled, remainder). Any non-zero value keeps a cell, so a
    day that cost something never renders as nothing at all."""
    if peak <= 0 or width <= 0:
        return "", ""
    cells = min(width, max(1, round(value / peak * width))) if value > 0 else 0
    return BAR_FILL * cells, BAR_TRACK * (width - cells)


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
    label_w, _, _ = views.figure_slots(width, count_w, cost_w, GAP)
    # The date, the space after it, and the two edges around the bar. What
    # is left is the bar; a panel with no room for one shows the date and
    # its figures, which is still the row.
    room = max(0, label_w - 8)
    peak = max(b["cost"] for b in days) or 1.0
    rows = []
    for b, count, cost in zip(days, counts, costs):
        filled, rest = bar(b["cost"], peak, room)
        label = (f"{b['key'][5:]} {BAR_LEFT}{filled}{rest}{BAR_RIGHT}" if room
                 else b["key"][5:])
        rows.append(figure_row(label, count, cost, width, count_w, cost_w,
                               GAP))
    return rows


def task_rows(tasks: list[dict], width: int) -> list[str]:
    """The priciest tasks, named the way the UI names them."""
    costs = [ledger.fmt_usd(b["cost"], b["cost_known"]) for b in tasks]
    counts = [ledger.fmt_tokens(views.total_tokens(b)) for b in tasks]
    cost_w = max(len(c) for c in costs)
    count_w = max(len(c) for c in counts)
    label_w, _, _ = views.figure_slots(width, count_w, cost_w,
                                       views.FIGURE_GAP)
    return [
        figure_row(views.label_of(b, label_w, views.UNKNOWN_LONG),
                   count, cost, width, count_w, cost_w, views.FIGURE_GAP)
        for b, count, cost in zip(tasks, counts, costs)
    ]


# Punctuation that joins two items rather than starting one.
SEPARATORS = ("·", "—", "–", "-", "→")


def wrap(text: str, width: int) -> list[str]:
    """Break a sentence on spaces so a note can't overrun the frames.

    A line already inside the width comes back untouched, spacing and all:
    the header above a table lines its fields up with runs of spaces, and
    rebuilding it word by word would close them.
    """
    if len(text) <= width:
        return [text] if text else []
    # A separator belongs to the word in front of it. Broken on its own it
    # starts the next line -- "· q quit", "— past the" -- which reads as the
    # sentence beginning with punctuation rather than continuing.
    words = []
    for word in text.split():
        if words and word in SEPARATORS:
            words[-1] += " " + word
        else:
            words.append(word)
    lines, line = [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    return lines + [line] if line else lines


def pairs(rows: list[tuple[str, str]], width: int, indent: int = 2,
          gap: int = 5) -> list[str]:
    """Aligned label/description rows, wrapping under their own description.

    A hanging indent rather than a hard break: these are lists of things you
    can type set beside what they do, and a description that wraps back to
    the left margin reads as another thing you can type. Where even that
    doesn't fit, the label takes a line of its own rather than squeezing the
    description into a column two words wide.
    """
    label_w = max(len(label) for label, _ in rows)
    lead = indent + label_w + gap
    if width - lead < 16:
        out = []
        for label, text in rows:
            out.append(" " * indent + label)
            out += [" " * (indent + 2) + line
                    for line in wrap(text, max(8, width - indent - 2))]
        return out
    out = []
    for label, text in rows:
        wrapped = wrap(text, width - lead) or [""]
        out.append(" " * indent + label.ljust(label_w) + " " * gap
                   + wrapped[0])
        out += [" " * lead + more for more in wrapped[1:]]
    return out


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
        # Both boxes close on the same line, the way the UI's pair does.
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


def boxed(title: str, view, width: int, limit: int = 0) -> list[str]:
    """A view's table inside a titled frame, the way the UI boxes one."""
    inner = width - 2 - 2 * PAD_X
    return frame(title, render(view, inner, limit).split("\n"), width)


def session_rows(view, width: int) -> list[str]:
    """The sessions tab's opening panel, as tui.draw_sessions_summary draws
    it: how many, how much each carries on average, and the two outliers
    that recency ordering buries."""
    buckets = view.buckets
    count = len(buckets)
    known = all(bucket["cost_known"] for bucket in buckets)
    average = sum(bucket["cost"] for bucket in buckets) / count
    priciest = max(buckets, key=lambda bucket: bucket["cost"])
    longest = max(buckets, key=views.total_tokens)

    head = f"{count:,} Sessions"
    mid = f"{view.tasks / count:.1f} Tasks/session"
    tail = f"avg {ledger.fmt_usd(average, known)}/session"
    if width - len(tail) - 3 > 14 + len(mid):
        head = f"{head:<{max(0, width - len(tail) - 3 - len(mid))}}{mid}"
    rows = [f"{fit(head, max(0, width - len(tail) - 1)):<{max(0, width - len(tail))}}{tail}"]

    figures = [ledger.fmt_usd(priciest["cost"], priciest["cost_known"]),
               f"{ledger.fmt_tokens(views.total_tokens(longest))} Tokens"]
    fig_w = max(len(figure) for figure in figures)
    fig_x = max(0, width - fig_w)
    label_x = 9 + 12
    room = max(0, fig_x - label_x - 2)
    for tag, bucket, figure in (("Priciest", priciest, figures[0]),
                                ("Longest", longest, figures[1])):
        row = (f"{fit(tag, 9):<9}{fit(views.started(bucket), 12):<12}"
               f"{views.label_of(bucket, room, views.UNKNOWN_LONG)}")
        rows.append(f"{fit(row, fig_x):<{fig_x}}{figure.rjust(fig_w)}"[:width])
    return rows


def tab_report(rows: list[dict], project: str, mode: str, period: str | None,
               width: int, budget: int = 0) -> str:
    """One tab of the UI, printed.

    The UI leads every tab but the overview with a summary of what the table
    below can't say about itself, then gives the table the rest of the
    screen. A chat message has no rest-of-the-screen, but it has the same
    two things to show and the same shape to show them in -- so this is that
    tab, boxed the same way, sized to the pane instead of the terminal.

    `budget` is the ceiling on what a conversation can carry. Over it the
    table stops after CHAT_ROWS rows rather than losing all of them: the
    first fifty are the ones being asked for, and the table says on its
    last row how many it left behind.
    """
    # A tab is a mode and a period, so a narrowing the UI doesn't offer --
    # `/token-cost tasks week` -- still gets the two views a tab has.
    built = views.Tab(rows, mode, period, unknown="—")

    lines = chrome(width, project)
    if built.summary is not None:
        lines.append("")
        lines += boxed(built.summary_title, built.summary, width)
    elif built.main.buckets:
        lines.append("")
        lines += frame("Sessions",
                       session_rows(built.main, width - 2 - 2 * PAD_X), width)

    lines.append("")
    body = boxed(built.main_title, built.main, width)
    if budget and len("\n".join(lines + body)) > budget:
        full = len("\n".join(body))

        def page(limit: int) -> list[str]:
            """The tab with its table cut to `limit` rows, and why."""
            return (lines + boxed(built.main_title, built.main, width, limit)
                    + [""]
                    + budget_notice(built.main, full, budget, width,
                                    limit).split("\n"))

        # Fifty rows fit any pane this prints into, but --budget is a
        # ceiling and not a suggestion: halve the cap until the page it
        # produces is one the conversation can actually carry.
        limit = CHAT_ROWS
        out = page(limit)
        while limit > 1 and len("\n".join(out)) > budget:
            limit //= 2
            out = page(limit)
        return "\n".join(out)
    lines += body

    lines.append("")
    lines += wrap("Estimated from published API rates; subscription plans are"
                  " not billed per token.", width)
    lines += wrap(built.main.hint, width)
    return "\n".join(lines)


def render(view, width: int = 0, limit: int = 0) -> str:
    """A view's table, narrowed to `width` when it doesn't already fit.

    The same fitting the UI does, from the same place: the prose column
    gives space back first, then whole columns go in views.DROP_ORDER, and
    the header still names the ones that survived. Rows are never dropped to
    make a table fit -- a table you can see is short is better than one that
    quietly isn't all there.

    `limit` is the one thing that does drop rows, and only because the pane
    they are printed into has a ceiling of its own. A capped table says so
    on its last row, so it is never mistaken for the whole list.

    It fills the width it is given, the way the UI's tables do, because it
    is drawn where the UI draws its own: inside a frame, with an edge for
    the figures to sit against.
    """
    table = views.rendered(view)
    cols, widths, total = table.fit(width or MAX_WIDTH * 4)
    headers = [c[0] for c in cols]
    aligns = [c[1] for c in cols]

    def line(cells):
        return (" " * views.GAP).join(
            fit(cell, w).rjust(w) if a == ">" else fit(cell, w).ljust(w)
            for cell, w, a in zip(cells, widths, aligns)
        ).rstrip()

    kept = min(limit, table.count) if limit else table.count
    rows = ([table.cells[header][i] for header in headers]
            for i in range(kept))
    span = sum(widths) + views.GAP * (len(widths) - 1)
    note = [] if kept == table.count else [""] + wrap(
        f"Showing {kept:,} {view.unit} out of {table.count:,}."
        " Run /token-cost html to get full report.", span)
    return "\n".join([line(headers)] + [line(row) for row in rows] + note
                     + ["─" * span, line(total)])


def budget_notice(view, size: int, budget: int, width: int,
                  shown: int) -> str:
    """Why the table above stops where it does.

    Inline shell output reaches the conversation through the Bash tool, which
    carries about 30k characters; past that the model is handed a file path
    and a preview rather than a table. Printing 90k anyway doesn't produce a
    long table, it produces no table. So the table carries what it can, and
    this says what that cost. Where the rest is, its last row already said.
    """
    return "\n".join(wrap(
        f"{len(view.buckets):,} {view.unit} is {size:,} characters — past the"
        f" {budget:,} a conversation can carry, so the table above stops at"
        f" {shown:,}.", width))


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


def launch_block(cwd: str, width: int) -> str:
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
    tabs = " · ".join(label for label, _, _ in views.TABS)
    if opened:
        return "\n".join(
            wrap(f"A new {opened} window is now open with the token-cost UI"
                 f" for {project}.", width)
            + wrap("Switch to that window — your usage data is there, not"
                   " here.", width)
            + [""]
            + pairs([("Tabs", tabs),
                     ("Keys", "←/→ or 1-6 change tab · ↑/↓ scroll ·"
                              " r refresh · q quit")], width, gap=3)
            + [""]
            + wrap(f"Reopen it any time by running {command} in any"
                   " terminal.", width))
    return "\n".join(
        wrap(f"The token-cost UI shows {project}'s usage across tabs for"
             f" {tabs}.", width)
        + [""]
        + wrap("It needs a terminal window of its own, and one could not be"
               " opened for you here. Run this in any terminal:", width)
        + ["", f"    {command}"])


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


def remote_session() -> bool:
    """True in a cloud session, where the ledger can only be the container's.

    Claude Code sets CLAUDE_CODE_REMOTE for its own remote sessions and tests
    it against the string "true"; read it the same way rather than inventing
    a second idea of what counts as remote.
    """
    return os.environ.get("CLAUDE_CODE_REMOTE") == "true"


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

    # Resolved once, before anything prints: every line this command emits
    # lands in the same pane and wraps in the same way when it overruns.
    width = report_width(width)

    if any(a.lower() in views.UI_WORDS for a in args):
        print(launch_block(cwd, width))
        return 0

    # The page in the browser is every tab at once, so a period asked for
    # beside it has nothing left to narrow -- each one is already a tab of
    # its own. Lifted out of args here, and the rest read as usual.
    wants_html = any(a.lower() in views.HTML_WORDS for a in args)
    args = [a for a in args if a.lower() not in views.HTML_WORDS]

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

    def say(text: str) -> None:
        for line in wrap(text, width):
            print(line)

    if not rows:
        if remote_session():
            say("Nothing to report: this is a cloud session.")
            print()
            say("A ledger belongs to the machine that ran the tasks. A cloud"
                " session runs in a container built for it and thrown away"
                " afterwards, so there is no history in this one and no reach"
                " into yours — your project's usage is on the machine where"
                " you run Claude Code yourself.")
            return 0
        say(f"No token usage recorded yet for {project}.")
        print()
        say("Usage is recorded automatically as tasks complete —"
            " finish a task and run /token-cost again.")
        return 0

    if remote_session():
        # Rows in a cloud session are the container's own tasks, filed under
        # its home directory. Say whose they are before drawing a table that
        # otherwise reads as this project's whole history.
        say(f"Cloud session: the rows below are this container's own tasks,"
            f" recorded against {Path(cwd).resolve()}, and they go away with"
            f" it. Your project's usage is on the machine where you run"
            f" Claude Code yourself.")
        print()

    if wants_html:
        # Imported here rather than at the top: it reads a template off disk
        # and builds every tab at once, and nothing else this command does
        # needs either.
        import html_report
        print(html_report.launch_block(cwd, rows, project, width))
        return 0

    if period:
        narrowed, scope = views.in_window(rows, period)
        if not narrowed:
            say(f"No token usage recorded for {project} in that window"
                f" ({scope}).")
            return 0

    if imported:
        say(f"Imported {imported} earlier session(s) from transcripts on"
            " disk.")
        print()
    if show_overview:
        output = overview(rows, project, width)
        if budget and len(output) > budget:
            # Too many days to draw a chart of. The day table says the same
            # thing in a form that can shed its rows and still be a page.
            print(tab_report(rows, project, "days", None, width, budget))
            return 0
        print(output)
        return 0
    print(tab_report(rows, project, mode, period, width, budget))
    return 0


if __name__ == "__main__":
    sys.exit(main())
