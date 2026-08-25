#!/usr/bin/env python3
"""Render the project's token ledger as a table.

Usage: report.py [--cwd PATH] [days|tasks|sessions] [today|week|month]
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger  # noqa: E402

HERE = Path(__file__).resolve().parent

TASK_WIDTH = 52      # room for a prompt to be recognisable, not complete
TITLE_WIDTH = 34

# The only thing that ever narrows a report. Every view prints every row it
# has: a table that quietly drops rows is worse than a long one, because you
# cannot tell from looking at it that anything is missing.
PERIODS = {"today": 1, "day": 1, "week": 7, "month": 30}


def short_model(model: str) -> str:
    """claude-haiku-4-5-20251001 -> haiku-4-5"""
    return re.sub(r"-\d{8}$", "", re.sub(r"^claude-", "", model))


def models_cell(b: dict) -> str:
    """The model a task mostly ran on, plus a count of the others.

    A task that spawns subagents can touch three models; naming them all
    would swamp the row, and the one that carried the spend is the one
    worth seeing.
    """
    models = b.get("models") or []
    if not models:
        return "?"
    if len(models) == 1:
        return short_model(models[0])
    return f"{short_model(models[0])} +{len(models) - 1}"


def label_of(b: dict, width: int) -> str:
    """A bucket's prompt, or a dash for rows recorded before prompts were."""
    return ledger.condense(b.get("prompt") or "\u2014", width)


def cache_write(b: dict) -> int:
    return b["cache_write_5m"] + b["cache_write_1h"]


def started(b: dict) -> str:
    ts = b.get("first_ts")
    if not ts:
        return "?"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return "?"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%m-%d %H:%M")


# A column is (header, align, cell_fn, total_fn). total_fn takes the full
# bucket list; None means the TOTAL row leaves the cell blank.
def token_col(header, get):
    return (header, ">",
            lambda b: ledger.fmt_tokens(get(b)),
            lambda bs: ledger.fmt_tokens(sum(get(b) for b in bs)))


NUMERIC_COLS = [
    ("TASKS", ">", lambda b: b["tasks"], lambda bs: sum(b["tasks"] for b in bs)),
    token_col("INPUT", lambda b: b["input"]),
    token_col("OUTPUT", lambda b: b["output"]),
    token_col("CACHE R", lambda b: b["cache_read"]),
    token_col("CACHE W", cache_write),
    ("EST. $", ">",
     lambda b: ledger.fmt_usd(b["cost"], b["cost_known"]),
     lambda bs: ledger.fmt_usd(sum(b["cost"] for b in bs),
                               all(b["cost_known"] for b in bs))),
]


def render(columns, buckets, overrides=None) -> str:
    """`overrides` replaces a TOTAL cell by header name. Needed for TASKS:
    buckets keyed by model each count their own tasks, so a turn that used
    both opus and a haiku subagent would be counted twice if we simply
    summed the per-bucket figures."""
    overrides = overrides or {}
    headers = [c[0] for c in columns]
    aligns = [c[1] for c in columns]
    rows = [[str(c[2](b)) for c in columns] for b in buckets]
    total = [
        str(overrides[c[0]]) if c[0] in overrides
        else (str(c[3](buckets)) if c[3] else "")
        for c in columns
    ]
    total[0] = "TOTAL"

    grid = [headers] + rows + [total]
    widths = [max(len(row[i]) for row in grid) for i in range(len(columns))]

    def line(cells):
        return "  ".join(
            cell.rjust(w) if a == ">" else cell.ljust(w)
            for cell, w, a in zip(cells, widths, aligns)
        ).rstrip()

    rule = "─" * (sum(widths) + 2 * (len(widths) - 1))
    return "\n".join([line(headers)] + [line(r) for r in rows] + [rule, line(total)])


def window(period: str):
    """The local calendar days a period covers, inclusive, as ISO strings.

    Rolling rather than calendar-aligned: "week" is the last seven days
    including today, not whatever is left of the current week.
    """
    last = datetime.now().astimezone().date()
    first = last - timedelta(days=PERIODS[period] - 1)
    return first.isoformat(), last.isoformat()


def describe(period: str, first: str, last: str) -> str:
    if PERIODS[period] == 1:
        return f"today, {last}"
    return f"last {PERIODS[period]} days, {first} → {last}"


def parse_args(args):
    """(mode, period). A period on its own reports by model, which is what
    `/token-cost today` has always done; with a mode it just narrows it."""
    mode = period = None
    for arg in args:
        arg = arg.lower()
        if arg in PERIODS:
            period = arg
        elif arg.startswith("task"):
            mode = "tasks"
        elif arg.startswith("session"):
            mode = "sessions"
        elif arg.startswith("day") or arg.startswith("date"):
            mode = "days"
    if mode is None:
        mode = "models" if period else "days"
    return mode, period


def main() -> int:
    args = sys.argv[1:]
    cwd = str(Path.cwd())
    if "--cwd" in args:
        i = args.index("--cwd")
        cwd = args[i + 1]
        del args[i:i + 2]
    mode, period = parse_args(args)

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
    project = Path(cwd).name

    if not rows:
        print(f"No token usage recorded yet for {project}.")
        print()
        print("Usage is recorded automatically as tasks complete —"
              " finish a task and run /token-cost again.")
        return 0

    scope = ""
    if period:
        first, last = window(period)
        rows = [r for r in rows if first <= ledger.local_day(r.get("ts")) <= last]
        scope = describe(period, first, last)
        if not rows:
            print(f"No token usage recorded for {project} in that window ({scope}).")
            return 0

    hint = "Run /token-cost tasks for a per-task breakdown."

    if mode == "tasks":
        # One row per task: the (session, turn) pair the recorder writes.
        buckets = ledger.aggregate(
            rows, lambda r: (r.get("session") or "?", r.get("turn") or 0))
        buckets.sort(key=lambda b: (b.get("first_ts") or "", b["key"][1]),
                     reverse=True)
        columns = [
            ("WHEN", "<", started, None),
            ("TASK", "<", lambda b: label_of(b, TASK_WIDTH), None),
            ("MODEL", "<", models_cell, None),
        ] + NUMERIC_COLS[1:]   # a task counting its own tasks says "1" forever
        subtitle = scope or "every task"
        hint = "" if period else "Narrow it with /token-cost tasks week or tasks month."
    elif mode == "sessions":
        buckets = ledger.aggregate(rows, lambda r: r.get("session") or "?")
        buckets.sort(key=lambda b: b.get("first_ts") or "", reverse=True)
        columns = [("SESSION", "<", lambda b: b["key"][:8], None),
                   ("STARTED", "<", started, None),
                   ("OPENED WITH", "<", lambda b: label_of(b, TITLE_WIDTH), None),
                   ] + NUMERIC_COLS
        subtitle = f"{len(buckets)} sessions"
        if scope:
            subtitle += f", {scope}"
    elif mode == "models":
        buckets = ledger.aggregate(rows, lambda r: short_model(r.get("model", "?")))
        buckets.sort(key=lambda b: -b["cost"])
        columns = [("MODEL", "<", lambda b: b["key"], None)] + NUMERIC_COLS
        subtitle = scope
    else:
        buckets = ledger.aggregate(rows, lambda r: ledger.local_day(r.get("ts")))
        buckets.sort(key=lambda b: b["key"])
        columns = [("DATE", "<", lambda b: b["key"], None)] + NUMERIC_COLS
        days = [b["key"] for b in buckets if b["key"] != "unknown"]
        subtitle = scope or (f"{min(days)} → {max(days)}" if days else "")

    tasks = len({(r.get("session"), r.get("turn")) for r in rows})
    if imported:
        print(f"Imported {imported} earlier session(s) from transcripts on disk.")
        print()
    print(f"Project: {project}    {tasks} tasks    {subtitle}")
    print()
    print(render(columns, buckets, {"TASKS": tasks}))
    print()
    print("Estimated from published API rates; subscription plans are not billed per token.")
    if hint:
        print(hint)
    return 0


if __name__ == "__main__":
    sys.exit(main())
