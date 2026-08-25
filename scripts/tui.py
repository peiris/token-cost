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
import json
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
    # Half-height, so consecutive bars can't fuse into one orange mass: each
    # sits on its own baseline with clear air above it. A full block would
    # fill its cell top to bottom and the chart would read as a filled area,
    # which is what it looked like before.
    BAR, CAP = "▄", "▖"
    TRACK = "┈"          # the unfilled remainder, so every row shows its extent
    LEFT_EDGE, RIGHT_EDGE = "▕", "▏"
    RULE, UNDER, VERT = "─", "━", "│"
    CORNERS = "╭╮╰╯"
    T_DOWN, T_UP = "┬", "┴"
    T_LEFT, T_RIGHT = "├", "┤"
    HALF_DOWN, HALF_UP = "▄", "▀"
    LOGO = "▂▄▆█"          # a rising bar chart: what this thing is about
    MARKER = "▌"           # the bar down the left of the selected tab
    SEP, ELLIPSIS = "│", "…"
    LEFT_ARROW, RIGHT_ARROW = "‹", "›"
    KEYS = "←/→ Tabs · ↑/↓ Scroll · r Refresh · q Quit"
else:
    BAR, CAP = "=", "-"
    TRACK = "."
    LEFT_EDGE, RIGHT_EDGE = "[", "]"
    RULE, UNDER, VERT = "-", "=", "|"
    CORNERS = "++++"
    T_DOWN, T_UP = "+", "+"
    T_LEFT, T_RIGHT = "+", "+"
    HALF_DOWN, HALF_UP = None, None
    LOGO = "..:#"
    MARKER = "|"
    SEP, ELLIPSIS = "|", "~"
    LEFT_ARROW, RIGHT_ARROW = "<", ">"
    KEYS = "Left/Right Tabs - Up/Down Scroll - r Refresh - q Quit"

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
        return views.build(rows, mode, scope, label_width,
                           unknown=views.UNKNOWN_LONG), rows, scope


# --------------------------------------------------------------------------
# drawing helpers
# --------------------------------------------------------------------------

def version() -> str:
    """The plugin's version, read from the manifest beside this file."""
    manifest = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
    try:
        with open(manifest, encoding="utf-8") as fh:
            return "v" + json.load(fh)["version"]
    except (OSError, ValueError, KeyError):
        return ""


def caps(text: str) -> str:
    """Capitalise each word, leaving the rest of it alone.

    str.title() would flatten anything already capitalised -- a model id or
    a project name comes back mangled. Only chrome goes through here anyway:
    panel titles and messages, never a prompt or a figure.
    """
    return " ".join(w[:1].upper() + w[1:] if w else w for w in text.split(" "))


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


def centred(sc: Screen, y: int, x: int, width: int, parts) -> None:
    """Place (text, attr) segments as one centred run."""
    span = sum(len(text) for text, _ in parts)
    at = x + max(0, (width - span) // 2)
    for text, attr in parts:
        sc.put(y, at, text, attr)
        at += len(text)


def panel(sc: Screen, y: int, x: int, w: int, h: int, title: str) -> None:
    """A rounded box with its title set into the top edge."""
    edge = curses.color_pair(C_ACCENT)
    w = min(w, sc.w - x)
    if w < 4 or h < 2:
        return
    tl, tr, bl, br = CORNERS
    head = f"{tl}{RULE} {fit(caps(title), w - 6)} " if title else tl
    sc.put(y, x, head + RULE * max(0, w - len(head) - 1) + tr, edge)
    for row in range(y + 1, y + h - 1):
        sc.put(row, x, VERT, edge)
        sc.put(row, x + w - 1, VERT, edge)
    sc.put(y + h - 1, x, bl + RULE * (w - 2) + br, edge)


# Breathing room inside a frame: two columns each side, and a blank line top
# and bottom when the box is tall enough to spare them. A table pressed
# against its own border reads as cramped, but on a short terminal two rows
# of data are worth more than two rows of air.
PAD_X = 2


def inside(x: int, y: int, w: int, h: int, min_content: int = 1):
    """(x, y, width, height) of the content area of a box drawn at x, y.

    Every box gets its blank line top and bottom; it is given up only when
    the box would then have less than `min_content` rows left to show. A
    table needs four (header, a row, rule, total), a panel needs one.
    """
    pad_y = 1 if h - 4 >= min_content else 0
    return x + 1 + PAD_X, y + 1 + pad_y, w - 2 - 2 * PAD_X, h - 2 - 2 * pad_y


def box_for(content: int) -> int:
    """Box height that shows exactly `content` rows, padding included.

    The mirror of inside(). Sizing a box without consulting it is how a
    chart ends up with two blank rows under the bars, or none above them.
    """
    return content + 4


def bar(value: float, peak: float, width: int):
    """(filled, remainder) for one bar, both already sized to `width`.

    Any non-zero value keeps at least one cell: a day that cost something
    should never render as nothing at all.
    """
    if peak <= 0 or width <= 0:
        return "", ""
    cells = min(width, max(1, round(value / peak * width))) if value > 0 else 0
    return BAR * cells, TRACK * (width - cells)


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
    flex_floor = max(6, min(18, width // 3))

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

    if used < width:
        # Fill the width: prose column if there is one, otherwise the label
        # column, which pushes the numbers out to the right edge where a
        # full-width table wants them.
        widths[flex if flex is not None else 0] += width - used
    return cols, widths, cells, total


def draw_boxed_table(sc: Screen, view, top: int, left: int, width: int,
                     height: int, offset: int, title: str) -> int:
    """A table inside its own titled panel, filling the width it is given.
    Returns the body's row capacity."""
    if height < 6:                       # box, header, rule, total, one row
        return draw_table(sc, view, top, left, width, height, offset)
    panel(sc, top, left, width, height, title)
    # Four: a header, one row worth showing, the rule and the total.
    x, y, w, h = inside(left, top, width, height, min_content=4)
    return draw_table(sc, view, y, x, w, h, offset)


def put_label(sc: Screen, y: int, x: int, text: str, attr: int = 0) -> None:
    """Draw a cell, dimming the aside on an unknown label.

    "Unknown (tasks before plugin installed)" is one name and one
    explanation; the explanation shouldn't carry the same weight as the
    prompts it sits among.
    """
    sc.put(y, x, text, attr)
    if text.startswith(views.UNKNOWN + " ("):
        head = len(views.UNKNOWN)
        sc.put(y, x + head, text[head:], curses.color_pair(C_MUTED))


def draw_row(sc: Screen, y: int, x: int, cells, widths, aligns, attr=0,
             limit=None) -> None:
    """`limit` is the first column the row may not reach: inside a box that
    is the border, and a cell written past it would draw straight over the
    frame."""
    edge = sc.w if limit is None else min(sc.w, limit)
    for cell, w, align in zip(cells, widths, aligns):
        if x >= edge:
            return
        text = fit(cell, min(w, edge - x))
        put_label(sc, y, x, text.rjust(w) if align == ">" else text.ljust(w),
                  attr)
        x += w + GAP


def draw_table(sc: Screen, view, top: int, left: int, width: int, height: int,
               offset: int) -> int:
    """Draw a scrolled table. Returns how many rows the body can hold, which
    is what bounds scrolling -- see the clamp in run()."""
    cols, widths, cells, total = layout(
        view.columns, view.buckets, width, view.overrides)
    aligns = [c[1] for c in cols]

    limit = left + width
    draw_row(sc, top, left, [c[0] for c in cols], widths, aligns,
             curses.color_pair(C_HEAD) | curses.A_BOLD, limit)

    # The footer is pinned to the bottom of the space this table was given,
    # not floated under the last row that happened to fit. A total that
    # walks up the screen as you scroll is hard to read and harder to trust.
    body = max(0, height - 3)    # header, rule, total
    rule_y = top + height - 2

    for i in range(body):
        index = offset + i
        if index >= len(cells):
            break
        draw_row(sc, top + 1 + i, left, cells[index], widths, aligns,
                 0, limit)

    sc.put(rule_y, left, RULE * min(sum(widths) + GAP * (len(widths) - 1),
                                    width, sc.w - left),
           curses.color_pair(C_ACCENT))
    draw_row(sc, rule_y + 1, left, total, widths, aligns,
             curses.color_pair(C_TOTAL) | curses.A_BOLD, limit)
    return body


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
    # Side by side needs room for both; below a certain width they stack, so
    # a narrow terminal loses the layout rather than the content. Between
    # them they take the whole width rather than leaving a ragged edge.
    stacked = width < 62
    stats_w = width if stacked else max(30, width // 3)
    models_w = width if stacked else width - stats_w - 2
    stats_h = box_for(3)              # the three facts below
    box_h = max(box_for(min(len(models), 5)), stats_h if not stacked else 0)

    panel(sc, y, left, stats_w, stats_h, "Project")
    bx, by, _, _ = inside(left, y, stats_w, stats_h)
    sc.put(by, bx, f"{data.tasks:,} Tasks", accent | curses.A_BOLD)
    sc.put(by + 1, bx, data.span, muted)
    sc.put(by + 2, bx, ledger.fmt_usd(data.cost, data.cost_known),
           accent | curses.A_BOLD)

    models_y = y + stats_h if stacked else y
    models_x = left if stacked else left + stats_w + 2
    if sc.h - models_y > 3:
        panel(sc, models_y, models_x, models_w, box_h, "Models")
        mx, my, mw, mh = inside(models_x, models_y, models_w, box_h)
        for i, b in enumerate(models[:mh]):
            cost = ledger.fmt_usd(b["cost"], b["cost_known"])
            sc.put(my + i, mx, f"{b['key']:<14}{b['tasks']:>6} Tasks")
            sc.put(my + i, mx + mw - len(cost), cost, curses.A_BOLD)
    y = (models_y + box_h + 1) if stacked else (y + max(stats_h, box_h) + 1)

    days = [b for b in views.day_buckets(data.rows) if b["key"] != "unknown"]
    days = days[-(max(3, sc.h - y - 12)):]
    if days and y + 4 < sc.h:
        chart_h = min(box_for(len(days)), sc.h - y - 8)
        panel(sc, y, left, width, chart_h, "Cost per day")
        cx, cy, cw, ch = inside(left, y, width, chart_h)
        peak = max(b["cost"] for b in days) or 1.0
        # Fills the panel: label, bar, then the figure hard against the right
        # edge, so the row reads left to right with nothing dangling.
        room = max(12, cw - 19)
        for i, b in enumerate(days[-ch:]):
            filled, rest = bar(b["cost"], peak, room)
            sc.put(cy + i, cx, b["key"][5:], muted)
            sc.put(cy + i, cx + 6, LEFT_EDGE, accent)
            sc.put(cy + i, cx + 7, filled, accent)
            sc.put(cy + i, cx + 7 + len(filled), rest, muted)
            sc.put(cy + i, cx + 7 + room, RIGHT_EDGE, accent)
            cost = ledger.fmt_usd(b["cost"], b["cost_known"])
            sc.put(cy + i, cx + cw - len(cost), cost, curses.A_BOLD)
        y += chart_h + 1

    tasks = sorted(views.task_buckets(data.rows), key=lambda b: -b["cost"])[:5]
    if tasks and y + 4 < sc.h:
        box_h = min(box_for(len(tasks)), sc.h - y - 2)
        panel(sc, y, left, width, box_h, "Most expensive tasks")
        tx, ty, tw, th = inside(left, y, width, box_h)
        for i, b in enumerate(tasks[:th]):
            cost = ledger.fmt_usd(b["cost"], b["cost_known"])
            put_label(sc, ty + i, tx,
                      views.label_of(b, max(10, tw - len(cost) - 2),
                                     views.UNKNOWN_LONG))
            sc.put(ty + i, tx + tw - len(cost), cost, curses.A_BOLD)
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
    left, width = 2, sc.w - 4
    room = sc.h - top - 1

    if not view.buckets:
        panel(sc, top, left, width, box_for(1), label)
        nx, ny, _, _ = inside(left, top, width, box_for(1))
        sc.put(ny, nx, caps(f"Nothing recorded in this window ({scope})."),
               curses.color_pair(C_MUTED))
        return 0, 0

    # A period tab pairs the model split with the tasks behind it, so it
    # answers "what did this cost" and "what was I doing" at once. The split
    # is short and its length is known; the task list takes what is left.
    task_view = None
    if mode == "models":
        candidate = views.build(rows, "tasks", scope, ledger.PROMPT_CAP)
        if candidate.buckets and room >= len(view.buckets) + 12:
            task_view = candidate

    if task_view is None:
        shown = draw_boxed_table(sc, view, top, left, width, room, offset,
                                 view.subtitle)
        return shown, len(view.buckets)

    # header, rows, rule, total -- sized through box_for so the box asks for
    # the padding rather than fitting its contents exactly and being denied it.
    split_h = box_for(len(view.buckets) + 3)
    draw_boxed_table(sc, view, top, left, width, split_h, 0, view.subtitle)
    below = top + split_h + 1
    shown = draw_boxed_table(sc, task_view, below, left, width,
                             sc.h - below - 1, offset,
                             f"{len(task_view.buckets)} tasks")
    return shown, len(task_view.buckets)


# --------------------------------------------------------------------------
# chrome
# --------------------------------------------------------------------------

# The gutter each tab's label sits in: the marker bar, then a column before
# the text. Inactive tabs keep the same inset so the row stays on one
# rhythm whichever tab is selected.
LABEL_INSET = 2

# Between one tab and the next. Wide enough that the names read as separate
# items rather than a sentence, narrow enough to keep them all on the row.
NAV_GAP = 3


def draw_nav(sc: Screen, tab: int, row: int, x: int, width: int,
             band_top: int = None, band_rows: int = 1) -> None:
    """Tab names spread across the full width, the selected one marked by a
    bar down its left edge.

    Equal slots rather than a centred run: the row then reads as a bar of
    tabs rather than a sentence of words, and a tab's position doesn't shift
    when a neighbour's name is longer.
    """
    labels = [label for label, _, _ in TABS]
    accent = curses.color_pair(C_ACCENT)
    muted = curses.color_pair(C_MUTED)
    band_top = row if band_top is None else band_top

    # Left-aligned, each name in its own gutter: the marker column plus the
    # room before the text. Every tab reserves that gutter whether or not it
    # is the selected one, so the row doesn't shift sideways as you move
    # along it.
    def span_of(names, gap):
        return (sum(LABEL_INSET + len(name) for name in names)
                + gap * (len(names) - 1))

    # Give up the qualifier, then the breathing space, before giving up the
    # row: "Week" in its place tells you more than one name and a counter.
    short = [label.replace("This ", "") for label in labels]
    chosen = gap = None
    for names, spacing in ((labels, NAV_GAP), (short, NAV_GAP),
                           (short, NAV_GAP - 1)):
        if span_of(names, spacing) <= width:
            chosen, gap = names, spacing
            break

    if chosen is None:
        place = f"{tab + 1}/{len(TABS)}"   # too tight to lay out: name this one
        centred(sc, row, x, width, [
            (LEFT_ARROW + " ", muted),
            (labels[tab], accent | curses.A_BOLD),
            (f"  {place} ", muted),
            (RIGHT_ARROW, muted),
        ])
        return

    at = x
    for i, label in enumerate(chosen):
        if i == tab:
            for line in range(band_top, band_top + band_rows):
                sc.put(line, at, MARKER, accent)
            sc.put(row, at + LABEL_INSET, label, accent | curses.A_BOLD)
        else:
            sc.put(row, at + LABEL_INSET, label, muted)
        at += LABEL_INSET + len(label) + gap


def draw_chrome(sc: Screen, data: Data, tab: int) -> int:
    """Masthead, then the tab bar. Returns the first body row."""
    accent = curses.color_pair(C_ACCENT)
    muted = curses.color_pair(C_MUTED)
    left, width = 2, sc.w - 4

    if sc.h < 20:
        # No room for frames: name the app, the project and the tabs.
        sc.put(0, left, "token-cost", accent | curses.A_BOLD)
        sc.put(0, max(left, sc.w - len(data.project) - 2), data.project, muted)
        draw_nav(sc, tab, 1, left, width)
        sc.put(sc.h - 1, 2, fit(KEYS, sc.w - 4), muted)
        return 3

    head = box_for(1)
    panel(sc, 0, left, width, head, "")
    x, y, w, _ = inside(left, 0, width, head)

    # Shed the trailing detail rather than run into the frame: the project
    # goes first, then the version, and the name always survives.
    mark = [(LOGO, accent), ("  Claude Token Cost", accent | curses.A_BOLD)]
    tag = [(f" {version()}", muted)] if version() else []
    where = [("  ·  ", muted), (data.project, curses.A_BOLD)]
    for parts in (mark + tag + where, mark + tag, mark):
        if sum(len(text) for text, _ in parts) <= w:
            break
    centred(sc, y, x, w, parts)

    # The two frames share a rule rather than stacking one on top of the
    # other: they are one piece of chrome, and two lines a row apart read as
    # a gap between them. The shared row's ends become tees.
    nav = 3                            # frame, tabs, frame -- no blank rows
    joint = head - 1
    panel(sc, joint, left, width, nav, "")
    sc.put(joint, left, T_LEFT, accent)
    sc.put(joint, left + width - 1, T_RIGHT, accent)
    nx, ny, nw, _ = inside(left, joint, width, nav)
    draw_nav(sc, tab, ny, nx, nw, band_top=joint + 1, band_rows=nav - 2)

    sc.put(sc.h - 1, 2, fit(KEYS, sc.w - 4), muted)
    return joint + nav + 1


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
            sc.put(top, 2, f"No Token Usage Recorded Yet For {data.project}.")
            sc.put(top + 2, 2, "Finish a Task and Press r.",
                   curses.color_pair(C_MUTED))
            capacity = total = 0
        else:
            capacity, total = draw_tab(sc, data, tab, top, offset)

        # The last row belongs at the bottom of the box, not somewhere in the
        # middle with empty space under it. If a resize or a reload left the
        # offset past that point, correct it and draw again before anyone
        # sees the gap.
        furthest = max(0, total - capacity)
        if offset > furthest:
            offset = furthest
            continue
        stdscr.refresh()

        try:
            key = stdscr.getch()
        except KeyboardInterrupt:
            # Ctrl+C is how plenty of people close a full-screen app. Quit
            # the way q does, rather than unwinding a traceback over a
            # terminal that curses is still holding.
            return
        page = max(1, capacity - 1)
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
            offset = min(offset + 1, furthest)
        elif key in (curses.KEY_UP, ord("k")):
            offset = max(0, offset - 1)
        elif key == curses.KEY_NPAGE:
            offset = min(offset + page, furthest)
        elif key == curses.KEY_PPAGE:
            offset = max(0, offset - page)
        elif key in (ord("g"), curses.KEY_HOME):
            offset = 0
        elif key in (ord("G"), curses.KEY_END):
            offset = furthest
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
    try:
        curses.wrapper(run, cwd)
    except KeyboardInterrupt:
        pass  # interrupted somewhere other than the keyboard read
    return 0


if __name__ == "__main__":
    sys.exit(main())
