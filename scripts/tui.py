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
import time
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
    ("All Tasks", "tasks", None),
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
    KEYS = "Click or ←/→ Tabs · ↑/↓ Scroll · r Refresh · q/Esc Quit"
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
    KEYS = "Click or Left/Right Tabs - Up/Down Scroll - r Refresh - q/Esc Quit"

# Colour pairs
C_ACCENT, C_MUTED, C_HEAD, C_TOTAL = 1, 2, 3, 4
C_HOVER, C_HOVER_MUTED = 5, 6


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

class Tab:
    """The tables one tab shows, and what to call each of them.

    Every tab but the overview leads with a summary of what the table below
    can't say about itself, so a tab is two views rather than one. Which two
    is fixed by the tab, and both are built from rows that only change when
    the ledger is reread -- so they are built here, once, rather than on the
    way into each frame.
    """

    def __init__(self, data: "Data", index: int):
        _, mode, period = TABS[index]
        rows, self.scope = data.rows, ""
        if period:
            rows, self.scope = views.in_window(data.rows, period)

        # Labels are built at their stored length and layout decides how much
        # of them fits; truncating up front left prompts short in a column
        # that then turned out to have room to spare.
        self.view = views.build(rows, mode, self.scope, ledger.PROMPT_CAP,
                                unknown=views.UNKNOWN_LONG)
        self.summary = self.summary_title = None

        if mode == "models":
            # "How much" before "on what": the model split, then the tasks
            # that made it up.
            self.summary = self.view
            self.main = views.build(rows, "tasks", self.scope,
                                    ledger.PROMPT_CAP,
                                    unknown=views.UNKNOWN_LONG)
            self.main_title = f"{len(self.main.buckets)} Tasks"
        elif mode == "sessions":
            # No model split here: the table below already names every
            # session, and repeating the split said nothing the Tasks tab
            # hadn't. Its summary is the shape of the sessions themselves,
            # which draw_sessions_summary reads off this same view.
            self.main = self.view
            self.main_title = self.view.subtitle
        else:
            self.summary = views.build(rows, "models", self.scope,
                                       ledger.PROMPT_CAP)
            self.main = self.view
            self.main_title = self.view.subtitle

        if self.summary is not None:
            self.summary_title = self.summary.subtitle or "By Model"


class Overview:
    """The overview tab's figures: the model split, the spend per day, and
    the tasks that cost the most.

    Three roll-ups over every row in the ledger. Doing them per frame meant
    three full passes to move a highlight one row.
    """

    def __init__(self, data: "Data"):
        self.models = views.model_buckets(data.rows)
        self.days = [b for b in views.day_buckets(data.rows)
                     if b["key"] != "unknown"]
        self.tasks = sorted(views.task_buckets(data.rows),
                            key=lambda b: -b["cost"])[:5]


class Data:
    """The ledger, and the views built over it. Reloaded on `r`.

    Views are cached against the rows they came from. Between one keypress
    and the next the ledger cannot have changed, so a frame's job is to draw
    what is already built, not to build it again: rolling three thousand
    rows up per frame is what put a pointer crossing the table at three
    frames a second.
    """

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
        self.tokens = sum(sum(r.get(k, 0) for k in ledger.TOKEN_KEYS)
                          for r in self.rows)
        self.cost_known = all(r.get("cost_usd") is not None for r in self.rows)
        days = sorted({ledger.local_day(r.get("ts")) for r in self.rows}
                      - {"unknown"})
        self.span = f"{days[0]} → {days[-1]}" if days else "no dated rows"
        # Everything derived from those rows is now stale. Rebuilt lazily,
        # so a reload only pays for the tabs someone actually looks at.
        self._tabs = {}
        self._overview = None

    def tab(self, index: int) -> Tab:
        got = self._tabs.get(index)
        if got is None:
            got = self._tabs[index] = Tab(self, index)
        return got

    def overview(self) -> Overview:
        if self._overview is None:
            self._overview = Overview(self)
        return self._overview

    def warm(self) -> bool:
        """Build one tab nobody has opened yet. True if there was one left.

        Rolling a big ledger up takes longer than a frame does, so a tab
        built on the keypress that arrives at it is a keypress that visibly
        waits. The UI spends far more time idle than drawing, and this is
        the work that fits there: called from the input loop whenever
        nothing is queued, it walks the tabs one at a time until they are
        all standing by. Arriving at one is then only a draw.
        """
        if self._overview is None:
            self.overview()
            return True
        for index, (_, mode, _) in enumerate(TABS):
            if mode == "overview" or index in self._tabs:
                continue
            built = self.tab(index)
            # The cells too, not just the buckets: turning them into text is
            # half the cost, and it needs no terminal width to do it.
            rendered(built.main)
            if built.summary is not None:
                rendered(built.summary)
            return True
        return False


# --------------------------------------------------------------------------
# drawing helpers
# --------------------------------------------------------------------------

_VERSION = None


def version() -> str:
    """The plugin's version, read from the manifest beside this file.

    Read once. It is drawn in the masthead, which means it was being opened
    and parsed off disk twice on every frame -- for a string that cannot
    change while the process is running.
    """
    global _VERSION
    if _VERSION is None:
        manifest = (Path(__file__).resolve().parent.parent
                    / ".claude-plugin" / "plugin.json")
        try:
            with open(manifest, encoding="utf-8") as fh:
                _VERSION = "v" + json.load(fh)["version"]
        except (OSError, ValueError, KeyError):
            _VERSION = ""
    return _VERSION


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
        # Where the pointer last was, or None until it first moves. Only the
        # draw pass looks at it: nothing remembers which row is lit, so the
        # band lands right after a scroll or a reload with no bookkeeping.
        self.mouse = None
        self.hover = self.hover_muted = curses.A_REVERSE
        # Rebuilt with the chrome on every frame. Each entry is
        # (top, left, height, width, destination tab), so mouse input uses
        # the exact geometry that was drawn, including the compact arrows.
        self.nav_hits = []

    def hovered(self, y: int, x: int, w: int) -> bool:
        return (self.mouse is not None and self.mouse[0] == y
                and x <= self.mouse[1] < x + w)

    def nav_target(self, y: int, x: int):
        """The tab selected by a click at screen coordinate (y, x)."""
        for top, left, height, width, target in self.nav_hits:
            if top <= y < top + height and left <= x < left + width:
                return target
        return None

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

class Rendered:
    """A view's cells, turned into text once, and the widths they fit into.

    Rendering a cell means formatting a token count, pricing a bucket or
    condensing a prompt, and a table does it for every row it holds -- three
    thousand of them, to put twenty on screen. None of that changes between
    frames, and none of it depends on the width of the terminal: the prose
    column is capped at the ledger's own prompt length, never at the screen.

    So the text is made once, per view, and stored by column. What the width
    decides is only which columns survive and how wide each one is, which is
    arithmetic over a handful of numbers -- cheap enough to redo while a
    window is being dragged. Drawing a frame then costs the rows on screen
    rather than the rows in the ledger.
    """

    def __init__(self, view):
        self.columns = view.columns
        self.count = len(view.buckets)
        self.cells = {}      # header -> that column's cells, top to bottom
        self.foot = {}       # header -> its TOTAL cell
        self.widest = {}     # header -> the widest of the header and its cells
        for header, _, cell_of, total_of in view.columns:
            column = [str(cell_of(b)) for b in view.buckets]
            self.cells[header] = column
            self.foot[header] = (
                str(view.overrides[header]) if header in view.overrides
                else (str(total_of(view.buckets)) if total_of else ""))
            self.widest[header] = max([len(header)] + [len(c) for c in column])
        self.fits = {}       # width -> (columns, widths, total row)

    def fit(self, width: int):
        got = self.fits.get(width)
        if got is None:
            got = self.fits[width] = self._fit(width)
        return got

    def _fit(self, width: int):
        """Columns and widths that fit `width`.

        The prose column flexes: it gives space back before any column is
        dropped, and takes whatever is spare when there is room. Only when
        squeezing it to its floor still isn't enough do columns start going,
        in the order views.DROP_ORDER sets, and the header still names the
        ones that survive. Rows are never dropped to make a table fit.
        """
        cols = list(self.columns)
        flex_floor = max(6, min(18, width // 3))

        while True:
            headers = [c[0] for c in cols]
            total = [self.foot[h] for h in headers]
            total[0] = "TOTAL"
            widths = [max(self.widest[h], len(t))
                      for h, t in zip(headers, total)]

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
            # Fill the width: prose column if there is one, otherwise the
            # label column, which pushes the numbers out to the right edge
            # where a full-width table wants them.
            widths[flex if flex is not None else 0] += width - used
        return cols, widths, total


def rendered(view) -> Rendered:
    """A view's rendered cells, made on first sight of it and kept with it.

    Kept on the view rather than in a cache of its own because a view is
    built exactly when the ledger is reread -- so this is thrown away at
    precisely the moment it stops being true, with nothing to invalidate.
    """
    if view.render is None:
        view.render = Rendered(view)
    return view.render


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

    "Unknown (no prompt on record)" is one name and one explanation; the
    explanation shouldn't carry the same weight as the prompts it sits
    among.
    """
    sc.put(y, x, text, attr)
    if text.startswith(views.UNKNOWN + " ("):
        head = len(views.UNKNOWN)
        muted = (sc.hover_muted if attr == sc.hover
                 else curses.color_pair(C_MUTED))
        sc.put(y, x + head, text[head:], muted)


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
    table = rendered(view)
    cols, widths, total = table.fit(width)
    aligns = [c[1] for c in cols]
    headers = [c[0] for c in cols]

    limit = left + width
    draw_row(sc, top, left, headers, widths, aligns,
             curses.color_pair(C_HEAD) | curses.A_BOLD, limit)

    # The footer is pinned to the bottom of the space this table was given,
    # not floated under the last row that happened to fit. A total that
    # walks up the screen as you scroll is hard to read and harder to trust.
    body = max(0, height - 3)    # header, rule, total
    rule_y = top + height - 2

    for i in range(body):
        index = offset + i
        if index >= table.count:
            break
        y = top + 1 + i
        attr = 0
        if sc.hovered(y, left, width):
            # The band first, then the cells over it: the gaps between
            # columns belong to the highlight too, or the row lights up
            # in stripes.
            attr = sc.hover
            sc.put(y, left, " " * width, attr)
        # Gathered column by column: only the rows on screen are ever
        # assembled into rows at all.
        draw_row(sc, y, left, [table.cells[h][index] for h in headers],
                 widths, aligns, attr, limit)

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

    figures = data.overview()
    models = figures.models
    # Side by side needs room for both; below a certain width they stack, so
    # a narrow terminal loses the layout rather than the content. Between
    # them they take the whole width rather than leaving a ragged edge.
    stacked = width < 62
    stats_w = width if stacked else max(30, width // 3)
    models_w = width if stacked else width - stats_w - 2
    stats_h = box_for(4)              # the four facts below
    box_h = max(box_for(min(len(models), 5)), stats_h if not stacked else 0)

    panel(sc, y, left, stats_w, stats_h, "Project")
    bx, by, _, _ = inside(left, y, stats_w, stats_h)
    sc.put(by, bx, f"{data.tasks:,} Tasks", accent | curses.A_BOLD)
    sc.put(by + 1, bx, data.span, muted)
    sc.put(by + 2, bx, f"{ledger.fmt_tokens(data.tokens)} Tokens")
    sc.put(by + 3, bx, ledger.fmt_usd(data.cost, data.cost_known),
           accent | curses.A_BOLD)

    models_y = y + stats_h if stacked else y
    models_x = left if stacked else left + stats_w + 2
    if sc.h - models_y > 3:
        panel(sc, models_y, models_x, models_w, box_h, "Models")
        mx, my, mw, mh = inside(models_x, models_y, models_w, box_h)
        shown = models[:max(0, mh)]
        # A panel squeezed to nothing draws nothing: max() over an empty
        # column is a crash, and a header with no rows under it is a bug.
        if not shown:
            return 0
        costs = [ledger.fmt_usd(b["cost"], b["cost_known"]) for b in shown]
        counts = [f"{ledger.fmt_tokens(views.total_tokens(b))} Tokens"
                  for b in shown]
        # Columns, not offsets from the text: a figure that moves because
        # the one beside it got shorter is a figure you can't scan down.
        cost_w = max(len(c) for c in costs)
        count_w = max(len(c) for c in counts)
        for i, b in enumerate(shown):
            sc.put(my + i, mx, f"{b['key']:<14}{b['tasks']:>6} Tasks")
            sc.put(my + i, mx + mw - cost_w - count_w - 3,
                   counts[i].rjust(count_w), curses.color_pair(C_MUTED))
            sc.put(my + i, mx + mw - cost_w, costs[i].rjust(cost_w),
                   curses.A_BOLD)
    y = (models_y + box_h + 1) if stacked else (y + max(stats_h, box_h) + 1)

    days = figures.days[-(max(3, sc.h - y - 12)):]
    if days and y + 4 < sc.h:
        chart_h = min(box_for(len(days)), sc.h - y - 8)
        panel(sc, y, left, width, chart_h, "Cost per day")
        cx, cy, cw, ch = inside(left, y, width, chart_h)
        peak = max(b["cost"] for b in days) or 1.0
        # Fills the panel: label, bar, then the figure hard against the right
        # edge, so the row reads left to right with nothing dangling.
        shown = days[-ch:] if ch > 0 else []
        # A panel squeezed to nothing draws nothing: max() over an empty
        # column is a crash, and a header with no rows under it is a bug.
        if not shown:
            return 0
        costs = [ledger.fmt_usd(b["cost"], b["cost_known"]) for b in shown]
        counts = [ledger.fmt_tokens(views.total_tokens(b)) for b in shown]
        cost_w = max(len(c) for c in costs)
        count_w = max(len(c) for c in counts)
        room = max(12, cw - 10 - cost_w - count_w - 3)
        for i, b in enumerate(shown):
            filled, rest = bar(b["cost"], peak, room)
            sc.put(cy + i, cx, b["key"][5:], muted)
            sc.put(cy + i, cx + 6, LEFT_EDGE, accent)
            sc.put(cy + i, cx + 7, filled, accent)
            sc.put(cy + i, cx + 7 + len(filled), rest, muted)
            sc.put(cy + i, cx + 7 + room, RIGHT_EDGE, accent)
            sc.put(cy + i, cx + cw - cost_w - count_w - 3,
                   counts[i].rjust(count_w), muted)
            sc.put(cy + i, cx + cw - cost_w, costs[i].rjust(cost_w),
                   curses.A_BOLD)
        y += chart_h + 1

    tasks = figures.tasks
    if tasks and y + 4 < sc.h:
        box_h = min(box_for(len(tasks)), sc.h - y - 2)
        panel(sc, y, left, width, box_h, "Most expensive tasks")
        tx, ty, tw, th = inside(left, y, width, box_h)
        shown = tasks[:max(0, th)]
        # A panel squeezed to nothing draws nothing: max() over an empty
        # column is a crash, and a header with no rows under it is a bug.
        if not shown:
            return 0
        costs = [ledger.fmt_usd(b["cost"], b["cost_known"]) for b in shown]
        counts = [ledger.fmt_tokens(views.total_tokens(b)) for b in shown]
        cost_w = max(len(c) for c in costs)
        count_w = max(len(c) for c in counts)
        room = max(10, tw - cost_w - count_w - 6)
        for i, b in enumerate(shown):
            put_label(sc, ty + i, tx,
                      views.label_of(b, room, views.UNKNOWN_LONG))
            sc.put(ty + i, tx + tw - cost_w - count_w - 3,
                   counts[i].rjust(count_w), curses.color_pair(C_MUTED))
            sc.put(ty + i, tx + tw - cost_w, costs[i].rjust(cost_w),
                   curses.A_BOLD)
    return 0


def draw_sessions_summary(sc: Screen, view, top: int, left: int,
                          width: int, box_h: int) -> None:
    """The sessions tab's opening panel: the shape of the sessions
    themselves. Every other tab leads with the model split; repeating it
    here said nothing the Tasks tab hadn't. What a list of sessions can't
    show about itself is its aggregate -- how many, how much each carries
    on average -- and its outliers, which recency ordering buries: the
    session that cost the most and the one that read the most.
    """
    accent = curses.color_pair(C_ACCENT)
    muted = curses.color_pair(C_MUTED)
    panel(sc, top, left, width, box_h, "Sessions")
    x, y, w, h = inside(left, top, width, box_h)
    buckets = view.buckets
    if not buckets or h <= 0:
        return

    n = len(buckets)
    known = all(b["cost_known"] for b in buckets)
    avg_cost = sum(b["cost"] for b in buckets) / n
    priciest = max(buckets, key=lambda b: b["cost"])
    longest = max(buckets, key=views.total_tokens)

    if h >= 1:
        mid = f"{view.tasks / n:.1f} Tasks/session"
        tail = f"avg {ledger.fmt_usd(avg_cost, known)}/session"
        sc.put(y, x, f"{n:,} Sessions", accent | curses.A_BOLD)
        sc.put(y, x + w - len(tail), tail, curses.A_BOLD)
        if w - len(tail) - 3 > 14 + len(mid):
            sc.put(y, x + w - len(tail) - 3 - len(mid), mid, muted)

    # The two outliers, named like the table below names its rows: when
    # they started and what they were opened with, figure hard right.
    figs = [ledger.fmt_usd(priciest["cost"], priciest["cost_known"]),
            f"{ledger.fmt_tokens(views.total_tokens(longest))} Tokens"]
    fig_w = max(len(f) for f in figs)
    room = max(10, w - 9 - 12 - fig_w - 2)
    for i, (tag, b, fig) in enumerate(
            (("Priciest", priciest, figs[0]), ("Longest", longest, figs[1]))):
        if 1 + i >= h:
            break
        row = y + 1 + i
        sc.put(row, x, tag, accent)
        sc.put(row, x + 9, views.started(b), muted)
        put_label(sc, row, x + 9 + 12,
                  views.label_of(b, room, views.UNKNOWN_LONG))
        sc.put(row, x + w - len(fig), fig, curses.A_BOLD)


def draw_tab(sc: Screen, data: Data, tab: int, top: int, offset: int):
    """Draw the active tab. Returns (visible rows, total rows) for scrolling."""
    label, mode, _ = TABS[tab]
    if mode == "overview":
        return draw_overview(sc, data, top, offset), 0

    built = data.tab(tab)
    view, summary, main = built.view, built.summary, built.main
    left, width = 2, sc.w - 4
    room = sc.h - top - 1

    if not view.buckets:
        panel(sc, top, left, width, box_for(1), label)
        nx, ny, _, _ = inside(left, top, width, box_for(1))
        sc.put(ny, nx,
               caps(f"Nothing recorded in this window ({built.scope})."),
               curses.color_pair(C_MUTED))
        return 0, 0

    if summary is None:
        split_h = box_for(3)               # count row plus the two outliers
    else:
        split_h = box_for(len(summary.buckets) + 3)  # header, rows, rule, total

    if not main.buckets or room < split_h + 8:
        shown = draw_boxed_table(sc, view, top, left, width, room, offset,
                                 view.subtitle)
        return shown, len(view.buckets)

    if summary is None:
        draw_sessions_summary(sc, view, top, left, width, split_h)
    else:
        draw_boxed_table(sc, summary, top, left, width, split_h, 0,
                         built.summary_title)
    below = top + split_h + 1
    shown = draw_boxed_table(sc, main, below, left, width,
                             sc.h - below - 1, offset, built.main_title)
    return shown, len(main.buckets)


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
    sc.nav_hits = []

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
        parts = [
            (LEFT_ARROW + " ", muted),
            (labels[tab], accent | curses.A_BOLD),
            (f"  {place} ", muted),
            (RIGHT_ARROW, muted),
        ]
        span = sum(len(text) for text, _ in parts)
        at = x + max(0, (width - span) // 2)
        centred(sc, row, x, width, parts)
        sc.nav_hits.extend([
            (row, at, 1, len(parts[0][0]), (tab - 1) % len(TABS)),
            (row, at + span - len(parts[-1][0]), 1, len(parts[-1][0]),
             (tab + 1) % len(TABS)),
        ])
        return

    at = x
    for i, label in enumerate(chosen):
        button_width = LABEL_INSET + len(label)
        sc.nav_hits.append((band_top, at, band_rows, button_width, i))
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


def mouse_report(stdscr):
    """(code, column, row) of the SGR mouse report whose Esc getch() just
    returned, or None.

    An ncurses new enough to know the SGR mouse protocol hands reports over
    as KEY_MOUSE and this is never reached. The one Apple ships is not:
    there, every report leaks through getch() as loose bytes behind an Esc,
    and has to be reassembled. Whatever turns out to be something else is
    pushed back for the loop to read normally.
    """
    seen = []
    stdscr.timeout(25)       # the rest of a report is already in flight
    try:
        def take():
            ch = stdscr.getch()
            if ch != -1:
                seen.append(ch)
            return ch

        if take() != ord("[") or take() != ord("<"):
            raise ValueError
        fields, digits = [], ""
        while True:
            ch = take()
            if 48 <= ch <= 57 and len(digits) < 5:      # 0-9
                digits += chr(ch)
            elif ch == ord(";") and len(fields) < 2:
                fields.append(int(digits or "0"))
                digits = ""
            elif ch in (ord("M"), ord("m")) and len(fields) == 2:
                return fields[0], fields[1], int(digits or "0")
            else:
                raise ValueError
    except ValueError:
        for ch in reversed(seen):
            curses.ungetch(ch)
        return None
    finally:
        stdscr.timeout(-1)


# One frame at 60fps. Under a burst of input the UI paints at least this
# often, so it stays legibly in motion rather than going quiet while it
# works through a backlog.
FRAME = 1 / 60


def queued(stdscr) -> bool:
    """Whether another key is already waiting behind the one just handled.

    Motion tracking means the terminal sends a report for every cell the
    pointer crosses, so a flick of the mouse arrives as a burst of them.
    Painting one frame each would be painting frames nobody can see, and
    each one is a frame further behind the pointer. Peek instead, and let
    the loop swallow the burst before drawing where the pointer ended up.
    """
    stdscr.nodelay(True)
    try:
        ch = stdscr.getch()
    finally:
        stdscr.nodelay(False)
    if ch == -1:
        return False
    curses.ungetch(ch)
    return True


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
        if curses.COLORS >= 256:
            # The hovered row names both of its colours: on a light theme
            # the default foreground is near-black and would sink into the
            # dark band if the pair inherited it. Under 256 colours there
            # is no quiet grey to be had, and Screen's A_REVERSE stands in.
            curses.init_pair(C_HOVER, 253, 237)
            curses.init_pair(C_HOVER_MUTED, 245, 237)

    # Hover needs the terminal streaming pointer positions, which is more
    # than ncurses ever asks for: mousemask() requests clicks (xterm mode
    # 1000). Any-motion tracking is mode 1003, and SGR encoding is 1006 --
    # without that, wheel-down on a mouse-v1 ncurses collapses into a bare
    # position report, and coordinates stop at column 223. Both are set by
    # hand, after mousemask so its own enable string can't knock the
    # terminal back to click-only. A terminal that can't track motion sends
    # nothing, and the highlight simply waits for a click.
    if curses.mousemask(curses.ALL_MOUSE_EVENTS
                        | curses.REPORT_MOUSE_POSITION)[0]:
        curses.mouseinterval(0)   # a wheel tick must not wait out a click test
        sys.stdout.write("\x1b[?1003h\x1b[?1006h")
        sys.stdout.flush()

    data = Data(cwd)
    sc = Screen(stdscr)
    if curses.has_colors() and curses.COLORS >= 256:
        sc.hover = curses.color_pair(C_HOVER)
        sc.hover_muted = curses.color_pair(C_HOVER_MUTED)
    tab, offset = 0, 0
    capacity = total = 0
    painted = 0.0

    while True:
        # Draw when the screen is up to date with the input, or when a frame
        # is due anyway. Skipping the paint while keys are still queued is
        # what keeps a fast scroll or a swept pointer landing in one frame
        # instead of a dozen; the clock is the floor under that, so a
        # continuous stream still paints sixty times a second.
        if time.monotonic() - painted >= FRAME or not queued(stdscr):
            sc.measure()
            stdscr.erase()
            top = draw_chrome(sc, data, tab)
            if not data.rows:
                sc.put(top, 2,
                       f"No Token Usage Recorded Yet For {data.project}.")
                sc.put(top + 2, 2, "Finish a Task and Press r.",
                       curses.color_pair(C_MUTED))
                capacity = total = 0
            else:
                capacity, total = draw_tab(sc, data, tab, top, offset)

            # The last row belongs at the bottom of the box, not somewhere in
            # the middle with empty space under it. If a resize or a reload
            # left the offset past that point, correct it and draw again
            # before anyone sees the gap.
            if offset > max(0, total - capacity):
                offset = max(0, total - capacity)
                continue
            stdscr.refresh()
            painted = time.monotonic()

        furthest = max(0, total - capacity)

        # Nothing waiting, so put the lull to use: build the tabs that
        # haven't been opened yet, one at a time, giving up the moment a key
        # arrives. By the time anyone has read the screen in front of them,
        # every tab behind it is ready to draw.
        while not queued(stdscr) and data.warm():
            pass

        try:
            key = stdscr.getch()
        except KeyboardInterrupt:
            # Ctrl+C is how plenty of people close a full-screen app. Quit
            # the way q does, rather than unwinding a traceback over a
            # terminal that curses is still holding.
            return
        page = max(1, capacity - 1)
        if key in (ord("q"), ord("Q")):
            return
        elif key == 27:
            # Esc quits -- but 27 is also the first byte of every arrow and
            # function key, and of every mouse report an old ncurses can't
            # parse. The tell is what follows: a sequence's remaining bytes
            # are already waiting, a real Esc is followed by silence. So
            # read a mouse report if one is there; failing that, swallow
            # the sequence rather than quit over an exotic arrow key, and
            # quit only on silence.
            report = mouse_report(stdscr)
            if report:
                code, mx, my = report
                sc.mouse = (my - 1, mx - 1)   # reports count from one
                wheel = code & ~28            # shift/alt/ctrl, shrugged off
                if wheel == 64:
                    offset = max(0, offset - 1)
                elif wheel == 65:
                    offset = min(offset + 1, furthest)
                elif wheel == 0:
                    target = sc.nav_target(*sc.mouse)
                    if target is not None:
                        tab, offset = target, 0
            else:
                stdscr.nodelay(True)
                follower = stdscr.getch()
                while stdscr.getch() != -1:
                    pass
                stdscr.nodelay(False)
                if follower == -1:
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
        elif key == curses.KEY_MOUSE:
            # An ncurses that parses reports itself. BUTTON5 only exists on
            # a mouse-v2 build; where the constant is missing, wheel-down is
            # arriving through mouse_report() instead.
            try:
                _, mx, my, _, state = curses.getmouse()
            except curses.error:
                continue
            sc.mouse = (my, mx)
            left = (curses.BUTTON1_RELEASED
                    | curses.BUTTON1_PRESSED
                    | getattr(curses, "BUTTON1_CLICKED", 0)
                    | getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0)
                    | getattr(curses, "BUTTON1_TRIPLE_CLICKED", 0))
            # With tracking on, the terminal stops turning the wheel into
            # arrow keys, so the wheel is put back here.
            if state & curses.BUTTON4_PRESSED:
                offset = max(0, offset - 1)
            elif state & getattr(curses, "BUTTON5_PRESSED", 0):
                offset = min(offset + 1, furthest)
            elif state & left:
                target = sc.nav_target(my, mx)
                if target is not None:
                    tab, offset = target, 0


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
    finally:
        # endwin unwinds what ncurses switched on, not what run() did. Leave
        # these modes behind and the shell gets escape codes sprayed at it
        # for every twitch of the mouse.
        sys.stdout.write("\x1b[?1003l\x1b[?1006l")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
