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
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger  # noqa: E402
import views  # noqa: E402

# (label, mode, period), shared with the report so the tab bar it prints
# names the same tabs this one navigates.
TABS = views.TABS

SEARCHABLE = {4: "Tasks", 5: "Sessions"}

GAP = views.GAP            # columns between table cells

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

# The rewritten terminfo, held for the life of the process: ncurses reads
# the directory at initscr(), long after this module finished importing.
_TERMINFO = None


def _drop_repeat_char() -> bool:
    """Hand ncurses this terminal's terminfo with `rep` taken out of it.

    `rep` is how terminfo says "draw that character n more times", and
    ncurses reaches for it the moment a row repeats one cell far enough to
    be worth the escape -- which on this screen is every panel edge, every
    table rule and every bar in the chart. The capability carries the
    character as a single byte (`%p1%c`), so it cannot express a glyph that
    is three bytes of UTF-8, and the ncurses macOS ships uses it anyway: a
    rule of ─ (U+2500) leaves as a NUL and disappears, a bar of ▄ (U+2584)
    leaves as 0x84 and lands as a row of replacement characters. Only the
    terminal decides whether we hit this -- xterm-256color has no `rep` and
    never showed it; xterm-ghostty has one, and so do kitty's and wezterm's.

    So take the capability away. Recompile the terminal's own entry without
    that one line into a directory of ours and point TERMINFO at it: every
    other capability is still the real terminal's, and the only thing lost
    is an optimisation ncurses cannot perform correctly. Losing it costs a
    few hundred bytes on a frame that draws a long rule.

    True once the swap is in place, or when the terminal never offered
    `rep` to begin with. False if the entry could not be rewritten, which
    is what the ASCII fallback below is for.
    """
    global _TERMINFO
    term = os.environ.get("TERM", "")
    if not term or not sys.stdout.isatty():
        # No terminal to read a description from -- `token-cost html`, or a
        # report piped somewhere. Nothing here will draw.
        return True
    try:
        curses.setupterm(term, sys.stdout.fileno())
        if not curses.tigetstr("rep"):
            return True
    except (curses.error, ValueError, OSError):
        return True                     # no description to read, so no `rep`
    try:
        entry = subprocess.run(["infocmp", "-x", term], check=True,
                               capture_output=True, text=True).stdout
        # A capability ends at the first comma that isn't spoken for by a
        # backslash, and takes the whitespace in front of it with it.
        without = re.sub(r"\s*\brep=(?:\\.|[^,\\])*,", "", entry)
        if without == entry:
            return False                # nothing came out, so nothing changed
        _TERMINFO = tempfile.TemporaryDirectory(prefix="token-cost-terminfo.")
        subprocess.run(["tic", "-x", "-o", _TERMINFO.name, "-"], input=without,
                       check=True, capture_output=True, text=True)
        # Where tic files an entry, and where ncurses will come looking. A
        # directory that turns out not to hold it is worse than not setting
        # TERMINFO at all: ncurses would walk on to the original and use
        # `rep` after all, with nothing here expecting it to.
        wrote = any((Path(_TERMINFO.name) / part / term).exists()
                    for part in (f"{ord(term[0]):02x}", term[0]))
    except (OSError, ValueError, subprocess.SubprocessError):
        wrote = False
    if not wrote:
        _TERMINFO = None
        return False
    # TERMINFO is a single directory and we are about to take it. Ghostty
    # sets it to the one inside its own bundle, which is nowhere ncurses
    # would look on its own, so hand that path to TERMINFO_DIRS on the way
    # past: if anything about the rewritten entry turns out not to satisfy
    # the lookup, the search carries on to the real one instead of ending
    # in a terminal ncurses cannot find at all. The trailing empty element
    # is how that list spells "and then the usual places".
    where = os.environ.get("TERMINFO")
    if where:
        rest = os.environ.get("TERMINFO_DIRS", "")
        os.environ["TERMINFO_DIRS"] = f"{where}:{rest}" if rest else f"{where}:"
    os.environ["TERMINFO"] = _TERMINFO.name
    return True


REPEATABLE = _drop_repeat_char()

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
    FILTER_KEYS = "Click or ←/→ Tabs · ↑/↓ Scroll · r Refresh · q Quit"
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
    FILTER_KEYS = "Click or Left/Right Tabs - Up/Down Scroll - r Refresh - q Quit"

if UNICODE and not REPEATABLE:
    # `rep` is still there and ncurses will still send a repeated cell
    # through it a byte at a time. Only the three glyphs this screen draws
    # in long runs have to give way -- an ASCII one survives the trip. The
    # corners, edges, tees and marks are single cells, never repeated far
    # enough for ncurses to reach for the capability, and stay as they are.
    BAR, TRACK, RULE = "=", ".", "-"

# Colour pairs
C_ACCENT, C_MUTED, C_HEAD, C_TOTAL = 1, 2, 3, 4
C_HOVER, C_HOVER_MUTED = 5, 6


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

# What a tab holds and what to call it is a view question, not a curses one,
# and the report prints the same tabs this file navigates. Both read it from
# the one place; these are the names this file has always used.
Tab = views.Tab
FilteredTab = views.FilteredTab



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

    def tab(self, index: int, query: str = "") -> Tab:
        got = self._tabs.get(index)
        if got is None:
            got = self._tabs[index] = Tab(self.rows, *TABS[index][1:])
        return got.filtered(query)

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


class Search:
    """Per-tab search text and the edit that is currently in progress."""

    def __init__(self):
        self.queries = {tab: "" for tab in SEARCHABLE}
        self.editing = None
        self.before = ""

    def query(self, tab: int) -> str:
        return self.queries.get(tab, "")

    def begin(self, tab: int) -> None:
        if tab not in SEARCHABLE:
            return
        self.editing = tab
        self.before = self.queries[tab]

    def commit(self) -> None:
        self.editing = None

    def cancel(self) -> None:
        if self.editing is not None:
            self.queries[self.editing] = self.before
        self.editing = None

    def clear(self, tab: int) -> None:
        if tab in self.queries:
            self.queries[tab] = ""

    def input(self, tab: int, key: int, text: str | None) -> tuple[bool, bool]:
        """Consume a search-edit key. Returns (handled, query changed)."""
        if self.editing != tab:
            return False, False
        if key in (curses.KEY_ENTER, 10, 13):
            self.commit()
            return True, False
        if key in (curses.KEY_BACKSPACE, 8, 127):
            old = self.queries[tab]
            self.queries[tab] = old[:-1]
            return True, self.queries[tab] != old
        if key == 21:                   # Ctrl+U: clear the input line
            changed = bool(self.queries[tab])
            self.queries[tab] = ""
            return True, changed
        if text and text.isprintable():
            self.queries[tab] += text
            return True, True
        # Navigation keys belong to the input while it has focus; mouse and
        # resize events still need the main loop so clicking a tab and
        # resizing the terminal continue to work mid-search.
        return key not in (curses.KEY_MOUSE, curses.KEY_RESIZE), False


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
        self.search_hit = None

    def hovered(self, y: int, x: int, w: int) -> bool:
        return (self.mouse is not None and self.mouse[0] == y
                and x <= self.mouse[1] < x + w)

    def nav_target(self, y: int, x: int):
        """The tab selected by a click at screen coordinate (y, x)."""
        for top, left, height, width, target in self.nav_hits:
            if top <= y < top + height and left <= x < left + width:
                return target
        return None

    def search_target(self, y: int, x: int):
        """The searchable tab whose input contains (y, x), if any."""
        if self.search_hit is None:
            return None
        tab, top, left, width, height = self.search_hit
        return tab if top <= y < top + height and left <= x < left + width else None

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

    def put_in(self, y: int, x: int, text: str, limit: int,
               attr: int = 0) -> None:
        """Draw inside a box: never at or past `limit`.

        put() clips at the edge of the terminal, which is the wrong edge for
        anything drawn inside a frame. A label one column too long for its
        panel doesn't run off the screen -- it paints over the panel's own
        border, and the box it was sitting in stops looking like a box.
        Everything drawn inside a frame goes through here.
        """
        self.put(y, x, fit(text, limit - x), attr)


def centred(sc: Screen, y: int, x: int, width: int, parts) -> None:
    """Place (text, attr) segments as one centred run, inside `width`."""
    span = sum(len(text) for text, _ in parts)
    at = x + max(0, (width - span) // 2)
    for text, attr in parts:
        sc.put_in(y, at, text, x + width, attr)
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


def inside(x: int, y: int, w: int, h: int, min_content: int = 1,
           pad_top: bool = True, pad_bottom: bool | None = None):
    """(x, y, width, height) of the content area of a box drawn at x, y.

    Every box gets its blank line top and bottom; it is given up only when
    the box would then have less than `min_content` rows left to show. A
    table needs four (header, a row, rule, total), a panel needs one.
    """
    if pad_bottom is None:
        pad_bottom = pad_top
    spare = h - 2 - min_content
    if pad_top and pad_bottom:
        top = bottom = 1 if spare >= 2 else 0
    else:
        top = 1 if pad_top and spare >= 1 else 0
        bottom = 1 if pad_bottom and spare - top >= 1 else 0
    return (x + 1 + PAD_X, y + 1 + top, w - 2 - 2 * PAD_X,
            h - 2 - top - bottom)


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

# Fitting a view's columns to a width is not a curses problem: the report has
# the same one, against a chat pane it can measure but not control. So the
# fitting lives in views.py with everything else the two frontends agree on,
# and these are the names this file has always called it by.
Rendered = views.Rendered
rendered = views.rendered


def draw_search_input(sc: Screen, search: Search, tab: int, y: int, x: int,
                      width: int, boxed: bool = False) -> int:
    """A persistent search input inside a task or session table."""
    muted = curses.color_pair(C_MUTED)
    text_padding = 2
    field_x = x
    field_w = max(0, width)
    inner_w = max(0, field_w - 2 - text_padding * 2)
    text_x = field_x + 1 + text_padding
    query = search.query(tab)
    row = y + 1 if boxed else y

    # Keep the field chrome quiet so the active query is the thing that draws
    # the eye. The field fills the table content width; the text keeps a
    # comfortable inset inside its border.
    sc.search_hit = (tab, y, field_x, field_w, 3 if boxed else 1)
    if boxed:
        sc.put(y, field_x, CORNERS[0] + RULE * max(0, field_w - 2)
               + CORNERS[1], muted)
        sc.put(y + 2, field_x, CORNERS[2] + RULE * max(0, field_w - 2)
               + CORNERS[3], muted)
    if search.editing == tab:
        value = query + MARKER
        if len(value) > inner_w:
            value = (ELLIPSIS + value[-(inner_w - 1):]
                     if inner_w > 1 else ELLIPSIS[:inner_w])
        sc.put(row, text_x, value, curses.A_BOLD)
        if not query:
            sc.put(row, text_x + 2, fit("Type a name to filter", inner_w - 2),
                   muted)
    elif query:
        shown = fit(query, inner_w)
        sc.put(row, text_x, shown, curses.A_BOLD)
        hint_x = text_x + len(shown) + 2
        if hint_x + len("Esc clears") <= field_x + inner_w:
            sc.put(row, hint_x, "Esc clears", muted)
    else:
        noun = SEARCHABLE[tab].lower()
        placeholder = f"Press / or click, then type to filter {noun}"
        sc.put(row, text_x, fit(placeholder, inner_w), muted)
    if not boxed:
        sc.put(row, field_x, LEFT_EDGE, muted)
        sc.put(row, field_x + field_w - 1, RIGHT_EDGE, muted)
    else:
        sc.put(row, field_x, VERT, muted)
        sc.put(row, field_x + field_w - 1, VERT, muted)
    return 3 if boxed else 1


def draw_boxed_table(sc: Screen, view, top: int, left: int, width: int,
                     height: int, offset: int, title: str,
                     search: Search = None, search_tab: int = None) -> int:
    """A table inside its own titled panel, filling the width it is given.
    Returns the body's row capacity."""
    has_search = search is not None and search_tab in SEARCHABLE
    if height < 6:                       # box, header, rule, total, one row
        if has_search and height > 2:
            draw_search_input(sc, search, search_tab, top, left, width)
            return draw_table(sc, view, top + 1, left, width, height - 1,
                              offset)
        return draw_table(sc, view, top, left, width, height, offset)
    panel(sc, top, left, width, height, title)
    # A roomy table gets a three-row input box; compact tables keep a one-row
    # control so at least a couple of records remain visible.
    boxed = has_search and height >= 10
    input_rows = 3 if boxed else 1
    minimum = 4 + input_rows if has_search else 4
    x, y, w, h = inside(left, top, width, height, min_content=minimum,
                         pad_top=not has_search, pad_bottom=has_search)
    if has_search:
        used = draw_search_input(sc, search, search_tab, y, x, w, boxed)
        y, h = y + used, h - used
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
        # Padded to what is visible rather than to the column: a cell cut off
        # by the border still has to stop at it, and ljust to the full width
        # writes its own trailing spaces straight over the frame.
        room = min(w, edge - x)
        text = fit(cell, room)
        put_label(sc, y, x,
                  text.rjust(room) if align == ">" else text.ljust(room), attr)
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
    # them they take the whole width rather than leaving a ragged edge. The
    # threshold is where the Models panel stops fitting a model's full name
    # beside its figures: the stats panel's floor, the gap, and what a row
    # of models needs.
    stacked = width < 68
    stats_w = width if stacked else max(30, width // 3)
    models_w = width if stacked else width - stats_w - 2
    # Every box on this tab is sized to the room actually left below it,
    # footer row excluded. A box drawn past the last line doesn't scroll --
    # it loses its bottom edge, and the footer lands inside it.
    stats_h = min(box_for(4), max(0, sc.h - y - 1))   # the four facts below
    box_h = box_for(min(len(models), 5))
    if not stacked:
        # Beside each other the two boxes close on the same line: whichever
        # is shorter grows into the other's depth, rather than leaving a
        # step across the middle of the tab. Which one that is depends on
        # the ledger, so both are sized from the taller of the pair.
        stats_h = box_h = min(max(stats_h, box_h), max(0, sc.h - y - 1))

    facts = ((f"{data.tasks:,} Tasks", accent | curses.A_BOLD),
             (data.span, muted),
             (f"{ledger.fmt_tokens(data.tokens)} Tokens", 0),
             (ledger.fmt_usd(data.cost, data.cost_known),
              accent | curses.A_BOLD))
    if stats_h >= 3:
        panel(sc, y, left, stats_w, stats_h, "Project")
        bx, by, bw, bh = inside(left, y, stats_w, stats_h)
        for i, (fact, attr) in enumerate(facts[:max(0, bh)]):
            sc.put_in(by + i, bx, fact, bx + bw, attr)

    models_y = y + stats_h if stacked else y
    models_x = left if stacked else left + stats_w + 2
    # Sized to the room that is actually left, footer row excluded. A box
    # drawn past the last line doesn't scroll -- it loses its bottom edge,
    # and the footer lands in the middle of the rows it was holding.
    box_h = min(box_h, sc.h - models_y - 1)
    if box_h >= 3:
        panel(sc, models_y, models_x, models_w, box_h, "Models")
        mx, my, mw, mh = inside(models_x, models_y, models_w, box_h)
        shown = models[:max(0, mh)]
        # A panel squeezed to nothing draws nothing: max() over an empty
        # column is a crash, and a header with no rows under it is a bug.
        if not shown:
            return 0
        # What a row of this width can hold, and how wide its name column
        # runs: report.model_rows asks the same question of the same place,
        # so the two panels agree down to the column.
        name_w, tallies, counts, costs = views.model_figures(shown, mw)
        cost_w = max(len(c) for c in costs)
        count_w = max(len(c) for c in counts)
        label_w, count_x, cost_x = views.figure_slots(mw, count_w, cost_w)
        for i, b in enumerate(shown):
            sc.put_in(my + i, mx, f"{b['key']:<{name_w}}{tallies[i]}",
                      mx + label_w)
            sc.put_in(my + i, mx + count_x, counts[i].rjust(count_w),
                      mx + mw, curses.color_pair(C_MUTED))
            sc.put_in(my + i, mx + cost_x, costs[i].rjust(cost_w),
                      mx + mw, curses.A_BOLD)
    y = (models_y + box_h + 1) if stacked else (y + max(stats_h, box_h) + 1)

    days = figures.days[-(max(3, sc.h - y - 12)):]
    chart_h = min(box_for(len(days)), max(0, sc.h - y - 8))
    if days and chart_h >= 3:
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
        # Sized from every day rather than the ones that fit, the way peak
        # already is: a column that changes width as rows scroll past it is a
        # column you cannot read down, and it would put this panel a space
        # out from the same panel printed by report.py.
        costs = [ledger.fmt_usd(b["cost"], b["cost_known"]) for b in days]
        counts = [ledger.fmt_tokens(views.total_tokens(b)) for b in days]
        cost_w = max(len(c) for c in costs)
        count_w = max(len(c) for c in counts)
        first = len(days) - len(shown)
        label_w, count_x, cost_x = views.figure_slots(cw, count_w, cost_w)
        # The date, the space after it, and the two edges around the bar.
        # Whatever is left is the bar; a panel with no room for one shows the
        # date and its figures, which is still the row.
        room = max(0, label_w - 8)
        for i, b in enumerate(shown):
            filled, rest = bar(b["cost"], peak, room)
            sc.put_in(cy + i, cx, b["key"][5:], cx + label_w, muted)
            if room:
                sc.put_in(cy + i, cx + 6, LEFT_EDGE, cx + label_w, accent)
                sc.put_in(cy + i, cx + 7, filled, cx + label_w, accent)
                sc.put_in(cy + i, cx + 7 + len(filled), rest, cx + label_w,
                          muted)
                sc.put_in(cy + i, cx + 7 + room, RIGHT_EDGE, cx + label_w,
                          accent)
            sc.put_in(cy + i, cx + count_x, counts[first + i].rjust(count_w),
                      cx + cw, muted)
            sc.put_in(cy + i, cx + cost_x, costs[first + i].rjust(cost_w),
                      cx + cw, curses.A_BOLD)
        y += chart_h + 1

    tasks = figures.tasks
    box_h = min(box_for(len(tasks)), max(0, sc.h - y - 2))
    if tasks and box_h >= 3:
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
        label_w, count_x, cost_x = views.figure_slots(tw, count_w, cost_w,
                                                      views.FIGURE_GAP)
        for i, b in enumerate(shown):
            put_label(sc, ty + i, tx,
                      views.label_of(b, label_w, views.UNKNOWN_LONG))
            sc.put_in(ty + i, tx + count_x, counts[i].rjust(count_w),
                      tx + tw, curses.color_pair(C_MUTED))
            sc.put_in(ty + i, tx + cost_x, costs[i].rjust(cost_w), tx + tw,
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
        tail_x = max(0, w - len(tail))
        sc.put_in(y, x, f"{n:,} Sessions", x + tail_x, accent | curses.A_BOLD)
        sc.put_in(y, x + tail_x, tail, x + w, curses.A_BOLD)
        if tail_x - 3 > 14 + len(mid):
            sc.put_in(y, x + tail_x - 3 - len(mid), mid, x + tail_x, muted)

    # The two outliers, named like the table below names its rows: when
    # they started and what they were opened with, figure hard right. Each
    # field stops where the next one starts, so a long prompt runs out of
    # room rather than over the figure or the panel's own border.
    figs = [ledger.fmt_usd(priciest["cost"], priciest["cost_known"]),
            f"{ledger.fmt_tokens(views.total_tokens(longest))} Tokens"]
    fig_w = max(len(f) for f in figs)
    fig_x = max(0, w - fig_w)
    label_x = 9 + 12
    room = max(0, fig_x - label_x - 2)
    for i, (tag, b, fig) in enumerate(
            (("Priciest", priciest, figs[0]), ("Longest", longest, figs[1]))):
        if 1 + i >= h:
            break
        row = y + 1 + i
        sc.put_in(row, x, tag, x + min(9, fig_x), accent)
        sc.put_in(row, x + 9, views.started(b), x + max(9, min(label_x, fig_x)),
                  muted)
        put_label(sc, row, x + label_x,
                  views.label_of(b, room, views.UNKNOWN_LONG))
        sc.put_in(row, x + w - len(fig), fig, x + w, curses.A_BOLD)


def draw_tab(sc: Screen, data: Data, tab: int, top: int, offset: int,
             search: Search = None):
    """Draw the active tab. Returns (visible rows, total rows) for scrolling."""
    label, mode, _ = TABS[tab]
    if mode == "overview":
        return draw_overview(sc, data, top, offset), 0

    query = search.query(tab) if search is not None else ""
    built = data.tab(tab, query)
    view, summary, main = built.view, built.summary, built.main
    left, width = 2, sc.w - 4
    room = sc.h - top - 1

    if not view.buckets:
        if built.query:
            height = min(room, box_for(4))
            panel(sc, top, left, width, height, label)
            nx, ny, nw, _ = inside(left, top, width, height, min_content=4,
                                   pad_top=False, pad_bottom=True)
            boxed = height >= 8
            used = draw_search_input(sc, search, tab, ny, nx, nw, boxed)
            message = f'No {SEARCHABLE[tab].lower()} match "{built.query}".'
            sc.put_in(ny + used, nx, message, nx + nw,
                      curses.color_pair(C_MUTED))
        else:
            panel(sc, top, left, width, box_for(1), label)
            nx, ny, nw, _ = inside(left, top, width, box_for(1))
            message = caps(
                f"Nothing recorded in this window ({built.scope}).")
            sc.put_in(ny, nx, message, nx + nw, curses.color_pair(C_MUTED))
        return 0, 0

    if summary is None:
        split_h = box_for(3)               # count row plus the two outliers
    else:
        split_h = box_for(len(summary.buckets) + 3)  # header, rows, rule, total

    searchable = tab in SEARCHABLE and search is not None
    if not main.buckets or room < split_h + (11 if searchable else 8):
        shown = draw_boxed_table(sc, view, top, left, width, room, offset,
                                 view.subtitle, search, tab)
        return shown, len(view.buckets)

    if summary is None:
        draw_sessions_summary(sc, view, top, left, width, split_h)
    else:
        draw_boxed_table(sc, summary, top, left, width, split_h, 0,
                         built.summary_title)
    below = top + split_h + 1
    shown = draw_boxed_table(sc, main, below, left, width,
                             sc.h - below - 1, offset, built.main_title,
                             search, tab)
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
                           (short, NAV_GAP - 1), (short, 1)):
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


def footer_for(tab: int, search: Search) -> str:
    """The key legend. The search is the field's business, not the footer's.

    Blank while the field has focus, because every key on the legend is
    being typed into the query instead. Once a query is applied the legend
    comes back without Esc on it: there, Esc clears the filter.
    """
    if search.editing == tab:
        return ""
    return FILTER_KEYS if search.query(tab) else KEYS


def draw_chrome(sc: Screen, data: Data, tab: int, search: Search) -> int:
    """Masthead, then the tab bar. Returns the first body row."""
    accent = curses.color_pair(C_ACCENT)
    muted = curses.color_pair(C_MUTED)
    left, width = 2, sc.w - 4

    if sc.h < 20:
        # No room for frames: name the app, the project and the tabs.
        where = max(left, sc.w - len(data.project) - 2)
        sc.put_in(0, left, "token-cost", where, accent | curses.A_BOLD)
        sc.put_in(0, where, data.project, sc.w, muted)
        draw_nav(sc, tab, 1, left, width)
        sc.put(sc.h - 1, 2, fit(footer_for(tab, search), sc.w - 4), muted)
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

    sc.put(sc.h - 1, 2, fit(footer_for(tab, search), sc.w - 4), muted)
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
    search = Search()
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
            sc.search_hit = None
            top = draw_chrome(sc, data, tab, search)
            if not data.rows:
                # The same two-column margin the chrome keeps, so a long
                # project name is cut where every other line ends.
                sc.put_in(top, 2,
                          f"No Token Usage Recorded Yet For {data.project}.",
                          sc.w - 2)
                sc.put_in(top + 2, 2, "Finish a Task and Press r.", sc.w - 2,
                          curses.color_pair(C_MUTED))
                capacity = total = 0
            else:
                capacity, total = draw_tab(sc, data, tab, top, offset, search)

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
            event = stdscr.get_wch()
        except KeyboardInterrupt:
            # Ctrl+C is how plenty of people close a full-screen app. Quit
            # the way q does, rather than unwinding a traceback over a
            # terminal that curses is still holding.
            return
        text = event if isinstance(event, str) else None
        key = ord(event) if isinstance(event, str) else event
        page = max(1, capacity - 1)
        if key != 27:
            handled, changed = search.input(tab, key, text)
            if handled:
                if changed:
                    offset = 0
                continue

        if key == 27:
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
                        search.commit()
                        tab, offset = target, 0
                    elif sc.search_target(*sc.mouse) == tab:
                        search.begin(tab)
                        offset = 0
            else:
                stdscr.nodelay(True)
                follower = stdscr.getch()
                while stdscr.getch() != -1:
                    pass
                stdscr.nodelay(False)
                if follower == -1:
                    if search.editing == tab:
                        search.cancel()
                        offset = 0
                    elif search.query(tab):
                        search.clear(tab)
                        offset = 0
                    else:
                        return
        elif key in (ord("q"), ord("Q")):
            return
        elif key == ord("/") and tab in SEARCHABLE:
            search.begin(tab)
            offset = 0
        elif key in (curses.KEY_RIGHT, ord("\t"), ord("l")):
            search.commit()
            tab, offset = (tab + 1) % len(TABS), 0
        elif key in (curses.KEY_LEFT, curses.KEY_BTAB, ord("h")):
            search.commit()
            tab, offset = (tab - 1) % len(TABS), 0
        elif ord("1") <= key <= ord("6"):
            search.commit()
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
                    search.commit()
                    tab, offset = target, 0
                elif sc.search_target(my, mx) == tab:
                    search.begin(tab)
                    offset = 0


def main() -> int:
    args = sys.argv[1:]
    cwd = str(Path.cwd())
    if "--cwd" in args:
        cwd = args[args.index("--cwd") + 1]
    if any(a.lower() in views.HTML_WORDS for a in args):
        # `token-cost html`: the same ledger, in the browser instead. It
        # needs no terminal, so it is answered before the TTY check below.
        import html_report
        return html_report.cli(cwd)
    if not sys.stdout.isatty():
        print("token-cost: the UI needs a terminal. Run it from your shell,"
              " or use /token-cost for the chat overview.", file=sys.stderr)
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
