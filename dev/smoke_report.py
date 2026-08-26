#!/usr/bin/env python3
"""Check that the chat report fits its pane, and is the UI it claims to be.

Two properties, and neither is about how it looks.

It has to fit. Every panel and every table lands in a pane whose width
nothing here controls, and a single line one column too long wraps -- which
takes the frame it belongs to with it, and the one below that. So render at
every width from the floor to well past the ceiling and measure: nothing
over the width, no frame stopping short of its own border, and narrowing
that costs columns rather than rows.

And it has to agree. The report is the UI printed, so every line the UI
draws at a given width has to be a line the report prints at that width --
same panels, same titles, same columns, same cells. Not the reverse: the
report has no bottom, so it shows rows the terminal had no room for.

Fitting is checked against synthetic ledgers as well as this project's, so
it says the same thing on a machine with no usage recorded and on one with
months of it. Agreement is checked against a frozen copy of the real one,
because the plugin records a row every time a task finishes -- including the
tasks running this check.

Usage: python3 dev/smoke_report.py [--cwd PATH] [--show N]
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import ledger  # noqa: E402
import report  # noqa: E402
import views  # noqa: E402

# Long enough to be clipped, wide enough to push the figures around: what a
# row has to survive is a prompt with no short form.
PROMPT = ("Can we migrate the output to the TUI instead of the plain table"
          " we have now, and keep every column")


def make_rows(days: int = 9, models: int = 6) -> list[dict]:
    """A ledger with enough shape to exercise every panel."""
    names = ["claude-opus-5", "claude-opus-5 (1m)", "claude-fable-5",
             "claude-fable-5 (1m)", "claude-sonnet-5",
             "claude-haiku-4-5-20251001"][:models]
    rows = []
    for day in range(days):
        for turn, name in enumerate(names):
            rows.append({
                "ts": f"2026-08-{day + 1:02d}T12:00:00Z",
                "session": f"session-{day}", "turn": turn,
                "model": name, "prompt": PROMPT[:12 + turn * 9],
                "input": 900 * (turn + 1), "output": 4000 * (day + 1),
                "cache_read": 1_500_000 * (day + 1) * (turn + 1),
                "cache_write_5m": 90_000, "cache_write_1h": 0,
                "ctx": 190_000, "cost_usd": 1.5 * (day + 1) * (turn + 1),
            })
    return rows


def ragged_frames(drawn: list[str], width: int, where: str) -> list[str]:
    """Frame lines that stop short of their own right-hand border.

    Inside a full-width frame every line has to reach it: one that stops
    early lost its edge, which reads as a broken box even though nothing
    wrapped. The paired Project/Models row is exempt -- two frames of their
    own widths with a gutter between them, and the shorter one runs out
    under the taller.
    """
    ragged, inside = [], False
    for i, line in enumerate(drawn, 1):
        if line.startswith("╭"):
            inside = len(line) == width and "╮" not in line[:-1]
        if inside and len(line) != width:
            ragged.append(i)
        elif line.startswith("╰") and len(line) == width:
            inside = False
    return [f"{where}: short frame line(s) at {ragged[:3]}"] if ragged else []


def tables_ok(rows: list[dict], scope: str = "") -> list[str]:
    """The plain tables land in the same pane, and overrun it the same way."""
    problems = []
    for mode in views.MODES:
        view = views.build(rows, mode, scope)
        for width in range(report.MIN_WIDTH, report.MAX_WIDTH + 21):
            drawn = report.render(view, width).split("\n")
            over = [(i, len(line)) for i, line in enumerate(drawn, 1)
                    if len(line) > width]
            if over:
                problems.append(f"{mode} at {width}: {len(over)} line(s)"
                                f" overrun, first at {over[0][0]}"
                                f" ({over[0][1]} cells)")
            if len(drawn) != len(view.buckets) + 3:   # header, rule, total
                problems.append(f"{mode} at {width}: {len(drawn)} lines for"
                                f" {len(view.buckets)} buckets -- rows are"
                                " never dropped to make a table fit")
    return problems


# Every (mode, period) a report can be asked for: the five that are tabs, the
# two that are narrowings no tab offers, and the bare day table.
REPORTS = (("days", None), ("models", "today"), ("days", "week"),
           ("days", "month"), ("tasks", None), ("sessions", None),
           ("tasks", "week"), ("sessions", "month"))


def tabs_ok(rows: list[dict], project: str) -> list[str]:
    """Every report, boxed, at every width -- with and without a ceiling.

    The framed tabs carry a frame around a table around a prose column, and
    each of those can be the one that overruns. Both budget states, because
    the over-budget page is a different page: same chrome and summary, a
    table down to its header and its total, and the notice under it.
    """
    problems = []
    for width in range(report.MIN_WIDTH, report.MAX_WIDTH + 21):
        for mode, period in REPORTS:
            for budget in (0, 3000):
                drawn = report.tab_report(rows, project, mode, period, width,
                                          budget).split("\n")
                name = f"{mode}/{period} budget={budget} at {width}"
                over = [(i, len(line)) for i, line in enumerate(drawn, 1)
                        if len(line) > width]
                if over:
                    problems.append(f"{name}: {len(over)} line(s) overrun,"
                                    f" first at {over[0][0]} ({over[0][1]})")
                trailing = [i for i, line in enumerate(drawn, 1)
                            if line != line.rstrip()]
                if trailing:
                    problems.append(f"{name}: trailing space at"
                                    f" {trailing[:3]}")
                problems += ragged_frames(drawn, width, name)
    return problems


def widths_ok(rows: list[dict], project: str) -> list[str]:
    """Every width's complaints, empty when the report behaved."""
    problems = []
    for width in range(report.MIN_WIDTH, report.MAX_WIDTH + 21):
        drawn = report.overview(rows, project, width).split("\n")
        over = [(i, len(line)) for i, line in enumerate(drawn, 1)
                if len(line) > width]
        if over:
            problems.append(f"{width}: {len(over)} line(s) overrun,"
                            f" first at {over[0][0]} ({over[0][1]} cells)")
            continue
        problems += ragged_frames(drawn, width, str(width))
        trailing = [i for i, line in enumerate(drawn, 1)
                    if line != line.rstrip()]
        if trailing:
            problems.append(f"{width}: trailing space at {trailing[:3]}")
    return problems


# --------------------------------------------------------------------------
# the report against the UI it mirrors
# --------------------------------------------------------------------------

# What the UI has that a printed page can't: its own footer, and the search
# field with its box. Everything else on screen, the report owes you.
UI_ONLY = ("Click or ", "Click or Left", "Press / or click", "Filter:",
           "Search:")

# A paired row whose right-hand panel is blank filler. The two overview
# panels are different heights in the two frontends -- the UI's Models box
# stops at whatever the terminal had room for -- so which of them is padding
# on a given line legitimately differs.
BLANK_RIGHT = re.compile(r"^[╰│].*[╯│] {2}│ +│$")


# The rule under the masthead and the tab bar beneath it. The UI has a
# keyboard and five other tabs to reach with it; a printed report has
# neither, so it closes the masthead instead and these two rows are the
# UI's alone.
NAV_RULE = re.compile(r"^├─+┤$")


def ui_lines(frame: list[str]) -> list[str]:
    """The frame's content, less the parts only a live UI can have."""
    kept = []
    nav = False
    for line in frame:
        text = line[2:].rstrip() if line.startswith("  ") else line.rstrip()
        if nav:
            nav = False          # the tab bar, immediately under its rule
            continue
        if NAV_RULE.match(text):
            nav = True
            continue
        if (not text or any(mark in text for mark in UI_ONLY)
                or text.startswith(("│  ╭", "│  ╰"))
                or BLANK_RIGHT.match(text)):
            continue
        kept.append(text)
    return kept


def frozen_config() -> str:
    """A config dir holding one still copy of this project's ledger.

    The plugin records a row every time a task finishes, including the tasks
    running this check. A UI capture and a report render seconds apart would
    otherwise disagree about the totals, and the disagreement would look
    like a layout bug.
    """
    home = Path(tempfile.mkdtemp(prefix="token-cost-smoke-"))
    (home / "token-cost").mkdir(parents=True)
    real = Path(ledger.ledger_path(str(Path.cwd())))
    for source in (real, real.with_suffix(".format")):
        if source.is_file():
            shutil.copy2(source, home / "token-cost" / source.name)
    return str(home)


# The report and the UI draw the same bar out of different glyphs: ASCII
# where a model has to retype the page, block glyphs where a terminal draws
# it for free. Same cells either way, so compare the shape, not the
# codepoint. Applied to both sides, so anything that is not a bar -- a date,
# a task label -- still has to match itself exactly.
_BAR_ALIKE = str.maketrans({"▄": "#", "=": "#", "┈": ".", "-": ".",
                            "▕": "|", "▏": "|", "[": "|", "]": "|"})


def same_bar(line: str) -> str:
    return line.translate(_BAR_ALIKE)


def agrees_with_ui(cwd: str) -> list[str]:
    """Every line the UI draws has to be a line the report prints.

    Not the other way round: the report has no bottom, so it shows rows the
    terminal had no room for. But anything the UI does fit, at the same
    width, the report owes you the same way -- same panels, same titles,
    same columns, same cells. That is what "the report is the UI, printed"
    has to mean to be worth saying.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import smoke_tui

    was = os.environ.get("CLAUDE_CONFIG_DIR")
    home = frozen_config()
    os.environ["CLAUDE_CONFIG_DIR"] = home
    problems = []
    try:
        rows = ledger.read_ledger(ledger.ledger_path(cwd))
        if not rows:
            return ["no frozen ledger to compare against"]
        project = Path(cwd).resolve().name
        for term_rows, cols in ((60, 92), (48, 76), (40, 68)):
            width = cols - 4
            captured = smoke_tui.frames(cwd, term_rows, cols)
            for tab, frame in enumerate(captured):
                label, mode, period = views.TABS[tab]
                drawn = set(
                    same_bar(line) for line in
                    (report.overview(rows, project, width) if mode == "overview"
                     else report.tab_report(rows, project, mode, period, width)
                     ).split("\n"))
                gone = [line for line in ui_lines(frame)
                        if same_bar(line) not in drawn]
                for line in gone[:4]:
                    problems.append(f"{term_rows}x{cols} {label}: the UI draws"
                                    f" a line the report doesn't\n  |{line}|")
            print(f"agrees {term_rows}x{cols}: "
                  f"{'FAIL' if problems else 'ok'}")
    finally:
        if was is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = was
        shutil.rmtree(home, ignore_errors=True)
    return problems


def main() -> int:
    args = sys.argv[1:]
    if "--show" in args:
        width = int(args[args.index("--show") + 1])
        print(report.overview(make_rows(), "token-cost", width))
        return 0

    rows = make_rows()
    problems = (widths_ok(rows, "token-cost") + tables_ok(rows)
                + tabs_ok(rows, "token-cost"))
    # A one-day, one-model project has panels with a single row in them, and
    # a project whose figures are enormous pushes every column left.
    problems += widths_ok(make_rows(days=1, models=1), "p")
    huge = make_rows(days=2)
    for row in huge:
        row["cache_read"] *= 90_000
        row["cost_usd"] *= 40_000
    problems += widths_ok(huge, "a-project-with-a-very-long-name-indeed")

    real = ledger.read_ledger(
        ledger.ledger_path(args[args.index("--cwd") + 1] if "--cwd" in args
                           else str(Path.cwd())))
    if real:
        problems += (widths_ok(real, "token-cost") + tables_ok(real)
                     + tabs_ok(real, "token-cost"))

    problems += agrees_with_ui(
        args[args.index("--cwd") + 1] if "--cwd" in args else str(Path.cwd()))

    for problem in problems:
        print("FAIL", problem)
    print("PASS" if not problems else f"{len(problems)} failure(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
