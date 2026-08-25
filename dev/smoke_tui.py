#!/usr/bin/env python3
"""Drive the TUI under a pseudo-terminal and check that it works.

The UI needs a TTY, which means it can't be exercised the way the rest of the
plugin can. So give it one: allocate a pty, run the app inside it, send the
keys a person would, and read back what it paints.

What this can prove: it starts, every tab draws its own content, scrolling and
refresh don't crash it, it survives sizes from a postage stamp to a wall, and
it exits cleanly. What it can't: whether the thing looks good.

Usage: python3 dev/smoke_tui.py [--cwd PATH] [--show TAB] [--rows N --cols N]
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time
from pathlib import Path

TUI = Path(__file__).resolve().parent.parent / "scripts" / "tui.py"
TABS_FIRST = "Overview"
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-B0-9]|\x1b[=>]|\r")

# Arrow keys in *application* cursor mode (ESC O x), which is what a terminal
# sends once curses calls keypad(True). The normal-mode ESC [ x form is not in
# xterm's terminfo for cursor keys and arrives as three unrelated keypresses.
RIGHT, LEFT, UP, DOWN = b"\x1bOC", b"\x1bOD", b"\x1bOA", b"\x1bOB"
PGDN, PGUP = b"\x1b[6~", b"\x1b[5~"

# (keys, what a roomy terminal must show, what a cramped one must show).
# None means the step only has to not break anything: scrolling a short list
# or refreshing unchanged data legitimately paints nothing at all. The two
# expectations differ because a 12-row terminal has no room for a chart --
# it still has to name the tab you are on, which is what a cramped screen
# owes you.
STEPS = [
    (RIGHT, "Today", "Today"),
    (RIGHT, "This Week", "This Week"),
    (RIGHT, "This Month", "This Month"),
    (RIGHT, "Every Task", "Every Task"),
    (RIGHT, "Sessions", "Sessions"),
    (RIGHT, "Cost Per Day", "Overview"),      # wraps back to the start
    (LEFT, "Sessions", "Sessions"),
    (b"5", "Every Task", "Every Task"),
    (DOWN * 5, None, None),
    (UP, None, None),
    (PGDN, None, None),
    (PGUP, None, None),
    (b"G", None, None),
    (b"g", None, None),
    (b"r", None, None),
    (b"1", "Cost Per Day", "Overview"),
]

# Ctrl+C. Sent instead of `q` in one pass, because a full-screen app that
# prints a traceback over the terminal it was holding is a bad way to leave.
INTERRUPT = b"\x03"


class App:
    """The TUI running in a pty."""

    def __init__(self, cwd: str, rows: int, cols: int):
        self.rows, self.cols = rows, cols
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.environ["TERM"] = "xterm-256color"
            os.execv(sys.executable, [sys.executable, str(TUI), "--cwd", cwd])
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, 0, 0))
        self.buffer = ""
        self.raw = ""

    def read(self, seconds: float) -> str:
        chunks, deadline = [], time.time() + seconds
        while time.time() < deadline:
            ready, _, _ = select.select([self.fd], [], [], 0.05)
            if ready:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
        raw = b"".join(chunks).decode("utf-8", "replace")
        self.raw += raw
        text = ANSI.sub("", raw)
        self.buffer += text
        return text

    def send(self, keys: bytes) -> None:
        os.write(self.fd, keys)

    def screen(self) -> str:
        """What the terminal is currently showing."""
        return paint(self.raw, self.rows, self.cols)

    def wait_for(self, text: str, seconds=3.0) -> bool:
        """Drain until `text` is on screen.

        Checked against the painted grid, not the byte stream: curses rewrites
        only the characters that changed, so moving from "Today" to "This
        Week" puts `his Week` on the wire and nothing that contains the tab's
        name. The screen is the only honest thing to assert against.
        """
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.read(0.15)
            if text in self.screen():
                return True
        return False

    def alive(self) -> bool:
        return os.waitpid(self.pid, os.WNOHANG) == (0, 0)

    def close(self) -> int:
        """Ask it to quit; return its exit status, killing it if it won't."""
        try:
            self.send(b"q")
        except OSError:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self._shut()
                return os.waitstatus_to_exitcode(status)
            self.read(0.1)
        os.kill(self.pid, signal.SIGKILL)
        os.waitpid(self.pid, 0)
        self._shut()
        return -1

    def _shut(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


# --------------------------------------------------------------------------
# a screen, so a frame can be looked at
# --------------------------------------------------------------------------

CSI = re.compile(r"\x1b\[([0-9;?]*)([a-zA-Z])")


def paint(stream: str, rows: int, cols: int) -> str:
    """Replay curses output onto a grid and return what the screen shows.

    curses repaints by moving the cursor and rewriting only what changed, so
    the raw byte stream is not a picture of anything -- old frames and new
    overlap in it. Interpreting the handful of sequences curses actually
    emits (absolute moves, column moves, clears) is the difference between
    guessing at a layout and seeing it.
    """
    grid = [[" "] * cols for _ in range(rows)]
    row = col = 0
    i = 0
    while i < len(stream):
        ch = stream[i]
        if ch == "\x1b":
            match = CSI.match(stream, i)
            if not match:
                # ESC ( B and ESC ) 0 select a charset and are three bytes;
                # ESC = and ESC > are two. Skipping the wrong count leaks the
                # trailing letter into the grid as a stray glyph.
                i += 3 if stream[i + 1:i + 2] in "()" else 2
                continue
            args, final = match.group(1), match.group(2)
            nums = [int(n) for n in args.split(";") if n.isdigit()]
            if final == "H":
                row = (nums[0] - 1) if nums else 0
                col = (nums[1] - 1) if len(nums) > 1 else 0
            elif final == "G":
                col = (nums[0] - 1) if nums else 0
            elif final == "J" and nums and nums[0] == 2:
                grid = [[" "] * cols for _ in range(rows)]
            elif final == "K":
                for x in range(col, cols):
                    grid[row][x] = " "
            elif final == "X":          # erase n characters, cursor stays put
                for x in range(col, min(col + (nums[0] if nums else 1), cols)):
                    grid[row][x] = " "
            elif final == "P":          # delete n characters, rest shifts left
                n = nums[0] if nums else 1
                line = grid[row][:col] + grid[row][col + n:] + [" "] * n
                grid[row] = line[:cols]
            elif final in "AB":
                row += (nums[0] if nums else 1) * (-1 if final == "A" else 1)
            elif final in "CD":
                col += (nums[0] if nums else 1) * (-1 if final == "D" else 1)
            row, col = max(0, min(row, rows - 1)), max(0, min(col, cols - 1))
            i = match.end()
            continue
        if ch == "\r":
            col = 0
        elif ch == "\n":
            row, col = min(row + 1, rows - 1), 0
        elif ch >= " ":
            if col < cols:
                grid[row][col] = ch
            col += 1
        i += 1
    return "\n".join("".join(line).rstrip() for line in grid)


def ledger_has_rows(cwd: str) -> bool:
    sys.path.insert(0, str(TUI.parent))
    import ledger
    return bool(ledger.read_ledger(ledger.ledger_path(cwd)))


def broke(frame: str) -> str:
    """A real crash on screen, as opposed to one being talked about.

    Task labels are the user's own prompts, and a prompt can contain the
    word Traceback -- this project's own ledger has several. So match the
    shape of an actual Python failure, which is the header immediately
    above a File line, rather than any one word.
    """
    if "Traceback (most recent call last):" in frame and '  File "' in frame:
        return frame[-500:]
    if "curses.error" in frame and '  File "' in frame:
        return frame[-500:]
    return ""


def run_size(cwd: str, rows: int, cols: int, empty=False) -> list:
    problems = []
    cramped = rows < 20 or cols < 70
    app = App(cwd, rows, cols)
    # The first tab's name, not the app's: the masthead is wording someone
    # may well change, while a tab has to be on screen for the UI to work
    # at all -- in the full nav, the compact indicator, or the slim line.
    if not app.wait_for(TABS_FIRST):
        problems.append(f"{rows}x{cols}: never drew a first frame")
        app.close()
        return problems

    if empty:
        # A project with no ledger has nothing to chart. What it owes you is
        # an explanation, on every tab, without falling over.
        if not app.wait_for("No token usage recorded yet"):
            problems.append(f"{rows}x{cols}: no empty-state message")
        for keys, _, _ in STEPS:
            app.send(keys)
            app.read(0.2)
            if not app.alive():
                problems.append(f"{rows}x{cols}: died on empty ledger after {keys!r}")
                break
        status = app.close()
        if status != 0:
            problems.append(f"{rows}x{cols}: exited {status}, expected 0")
        if found := broke(app.buffer):
            problems.append(f"{rows}x{cols}: crashed\n{found}")
        return problems

    for keys, roomy, tight in STEPS:
        expect = tight if cramped else roomy
        app.send(keys)
        if expect:
            if not app.wait_for(expect):
                problems.append(f"{rows}x{cols}: {expect!r} never appeared")
        else:
            app.read(0.3)
        if not app.alive():
            problems.append(f"{rows}x{cols}: died after {keys!r}")
            break

    if problems and (found := broke(app.buffer)):
        problems.append(f"{rows}x{cols} output:\n{found}")

    status = app.close()
    if status != 0:
        problems.append(f"{rows}x{cols}: exited {status}, expected 0")
    if found := broke(app.buffer):
        problems.append(f"{rows}x{cols}: crashed\n{found}")

    # And again, leaving by Ctrl+C rather than by q.
    app = App(cwd, rows, cols)
    if app.wait_for(TABS_FIRST):
        app.send(INTERRUPT)
        time.sleep(0.6)
        status = app.close()
        if status != 0:
            problems.append(f"{rows}x{cols}: Ctrl+C exited {status}, expected 0")
        if found := broke(app.buffer):
            problems.append(f"{rows}x{cols}: Ctrl+C left a traceback\n{found}")
    else:
        app.close()
    return problems


# The band under the hovered row, by its colours: background 237 is used by
# nothing else the UI paints, so its appearance on the wire is the hover
# working, and nothing else is.
HOVER_BG = "48;5;237"


def x10(code: int, x: int, y: int) -> bytes:
    """An X10-encoded mouse report: three bytes, everything offset by 32."""
    return b"\x1b[M" + bytes([32 + code, 32 + x, 32 + y])


def sgr(y: int) -> bytes:
    """An SGR motion report at column 12, row y."""
    return b"\x1b[<35;12;%dM" % y


def sgr_click(x: int, y: int) -> bytes:
    """An SGR left-button press at one-based terminal coordinates."""
    return b"\x1b[<0;%d;%dM" % (x, y)


def text_position(app: App, text: str):
    """One-based coordinates in the middle of visible `text`."""
    for y, line in enumerate(app.screen().splitlines()):
        if text in line:
            return line.index(text) + len(text) // 2 + 1, y + 1
    return None


def compact_controls(app: App, place: str):
    """One-based positions of the compact nav's left and right arrows."""
    for y, line in enumerate(app.screen().splitlines()):
        if place not in line:
            continue
        occupied = [x for x, char in enumerate(line) if not char.isspace()]
        if occupied:
            return (occupied[0] + 1, y + 1), (occupied[-1] + 1, y + 1)
    return None


def resting_row(app: App, report) -> int:
    """A screen row that lights when the pointer comes to rest on it, or 0.

    Which rows are table rows depends on the layout, which depends on the
    data -- so find one rather than name one, moving a cell at a time and
    letting each land, the way a hand moves a mouse.
    """
    for y in range(6, 38):
        seen = app.raw.count(HOVER_BG)
        app.send(report(y))
        app.read(0.08)
        if app.raw.count(HOVER_BG) > seen:
            return y
    return 0


def run_mouse(cwd: str) -> list:
    """Hover and wheel, in both encodings a terminal might use.

    SGR reports are what any terminal of this decade sends once the app asks
    for them; on Apple's ncurses they reach the input loop as loose bytes,
    which is the path this exercises. X10 is the older encoding, the one
    ncurses parses itself and hands over as KEY_MOUSE. A row has to light
    under either.
    """
    problems = []
    app = App(cwd, 40, 140)
    if not app.wait_for(TABS_FIRST):
        app.close()
        return ["mouse: never drew a first frame"]
    if "\x1b[?1003h" not in app.raw or "\x1b[?1006h" not in app.raw:
        problems.append("mouse: motion tracking was never switched on")

    # Tab presses take both paths the TUI supports: SGR reports are decoded
    # by the app on older ncurses, while X10 reports arrive as KEY_MOUSE.
    today = text_position(app, "Today")
    if today is None:
        problems.append("mouse: could not locate Today tab")
    else:
        app.send(sgr_click(*today))
        if not app.wait_for("Today,"):
            problems.append("mouse: SGR click did not open Today")

    week = text_position(app, "This Week")
    if week is None:
        problems.append("mouse: could not locate This Week tab")
    else:
        app.send(x10(0, *week) + x10(3, *week))
        if not app.wait_for("Last 7 Days"):
            problems.append("mouse: X10 click did not open This Week")

    app.send(b"5")                      # two tables on screen to hover over
    app.wait_for("Every Task")

    row = resting_row(app, sgr)
    if not row:
        problems.append("mouse: SGR motion lit no row")
    x10_row = resting_row(app, lambda y: x10(35, 12, y))  # 32 motion, 3 no button
    if not x10_row:
        problems.append("mouse: X10 motion lit no row")

    # A flick of the mouse crosses more cells than there are frames to draw
    # them in, and the UI answers where the pointer ended up rather than
    # painting every cell it passed over. What has to hold is that it lands
    # lit on the row the pointer stopped on.
    row = row or x10_row
    if row:
        app.send(sgr(1))                # off the table: the band goes out
        app.read(0.3)
        seen = app.raw.count(HOVER_BG)
        for y in range(6, row + 1):     # sent as fast as the pty will take it
            app.send(sgr(y))
        app.read(1.0)
        if app.raw.count(HOVER_BG) <= seen:
            problems.append("mouse: a fast sweep left the resting row unlit")

    for _ in range(4):                  # wheel: SGR both ways, X10 up
        app.send(b"\x1b[<65;12;20M" + b"\x1b[<64;12;20M" + x10(64, 12, 20))
    app.read(0.6)
    if not app.alive():
        problems.append("mouse: died on wheel events")

    # A CSI arrow -- Esc-led, but no mouse report -- must fall through
    # mouse_report() into the swallow, not the quit.
    app.send(b"\x1b[C")
    app.read(0.4)
    if not app.alive():
        problems.append("mouse: a CSI arrow quit the app")

    # A bare Esc must still quit: mouse_report() waits out its 25ms, finds
    # nothing, and hands the silence back to the quit check.
    app.send(b"\x1b")
    ended = None
    deadline = time.time() + 3.0
    while time.time() < deadline:
        pid, status = os.waitpid(app.pid, os.WNOHANG)
        if pid:
            ended = os.waitstatus_to_exitcode(status)
            break
        app.read(0.1)
    app.read(0.3)                       # the shutdown's own bytes
    if ended is None:
        problems.append("mouse: bare Esc no longer quits")
        os.kill(app.pid, signal.SIGKILL)
        os.waitpid(app.pid, 0)
    elif ended != 0:
        problems.append(f"mouse: Esc exited {ended}, expected 0")
    app._shut()
    if "\x1b[?1003l" not in app.raw or "\x1b[?1006l" not in app.raw:
        problems.append("mouse: tracking left switched on at exit")
    if found := broke(app.buffer):
        problems.append(f"mouse: crashed\n{found}")
    return problems


def run_compact_mouse(cwd: str) -> list:
    """The compact nav exposes arrow buttons in place of all six tabs."""
    problems = []
    app = App(cwd, 12, 40)
    if not app.wait_for(TABS_FIRST):
        app.close()
        return ["compact mouse: never drew a first frame"]

    controls = compact_controls(app, "1/6")
    if controls is None:
        problems.append("compact mouse: could not locate arrow buttons")
    else:
        _, right = controls
        app.send(sgr_click(*right))
        if not app.wait_for("Today"):
            problems.append("compact mouse: right arrow did not open Today")

        controls = compact_controls(app, "2/6")
        if controls is None:
            problems.append("compact mouse: could not locate updated arrows")
        else:
            left, _ = controls
            app.send(x10(0, *left) + x10(3, *left))
            if not app.wait_for(TABS_FIRST):
                problems.append("compact mouse: left arrow did not return")

    status = app.close()
    if status != 0:
        problems.append(f"compact mouse: exited {status}, expected 0")
    if found := broke(app.buffer):
        problems.append(f"compact mouse: crashed\n{found}")
    return problems


def show(cwd: str, tab: int, rows: int, cols: int) -> None:
    """Print one frame, so a layout can be eyeballed from outside a terminal."""
    app = App(cwd, rows, cols)
    app.wait_for(TABS_FIRST)
    if tab:
        app.send(str(tab + 1).encode())
        app.read(1.2)
    app.read(0.4)
    frame = app.screen()
    app.close()
    print(frame)


def main() -> int:
    args = sys.argv[1:]
    cwd = str(Path.cwd())
    if "--cwd" in args:
        cwd = args[args.index("--cwd") + 1]
    rows = int(args[args.index("--rows") + 1]) if "--rows" in args else 44
    cols = int(args[args.index("--cols") + 1]) if "--cols" in args else 150
    if "--show" in args:
        show(cwd, int(args[args.index("--show") + 1]), rows, cols)
        return 0

    empty = not ledger_has_rows(cwd)
    if empty:
        print("(no ledger for this project — checking the empty state)")

    problems = []
    for size in ((40, 140), (12, 40), (60, 200)):
        found = run_size(cwd, *size, empty=empty)
        print(f"{size[0]}x{size[1]}: {'FAIL' if found else 'ok'}")
        problems += found

    if not empty:                # an empty ledger has no rows to hover
        found = run_mouse(cwd)
        print(f"mouse: {'FAIL' if found else 'ok'}")
        problems += found
        found = run_compact_mouse(cwd)
        print(f"compact mouse: {'FAIL' if found else 'ok'}")
        problems += found

    for p in problems:
        print("\n" + p)
    print("\n" + ("PASS" if not problems else f"{len(problems)} problem(s)"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
