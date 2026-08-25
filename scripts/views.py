"""View construction shared by every frontend.

`report.py` prints tables into a conversation and `tui.py` draws them in a
terminal, but neither decides what a view *contains* -- that lives here, so
the two can never disagree about what a week costs. It is the same split
`ledger.py` makes for the recording side: one place that knows the numbers,
several that know how to show them.

A column is (header, align, cell_fn, total_fn), where cell_fn renders one
bucket and total_fn renders the footer over all of them. total_fn None leaves
the footer cell blank.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger  # noqa: E402

TASK_WIDTH = 52      # room for a prompt to be recognisable, not complete
TITLE_WIDTH = 34

# The only thing that ever narrows a report. Every view shows every row it
# has: a table that quietly drops rows is worse than a long one, because you
# cannot tell from looking at it that anything is missing.
PERIODS = {"today": 1, "day": 1, "week": 7, "month": 30}

MODES = ("days", "tasks", "sessions", "models")


# --------------------------------------------------------------------------
# cell formatting
# --------------------------------------------------------------------------

# Compiled once: a table names its model on every row, and re-looking-up
# these two patterns per cell is most of what shortening a name costs.
_VENDOR = re.compile(r"^claude-")
_DATED = re.compile(r"-\d{8}$")


def short_model(model: str) -> str:
    """claude-haiku-4-5-20251001 -> haiku-4-5

    A billing note rides along untouched: 'claude-opus-5 (fast)' shortens to
    'opus-5 (fast)', because dropping it would leave two rows on the same
    model at different prices with nothing to tell them apart.
    """
    model, sep, note = model.partition(" (")
    return _DATED.sub("", _VENDOR.sub("", model)) + sep + note


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


# What a row with no prompt is called: a turn whose transcript yielded no
# prompt and no ai-title when it was imported. The plain report keeps the
# dash -- it has a chat message's width to live within, and a phrase this
# long repeated down 900 rows is a wall. The UI has the room to say it.
UNKNOWN = "Unknown"
UNKNOWN_NOTE = "(no prompt on record)"
UNKNOWN_LONG = f"{UNKNOWN} {UNKNOWN_NOTE}"


def label_of(b: dict, width: int, unknown: str = "—") -> str:
    """A bucket's prompt, or `unknown` when none could be derived."""
    return ledger.condense(b.get("prompt") or unknown, width)


def cache_write(b: dict) -> int:
    return b["cache_write_5m"] + b["cache_write_1h"]


def started(b: dict) -> str:
    at = ledger.to_local(b.get("first_ts"))
    if at is None:
        return "?"
    return f"{at.month:02d}-{at.day:02d} {at.hour:02d}:{at.minute:02d}"


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

# Which columns a narrow terminal gives up first, and in what order. Cache
# writes are the smallest number on the row and input is usually two digits
# next to millions of cached tokens, so they are the least missed. Rows are
# never dropped this way -- only columns, and the header says which survived.
DROP_ORDER = ("CTX", "CACHE W", "CACHE R", "INPUT", "OUTPUT", "TASKS", "MODEL",
              "TIME", "REQS")


# --------------------------------------------------------------------------
# periods
# --------------------------------------------------------------------------

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


def in_window(rows: list, period: str):
    """(rows inside the period, a phrase describing it)."""
    first, last = window(period)
    kept = [r for r in rows if first <= ledger.local_day(r.get("ts")) <= last]
    return kept, describe(period, first, last)


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


# --------------------------------------------------------------------------
# the views themselves
# --------------------------------------------------------------------------

class View:
    """One table: its columns, its rows, and what to call it."""

    def __init__(self, columns, buckets, subtitle, tasks, hint=""):
        self.columns = columns
        self.buckets = buckets
        self.subtitle = subtitle
        self.tasks = tasks          # distinct tasks behind the whole view
        self.hint = hint
        # Somewhere for a frontend to keep its rendering of these buckets.
        # It hangs off the view rather than off the frontend because a view
        # is built exactly when its data changes, so nothing kept here can
        # outlive the numbers it was made from.
        self.render = None

    @property
    def overrides(self) -> dict:
        """Footer cells that can't be summed from the rows above. Buckets
        keyed by model each count their own tasks, so a turn that used both
        opus and a haiku subagent would otherwise be counted twice."""
        return {"TASKS": self.tasks}


def task_buckets(rows: list) -> list:
    """One bucket per task -- the (session, turn) pair the recorder writes."""
    buckets = ledger.aggregate(
        rows, lambda r: (r.get("session") or "?", r.get("turn") or 0))
    buckets.sort(key=lambda b: (b.get("first_ts") or "", b["key"][1]),
                 reverse=True)
    return buckets


def model_buckets(rows: list) -> list:
    buckets = ledger.aggregate(rows, lambda r: short_model(ledger.model_label(r)))
    buckets.sort(key=lambda b: -b["cost"])
    return buckets


def day_buckets(rows: list) -> list:
    buckets = ledger.aggregate(rows, lambda r: ledger.local_day(r.get("ts")))
    buckets.sort(key=lambda b: b["key"])
    return buckets


def session_buckets(rows: list) -> list:
    buckets = ledger.aggregate(rows, lambda r: r.get("session") or "?")
    buckets.sort(key=lambda b: b.get("first_ts") or "", reverse=True)
    return buckets


def total_tokens(bucket: dict) -> int:
    """Every counter in one figure: what the model actually read and wrote."""
    return sum(bucket.get(k, 0) for k in ledger.TOKEN_KEYS)


def count_tasks(rows: list) -> int:
    return len({(r.get("session"), r.get("turn")) for r in rows})


def build(rows, mode, scope="", label_width=None, unknown="—") -> View:
    """Assemble one view over rows already filtered to their period.

    `scope` is the phrase naming that period, if any; `label_width` lets a
    caller with more room than a chat message show a longer prompt.
    """
    tasks = count_tasks(rows)

    if mode == "tasks":
        width = label_width or TASK_WIDTH
        return View(
            [("TIME", "<", started, None),
             ("TASK", "<", lambda b: label_of(b, width, unknown), None),
             ("MODEL", "<", models_cell, None)]
            + NUMERIC_COLS[1:],   # a task counting its own tasks says "1" forever
            task_buckets(rows),
            scope or "every task",
            tasks,
            "" if scope else "Narrow it with /token-cost tasks week or tasks month.",
        )

    if mode == "sessions":
        width = label_width or TITLE_WIDTH
        buckets = session_buckets(rows)
        subtitle = f"{len(buckets)} sessions"
        if scope:
            subtitle += f", {scope}"
        return View(
            [("SESSION", "<", lambda b: b["key"][:8], None),
             ("TIME", "<", started, None),
             ("OPENED WITH", "<", lambda b: label_of(b, width, unknown), None)]
            + NUMERIC_COLS,
            buckets, subtitle, tasks,
            "Run /token-cost tasks for a per-task breakdown.",
        )

    if mode == "models":
        # Peak context: the largest single prompt each model was handed.
        # Beyond 200K the model was in 1M-window territory and its rows say
        # so -- opus-5 (1m) -- and this column says how deep.
        ctx_col = ("CTX", ">",
                   lambda b: ledger.fmt_tokens(b["ctx"]),
                   lambda bs: ledger.fmt_tokens(max((b["ctx"] for b in bs),
                                                    default=0)))
        return View(
            [("MODEL", "<", lambda b: b["key"], None)]
            + NUMERIC_COLS[:-1] + [ctx_col, NUMERIC_COLS[-1]],
            model_buckets(rows), scope, tasks,
            "Run /token-cost tasks for a per-task breakdown.",
        )

    buckets = day_buckets(rows)
    days = [b["key"] for b in buckets if b["key"] != "unknown"]
    return View(
        [("DATE", "<", lambda b: b["key"], None)] + NUMERIC_COLS,
        buckets,
        scope or (f"{min(days)} → {max(days)}" if days else ""),
        tasks,
        "Run /token-cost tasks for a per-task breakdown.",
    )
