#!/usr/bin/env python3
"""The token-cost terminal UI.

A full-screen, tabbed reader for the ledger. It exists because the plain
report has to fit inside a chat message and this doesn't: no ceiling on rows,
somewhere to put a chart, and tabs instead of remembering arguments.

Run it from a terminal -- it needs a real one. Inline shell inside Claude Code
is captured text with no TTY, so curses cannot attach there.

Every number on screen comes from views.py, the same module the plain report
builds from, so the two can never disagree.
"""

from __future__ import annotations

import curses
import locale
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger  # noqa: E402
import views  # noqa: E402

# (label, mode, period). Overview is the one view with no plain-text
# equivalent; the rest are the reports, one keypress apart.
TABS = [
    ("Overview", "overview", None),
    ("Today", "models", "today"),
    ("This Week", "days", "week"),
    ("This Month", "days", "month"),
    ("Tasks", "tasks", None),
    ("Sessions", "sessions", None),
]

GAP = 2                    # columns between table cells

# Box-drawing and block glyphs are multi-byte in UTF-8, and curses only counts
# them as one column each when the locale says so. Without that, ncurses
# advances a column per *byte*: a 126-character rule paints 42 dashes and
# every width calculation on the screen is wrong by a factor of three. So ask
# for the user's locale, then check whether we actually got a UTF-8 one and
# fall back to ASCII if not.
def _unicode_ok() -> bool:
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    # The locale's own codeset, not getpreferredencoding(): under LC_ALL=C
    # that reports UTF-8 on macOS while curses silently drops every
    # box-drawing character it is handed.
    codeset = ""
    if hasattr(locale, "nl_langinfo") and hasattr(locale, "CODESET"):
        codeset = locale.nl_langinfo(locale.CODESET) or ""
    if not codeset:
        codeset = (locale.getlocale()[1] or "")
    return codeset.lower().replace("-", "") == "utf8"


UNICODE = _unicode_ok()

if UNICODE:
    BLOCKS = " ▏▎▍▌▋▊▉█"   # eighths, so a bar can end between two columns
    BAR = "█"
    RULE, UNDER, VERT = "─", "━", "│"
    CORNERS = "╭╮╰╯"
    SEP, ELLIPSIS = "│", "…"
    LEFT_ARROW, RIGHT_ARROW = "‹", "›"
    KEYS = "←/→ tabs · ↑/↓ scroll · r refresh · q quit"
else:
    BLOCKS = " ##"
    BAR = "#"
    RULE, UNDER, VERT = "-", "=", "|"
    CORNERS = "++++"
    SEP, ELLIPSIS = "|", "~"
    LEFT_ARROW, RIGHT_ARROW = "<", ">"
    KEYS = "left/right tabs - up/down scroll - r refresh - q quit"

# Colour pairs
C_ACCENT, C_MUTED, C_HEAD, C_TOTAL = 1, 2, 3, 4


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

class Data:
    """The ledger, and the views built over it. Reloaded on `r`."""

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.project = Path(cwd).name
        self.reload()

    def reload(self) -> None:
        try:
            import record
            record.sync(self.cwd)
        except Exception:
            pass  # a sync failure must never stop the UI drawing
        self.rows = ledger.read_ledger(ledger.ledger_path(self.cwd))
        self.tasks = views.count_tasks(self.rows)
        self.cost = sum(r.get("cost_usd") or 0.0 for r in self.rows)
        self.cost_known = all(r.get("cost_usd") is not None for r in self.rows)
        days = sorted({ledger.local_day(r.get("ts")) for r in self.rows}
                      - {"unknown"})
        self.span = f"{days[0]} → {days[-1]}" if days else "no dated rows"

    def view(self, tab: int, label_width: int):
        _, mode, period = TABS[tab]
        rows, scope = self.rows, ""
        if period:
            rows, scope = views.in_window(self.rows, period)
        return views.build(rows, mode, scope, label_width), rows, scope


# --------------------------------------------------------------------------
# drawing helpers
# --------------------------------------------------------------------------

def fit(text: str, width: int) -> str:
    """Trim to width, marking the cut so a clipped label never reads as
    complete."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[:width - 1] + ELLIPSIS if width > 1 else ELLIPSIS


class Screen:
    """Thin wrapper that clips every write, so a narrow terminal or an
    over-long prompt can never raise out of the draw loop."""

    def __init__(self, stdscr):
        self.s = stdscr
        self.h, self.w = stdscr.getmaxyx()

    def measure(self) -> None:
        self.h, self.w = self.s.getmaxyx()

    def put(self, y: int, x: int, text: str, attr: int = 0) -> None:
        if y < 0 or y >= self.h or x >= self.w:
            return
        if not UNICODE:
            # Prompts carry whatever the user typed, and a byte-mode curses
            # counts a column per byte. One ASCII character per character
            # keeps the arithmetic on this screen true.
            text = text.encode("ascii", "replace").decode("ascii")
        text = fit(text, self.w - x)
        if not text:
            return
        try:
            self.s.addstr(y, x, text, attr)
        except curses.error:
            # The bottom-right cell always raises on write; nothing else here
            # can fail, and losing one glyph beats losing the frame.
            pass


def panel(sc: Screen, y: int, x: int, w: int, h: int, title: str) -> None:
    """A rounded box with a title set into its top edge."""
    muted = curses.color_pair(C_MUTED)
    w = min(w, sc.w - x)
    if w < 4 or h < 2:
        return
    tl, tr, bl, br = CORNERS
    head = f"{tl}{RULE} {fit(title, w - 6)} " if title else tl
    sc.put(y, x, head + RULE * max(0, w - len(head) - 1) + tr, muted)
    for row in range(y + 1, y + h - 1):
        sc.put(row, x, VERT, muted)
        sc.put(row, x + w - 1, VERT, muted)
    sc.put(y + h - 1, x, bl + RULE * (w - 2) + br, muted)


def bar(value: float, peak: float, width: int) -> str:
    """A horizontal bar with eighth-block precision, so small values still
    show something rather than rounding away to nothing."""
    if peak <= 0 or width <= 0:
        return ""
    eighths = max(1, round(value / peak * width * 8)) if value > 0 else 0
    full, rest = divmod(eighths, 8)
    return BAR * min(full, width) + (BLOCKS[min(rest, len(BLOCKS) - 1)]
                                     if full < width and rest else "")


# --------------------------------------------------------------------------
# table rendering
# --------------------------------------------------------------------------

def layout(columns, buckets, width, overrides):
    """Column widths that fit `width`.

    The prose column flexes: it gives space back before any column is
    dropped, and takes whatever is spare when there is room. Only when
    squeezing it to its floor still isn't enough do columns start going, in
    the order views.DROP_ORDER sets, and the header still names the ones that
    survive. Rows are never dropped to make a table fit.
    """
    cols = list(columns)
    flex_floor = 18

    while True:
        cells = [[str(c[2](b)) for c in cols] for b in buckets]
        total = [str(overrides[c[0]]) if c[0] in overrides
                 else (str(c[3](buckets)) if c[3] else "") for c in cols]
        total[0] = "TOTAL"
        grid = [[c[0] for c in cols]] + cells + [total]
        widths = [max(len(r[i]) for r in grid) for i in range(len(cols))]

        headers = [c[0] for c in cols]
        flex = next((headers.index(h) for h in ("TASK", "OPENED WITH")
                     if h in headers), None)
        used = sum(widths) + GAP * (len(cols) - 1)

        if used > width and flex is not None:
            # Take it out of the prose first: a shorter prompt costs less
            # than a missing column of numbers.
            give = min(used - width, max(0, widths[flex] - flex_floor))
            widths[flex] -= give
            used -= give

        if used <= width or len(cols) <= 2:
            break

        victim = next((h for h in views.DROP_ORDER if h in headers), None)
        if victim is None:
            break
        cols.pop(headers.index(victim))

    if flex is not None and used < width:
        widths[flex] += width - used
    return cols, widths, cells, total


def draw_row(sc: Screen, y: int, x: int, cells, widths, aligns, attr=0) -> None:
    for cell, w, align in zip(cells, widths, aligns):
        if x >= sc.w:
            return
        text = fit(cell, w)
        sc.put(y, x, text.rjust(w) if align == ">" else text.ljust(w), attr)
        x += w + GAP


def draw_table(sc: Screen, view, top: int, left: int, width: int, height: int,
               offset: int) -> int:
    """Draw a scrolled table. Returns how many body rows were visible."""
    cols, widths, cells, total = layout(
        view.columns, view.buckets, width, view.overrides)
    aligns = [c[1] for c in cols]

    draw_row(sc, top, left, [c[0] for c in cols], widths, aligns,
             curses.color_pair(C_HEAD) | curses.A_BOLD)

    body = height - 3            # header, rule, total
    shown = 0
    for i in range(body):
        index = offset + i
        if index >= len(cells):
            break
        draw_row(sc, top + 1 + i, left, cells[index], widths, aligns)
        shown += 1

    rule_y = top + 1 + min(body, max(shown, 0))
    sc.put(rule_y, left, RULE * min(sum(widths) + GAP * (len(widths) - 1),
                                    sc.w - left), curses.color_pair(C_MUTED))
    draw_row(sc, rule_y + 1, left, total, widths, aligns,
             curses.color_pair(C_TOTAL) | curses.A_BOLD)
    return shown


# --------------------------------------------------------------------------
# tabs
# --------------------------------------------------------------------------

def draw_overview(sc: Screen, data: Data, top: int, offset: int) -> int:
    """Headline figures, spend per day, model split, priciest tasks."""
    accent = curses.color_pair(C_ACCENT)
    muted = curses.color_pair(C_MUTED)
    left, width = 2, sc.w - 4
    y = top

    models = views.model_buckets(data.rows)
    stats_w = min(30, width)
    # Side by side needs room for both; below a certain width they stack, so
    # a narrow terminal loses the layout rather than the content.
    stacked = width - stats_w - 2 < 26
    models_w = width if stacked else min(width - stats_w - 2, 58)
    box_h = min(3 + len(models), 7)

    panel(sc, y, left, stats_w, 5, "Project")
    sc.put(y + 1, left + 2, f"{data.tasks:,} tasks", accent | curses.A_BOLD)
    sc.put(y + 2, left + 2, data.span, muted)
    sc.put(y + 3, left + 2, ledger.fmt_usd(data.cost, data.cost_known),
           accent | curses.A_BOLD)

    models_y = y + 5 if stacked else y
    if sc.h - models_y > 3:
        panel(sc, models_y, left if stacked else left + stats_w + 2,
              models_w, box_h, "Models")
        mx = left if stacked else left + stats_w + 2
        for i, b in enumerate(models[:box_h - 2]):
            cost = ledger.fmt_usd(b["cost"], b["cost_known"])
            sc.put(models_y + 1 + i, mx + 2,
                   f"{b['key']:<14}{b['tasks']:>6} tasks")
            sc.put(models_y + 1 + i, mx + models_w - len(cost) - 2, cost,
                   curses.A_BOLD)
    y = (models_y + box_h + 1) if stacked else (y + max(5, box_h) + 1)

    days = [b for b in views.day_buckets(data.rows) if b["key"] != "unknown"]
    days = days[-(max(3, sc.h - y - 12)):]
    if days and y + 4 < sc.h:
        chart_h = min(len(days) + 2, sc.h - y - 8)
        panel(sc, y, left, width, chart_h, "Cost per day")
        peak = max(b["cost"] for b in days) or 1.0
        room = width - 24
        for i, b in enumerate(days[-(chart_h - 2):]):
            sc.put(y + 1 + i, left + 2, b["key"][5:], muted)
            sc.put(y + 1 + i, left + 8, bar(b["cost"], peak, room), accent)
            cost = ledger.fmt_usd(b["cost"], b["cost_known"])
            sc.put(y + 1 + i, left + width - len(cost) - 2, cost)
        y += chart_h + 1

    tasks = sorted(views.task_buckets(data.rows), key=lambda b: -b["cost"])[:5]
    if tasks and y + 4 < sc.h:
        box_h = min(len(tasks) + 2, sc.h - y - 2)
        panel(sc, y, left, width, box_h, "Most expensive tasks")
        for i, b in enumerate(tasks[:box_h - 2]):
            cost = ledger.fmt_usd(b["cost"], b["cost_known"])
            sc.put(y + 1 + i, left + 2,
                   views.label_of(b, max(10, width - len(cost) - 8)))
            sc.put(y + 1 + i, left + width - len(cost) - 2, cost, curses.A_BOLD)
    return 0


def draw_tab(sc: Screen, data: Data, tab: int, top: int, offset: int):
    """Draw the active tab. Returns (visible rows, total rows) for scrolling."""
    label, mode, period = TABS[tab]
    if mode == "overview":
        return draw_overview(sc, data, top, offset), 0

    # Build labels at their stored length and let layout decide how much of
    # them fits; truncating up front left prompts short in a column that then
    # turned out to have room to spare.
    view, rows, scope = data.view(tab, ledger.PROMPT_CAP)
    if not view.buckets:
        sc.put(top, 2, f"Nothing recorded in this window ({scope}).",
               curses.color_pair(C_MUTED))
        return 0, 0

    sc.put(top, 2, view.subtitle, curses.color_pair(C_MUTED))
    # On a short terminal the breathing room goes first: a blank line is
    # worth less than a row of data, and reserving one of each left tables
    # showing a header and a total with nothing in between.
    gap = 2 if sc.h >= 20 else 1
    shown = draw_table(sc, view, top + gap, 2, sc.w - 4,
                       sc.h - top - gap - 1, offset)

    # A period tab gets the room the model split leaves over, so it answers
    # "what did this cost" and "what was I doing" at once.
    below = top + gap + len(view.buckets) + 3
    room = sc.h - below - 2
    if mode == "models" and room >= 5:      # subtitle, header, rule, total, a row
        task_view = views.build(rows, "tasks", scope, ledger.PROMPT_CAP)
        if task_view.buckets:
            sc.put(below, 2, f"{len(task_view.buckets)} tasks",
                   curses.color_pair(C_MUTED))
            draw_table(sc, task_view, below + gap, 2, sc.w - 4,
                       sc.h - below - gap - 1, offset)
            return shown, len(task_view.buckets)
    return shown, len(view.buckets)


# --------------------------------------------------------------------------
# chrome
# --------------------------------------------------------------------------

def draw_chrome(sc: Screen, data: Data, tab: int) -> int:
    """Title, tab bar and footer. Returns the first body row."""
    accent = curses.color_pair(C_ACCENT)
    muted = curses.color_pair(C_MUTED)

    sc.put(0, 2, "token-cost", accent | curses.A_BOLD)
    right = f"{data.project}"
    sc.put(0, max(2, sc.w - len(right) - 2), right, muted)

    # A tab bar that runs off the edge hides both which tabs exist and which
    # one is active. When the full set won't fit, name the current one and say
    # where it sits instead.
    full = sum(len(label) + 3 for label, _, _ in TABS) + 2
    if full > sc.w:
        current = TABS[tab][0]
        sc.put(2, 2, LEFT_ARROW, muted)
        sc.put(2, 4, fit(current, sc.w - 12), accent | curses.A_BOLD)
        where = f"{tab + 1}/{len(TABS)}"
        sc.put(2, max(4, sc.w - len(where) - 4), where, muted)
        sc.put(2, sc.w - 3, RIGHT_ARROW, muted)
        sc.put(3, 4, UNDER * min(len(current), sc.w - 8), accent)
        return 5

    x = 2
    for i, (label, _, _) in enumerate(TABS):
        if i == tab:
            sc.put(2, x, label, accent | curses.A_BOLD)
            sc.put(3, x, UNDER * len(label), accent)
        else:
            sc.put(2, x, label, muted)
        x += len(label) + 3
        if i < len(TABS) - 1:
            sc.put(2, x - 2, SEP, muted)

    sc.put(sc.h - 1, 2, fit(KEYS, sc.w - 4), muted)
    return 5


def run(stdscr, cwd: str) -> None:
    curses.curs_set(0)
    stdscr.keypad(True)
    # ncurses waits a full second after an Esc to see whether it begins an
    # arrow-key sequence. That is a second of lag on every arrow press; 25ms
    # is longer than any real terminal takes to deliver the rest.
    if hasattr(curses, "set_escdelay"):
        curses.set_escdelay(25)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        # 209 is the closest 256-colour cell to Claude's warm coral; on an
        # 8-colour terminal fall back to yellow rather than losing the accent.
        warm = 209 if curses.COLORS >= 256 else curses.COLOR_YELLOW
        grey = 245 if curses.COLORS >= 256 else curses.COLOR_WHITE
        curses.init_pair(C_ACCENT, warm, -1)
        curses.init_pair(C_MUTED, grey, -1)
        curses.init_pair(C_HEAD, warm, -1)
        curses.init_pair(C_TOTAL, warm, -1)

    data = Data(cwd)
    sc = Screen(stdscr)
    tab, offset = 0, 0

    while True:
        sc.measure()
        stdscr.erase()
        top = draw_chrome(sc, data, tab)
        if not data.rows:
            sc.put(top, 2, f"No token usage recorded yet for {data.project}.")
            sc.put(top + 2, 2, "Finish a task and press r.",
                   curses.color_pair(C_MUTED))
            total = 0
        else:
            _, total = draw_tab(sc, data, tab, top, offset)
        stdscr.refresh()

        key = stdscr.getch()
        page = max(1, sc.h - top - 6)
        # Not Esc: it is the first byte of every arrow-key sequence, so
        # binding it to quit means a right-arrow can close the app whenever
        # the rest of the sequence lands a moment late.
        if key in (ord("q"), ord("Q")):
            return
        elif key in (curses.KEY_RIGHT, ord("\t"), ord("l")):
            tab, offset = (tab + 1) % len(TABS), 0
        elif key in (curses.KEY_LEFT, curses.KEY_BTAB, ord("h")):
            tab, offset = (tab - 1) % len(TABS), 0
        elif ord("1") <= key <= ord("6"):
            tab, offset = min(key - ord("1"), len(TABS) - 1), 0
        elif key in (curses.KEY_DOWN, ord("j")):
            offset = min(offset + 1, max(0, total - 1))
        elif key in (curses.KEY_UP, ord("k")):
            offset = max(0, offset - 1)
        elif key == curses.KEY_NPAGE:
            offset = min(offset + page, max(0, total - 1))
        elif key == curses.KEY_PPAGE:
            offset = max(0, offset - page)
        elif key in (ord("g"), curses.KEY_HOME):
            offset = 0
        elif key in (ord("G"), curses.KEY_END):
            offset = max(0, total - page)
        elif key in (ord("r"), ord("R")):
            data.reload()
            offset = 0


def main() -> int:
    args = sys.argv[1:]
    cwd = str(Path.cwd())
    if "--cwd" in args:
        cwd = args[args.index("--cwd") + 1]
    if not sys.stdout.isatty():
        print("token-cost: the UI needs a terminal. Run it from your shell,"
              " or use /token-cost for the plain table.", file=sys.stderr)
        return 1
    curses.wrapper(run, cwd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
