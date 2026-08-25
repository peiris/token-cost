#!/usr/bin/env python3
"""Render the project's token ledger as a table.

Usage: report.py [--cwd PATH] [days|tasks [N|all]|sessions|today]
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger  # noqa: E402

HERE = Path(__file__).resolve().parent

# How many tasks the breakdown shows before it starts trimming. A day of
# work is tens of tasks, not hundreds; `tasks all` opts into the full list.
TASK_LIMIT = 25
TASK_WIDTH = 52      # room for a prompt to be recognisable, not complete
TITLE_WIDTH = 34


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


def render(columns, buckets, overrides=None, summaries=None) -> str:
    """`overrides` replaces a summary cell by header name. Needed for TASKS:
    buckets keyed by model each count their own tasks, so a turn that used
    both opus and a haiku subagent would be counted twice if we simply
    summed the per-bucket figures.

    `summaries` is a list of (label, buckets) pairs to print below the rule,
    defaulting to one TOTAL over the rows shown. A truncated list passes two,
    so a subtotal of the visible rows can never be read as the project's
    total -- the difference between those two numbers is the whole point of
    printing both.
    """
    overrides = overrides or {}
    summaries = summaries or [("TOTAL", buckets)]
    headers = [c[0] for c in columns]
    aligns = [c[1] for c in columns]
    body = [[str(c[2](b)) for c in columns] for b in buckets]
    foot = []
    for label, subset in summaries:
        cells = [
            str(overrides[c[0]]) if c[0] in overrides
            else (str(c[3](subset)) if c[3] else "")
            for c in columns
        ]
        cells[0] = label
        foot.append(cells)

    grid = [headers] + body + foot
    widths = [max(len(row[i]) for row in grid) for i in range(len(columns))]

    def line(cells):
        return "  ".join(
            cell.rjust(w) if a == ">" else cell.ljust(w)
            for cell, w, a in zip(cells, widths, aligns)
        ).rstrip()

    rule = "─" * (sum(widths) + 2 * (len(widths) - 1))
    return "\n".join([line(headers)] + [line(r) for r in body] + [rule]
                     + [line(f) for f in foot])


def main() -> int:
    args = sys.argv[1:]
    cwd = str(Path.cwd())
    if "--cwd" in args:
        i = args.index("--cwd")
        cwd = args[i + 1]
        del args[i:i + 2]
    mode = args[0].lower() if args else "days"

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

    hint = "Run /token-cost tasks for a per-task breakdown."
    summaries = None

    if mode.startswith("task"):
        # One row per task: the (session, turn) pair the recorder writes.
        buckets = ledger.aggregate(
            rows, lambda r: (r.get("session") or "?", r.get("turn") or 0))
        buckets.sort(key=lambda b: (b.get("first_ts") or "", b["key"][1]),
                     reverse=True)
        every = buckets
        total_tasks = len(every)
        limit = TASK_LIMIT
        if len(args) > 1:
            if args[1].lower() in ("all", "full"):
                limit = None
            elif args[1].isdigit():
                limit = max(int(args[1]), 1)
        buckets = every[:limit] if limit is not None else every
        columns = [
            ("WHEN", "<", started, None),
            ("TASK", "<", lambda b: label_of(b, TASK_WIDTH), None),
            ("MODEL", "<", models_cell, None),
        ] + NUMERIC_COLS[1:]   # a task counting its own tasks says "1" forever
        if len(buckets) < total_tasks:
            # Two footers, both named for what they cover. One row labelled
            # TOTAL that silently means "the 25 rows above" is how you get a
            # $20 total sitting under a $1,300 project.
            summaries = [(f"SHOWN ({len(buckets)})", buckets),
                         (f"ALL ({total_tasks})", every)]
            subtitle = f"latest {len(buckets)} of {total_tasks} tasks"
            hint = "Run /token-cost tasks all for the full list."
        else:
            subtitle = "every task"
            hint = ""
    elif mode.startswith("session"):
        buckets = ledger.aggregate(rows, lambda r: r.get("session") or "?")
        buckets.sort(key=lambda b: b.get("first_ts") or "", reverse=True)
        columns = [("SESSION", "<", lambda b: b["key"][:8], None),
                   ("STARTED", "<", started, None),
                   ("OPENED WITH", "<", lambda b: label_of(b, TITLE_WIDTH), None),
                   ] + NUMERIC_COLS
        subtitle = f"{len(buckets)} sessions"
    elif mode == "today":
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        rows = [r for r in rows if ledger.local_day(r.get("ts")) == today]
        if not rows:
            print(f"No token usage recorded for {project} today ({today}).")
            return 0
        buckets = ledger.aggregate(rows, lambda r: short_model(r.get("model", "?")))
        buckets.sort(key=lambda b: -b["cost"])
        columns = [("MODEL", "<", lambda b: b["key"], None)] + NUMERIC_COLS
        subtitle = f"today, {today}"
    else:
        buckets = ledger.aggregate(rows, lambda r: ledger.local_day(r.get("ts")))
        buckets.sort(key=lambda b: b["key"])
        columns = [("DATE", "<", lambda b: b["key"], None)] + NUMERIC_COLS
        days = [b["key"] for b in buckets if b["key"] != "unknown"]
        subtitle = f"{min(days)} → {max(days)}" if days else ""

    tasks = len({(r.get("session"), r.get("turn")) for r in rows})
    if imported:
        print(f"Imported {imported} earlier session(s) from transcripts on disk.")
        print()
    print(f"Project: {project}    {tasks} tasks    {subtitle}")
    print()
    print(render(columns, buckets, {"TASKS": tasks}, summaries))
    print()
    print("Estimated from published API rates; subscription plans are not billed per token.")
    if hint:
        print(hint)
    return 0


if __name__ == "__main__":
    sys.exit(main())
