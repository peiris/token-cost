#!/usr/bin/env python3
"""Check that the chat report fits every width it might be handed.

The overview and the plain tables land in a pane whose width nothing here
controls, and a single line one column too long wraps -- which takes the
frame it belongs to with it, and the one below that. So the property worth
testing is not how it looks but that it never overruns, and that narrowing
costs columns rather than rows: render at every width from the floor to well
past the ceiling and measure.

Rendered against a synthetic ledger, so the check says the same thing on a
machine with no usage recorded and on one with months of it.

Usage: python3 dev/smoke_report.py [--cwd PATH] [--show N]
"""

from __future__ import annotations

import sys
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
        # Inside a full-width frame every line has to reach the right-hand
        # border: one that stops early lost its edge, which reads as a
        # broken box even though nothing wrapped. The paired Project/Models
        # row is exempt -- two frames of their own widths with a gutter
        # between them, and the shorter one runs out under the taller.
        ragged, inside = [], False
        for i, line in enumerate(drawn, 1):
            if line.startswith("╭"):
                inside = len(line) == width and "╮" not in line[:-1]
            if inside and len(line) != width:
                ragged.append(i)
            elif line.startswith("╰") and len(line) == width:
                inside = False
        if ragged:
            problems.append(f"{width}: short frame line(s) at {ragged[:3]}")
        trailing = [i for i, line in enumerate(drawn, 1)
                    if line != line.rstrip()]
        if trailing:
            problems.append(f"{width}: trailing space at {trailing[:3]}")
    return problems


def main() -> int:
    args = sys.argv[1:]
    if "--show" in args:
        width = int(args[args.index("--show") + 1])
        print(report.overview(make_rows(), "token-cost", width))
        return 0

    rows = make_rows()
    problems = widths_ok(rows, "token-cost") + tables_ok(rows)
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
        problems += widths_ok(real, "token-cost") + tables_ok(real)

    for problem in problems:
        print("FAIL", problem)
    print("PASS" if not problems else f"{len(problems)} failure(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
