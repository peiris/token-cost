#!/usr/bin/env python3
"""Print what the TUI actually has on screen, as curses sees it.

Replaying a byte stream is guesswork -- curses addresses rows implicitly and
erases with sequences a toy emulator gets wrong. `instr()` reads the cells
back out of curses itself, so this is ground truth for what a real terminal
would be showing. Used by smoke_tui.py; run it under a pty.

Usage: python3 dev/dump_frame.py --cwd PATH --tab N [--all]

`--all` prints every tab up to --tab rather than only the last one, each
behind its own sentinel. One process, one terminal size, six frames: the
checks that sweep sizes pay for a pty once instead of once per tab.
"""

from __future__ import annotations

import curses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import tui  # noqa: E402


def capture(stdscr, cwd: str, tab: int, every: bool = False) -> list:
    stdscr.keypad(True)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        for pair in (tui.C_ACCENT, tui.C_MUTED, tui.C_HEAD, tui.C_TOTAL):
            curses.init_pair(pair, curses.COLOR_YELLOW, -1)

    data = tui.Data(cwd)
    screen = tui.Screen(stdscr)
    search = tui.Search()

    # Draw every tab up to the requested one, the way arrowing there would,
    # so this catches anything a previous frame fails to clean up.
    frames = []
    for step in range(tab + 1):
        screen.measure()
        stdscr.erase()
        top = tui.draw_chrome(screen, data, step, search)
        if data.rows:
            tui.draw_tab(screen, data, step, top, 0, search)
        else:
            screen.put(top, 2, f"No token usage recorded yet for {data.project}.")
            screen.put(top + 2, 2, "Finish a task and press r.")
        stdscr.refresh()
        if every or step == tab:
            # instr's limit counts bytes, not cells, so a row of box-drawing
            # glyphs comes back cut to a third of its width unless we ask for
            # the worst case.
            frames.append([
                stdscr.instr(y, 0, screen.w * 4).decode("utf-8", "replace")
                .rstrip() for y in range(screen.h)])
    return frames


def main() -> int:
    args = sys.argv[1:]
    cwd = args[args.index("--cwd") + 1] if "--cwd" in args else str(Path.cwd())
    tab = int(args[args.index("--tab") + 1]) if "--tab" in args else 0
    frames = curses.wrapper(capture, cwd, tab, "--all" in args)
    # A sentinel before each, because everything curses painted on the way
    # here is still sitting in the pty's byte stream ahead of this.
    for lines in frames:
        print("\n<<<FRAME>>>")
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
