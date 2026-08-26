#!/usr/bin/env python3
"""The ledger as a page in a browser.

A third frontend, and the only one with no ceiling of any kind. A chat
message has a character budget; a terminal has a bottom edge and a column
count. A page has neither -- so this is where `/token-cost html` sends you
when the table itself is the point: every task on record, every column,
searchable and sortable, in one document.

It is the same UI seen a third time -- same tabs, same panels, same titles,
same columns, same cells -- because every figure on it comes from views.py
like the other two. What this file adds is only a shape for them: the
roll-ups happen here, in Python, and are handed to a pre-built template as
JSON. The page lays out what it was given, filters it and sorts it. Nothing
about what a week costs is decided in JavaScript, and the only arithmetic
over there is re-totalling a table the reader has filtered -- a footer
nobody could have computed in advance.

One file, written whole and opened with file:// -- no server, no assets, no
network. It replaces itself on every run, so the page in your browser is one
you can reload rather than one of forty in a temp directory.
"""

from __future__ import annotations

import html
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger  # noqa: E402
import report  # noqa: E402
import views  # noqa: E402

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "report.html"

# What the template leaves for us to fill in. Two holes, both filled once.
DATA_SLOT = "__TOKEN_COST_DATA__"
TITLE_SLOT = "__TOKEN_COST_TITLE__"

LOGO = "▂▄▆█"


# --------------------------------------------------------------------------
# the payload
# --------------------------------------------------------------------------

def view_payload(view) -> dict:
    """One table, as the page needs it.

    The cells are already text, rendered by the same Rendered the other two
    frontends draw from, so a row reads identically wherever you see it.
    Beside them go the figures those cells were made of: a filtered table
    has a footer nobody has computed yet, and "1.2M" cannot be added up.
    """
    table = views.rendered(view)
    headers = [column[0] for column in view.columns]
    aligns = [column[1] for column in view.columns]
    figures = [views.FIGURE_VALUES.get(header) for header in headers]

    total = [table.foot[header] for header in headers]
    if total:
        total[0] = "TOTAL"

    return {
        "columns": [
            {"header": header, "align": align,
             "kind": figure[1] if figure else "text"}
            for header, align, figure in zip(headers, aligns, figures)
        ],
        "rows": [[table.cells[header][i] for header in headers]
                 for i in range(table.count)],
        "figures": [[figure[0](bucket) if figure else None
                     for figure in figures] for bucket in view.buckets],
        # The rows carrying usage from a model with no price entry, so a
        # re-totalled column can flag the same "+?" the whole one does.
        # Named rather than counted: it is almost always empty.
        "unpriced": [i for i, bucket in enumerate(view.buckets)
                     if not bucket["cost_known"]],
        "total": total,
        "count": table.count,
        "unit": view.unit,
        # The prose column, which is the one a search reads and the one that
        # gives its width back when the window narrows. -1 where there is none.
        "search": next((headers.index(header)
                        for header in ("TASK", "OPENED WITH")
                        if header in headers), -1),
        "hint": view.hint,
    }


def overview_payload(rows: list) -> dict:
    """The overview tab's three roll-ups, in the shapes it draws them.

    All of the models and all of the days, not the handful a terminal had
    room for: this page's whole reason is that it never has to choose.
    """
    models = views.model_buckets(rows)
    days = [b for b in views.day_buckets(rows) if b["key"] != "unknown"]
    peak = max((b["cost"] for b in days), default=0.0) or 1.0
    costly = sorted(views.task_buckets(rows), key=lambda b: -b["cost"])[:5]

    def share(cost: float) -> float:
        # tui.bar: any non-zero value keeps a cell, so a day that cost
        # something never renders as nothing at all.
        return max(0.015, min(1.0, cost / peak)) if cost > 0 else 0.0

    return {
        "models": [{"name": b["key"],
                    "tasks": b["tasks"],
                    "tokens": ledger.fmt_tokens(views.total_tokens(b)),
                    "cost": ledger.fmt_usd(b["cost"], b["cost_known"])}
                   for b in models],
        "days": [{"label": b["key"][5:],
                  "share": share(b["cost"]),
                  "tokens": ledger.fmt_tokens(views.total_tokens(b)),
                  "cost": ledger.fmt_usd(b["cost"], b["cost_known"])}
                 for b in days],
        "tasks": [{"label": views.label_of(b, ledger.PROMPT_CAP,
                                           views.UNKNOWN_LONG),
                   "tokens": ledger.fmt_tokens(views.total_tokens(b)),
                   "cost": ledger.fmt_usd(b["cost"], b["cost_known"])}
                  for b in costly],
    }


def sessions_summary(view) -> dict | None:
    """The sessions tab's opening panel, as tui.draw_sessions_summary draws
    it: how many, how much each carries on average, and the two outliers
    that recency ordering buries."""
    buckets = view.buckets
    if not buckets:
        return None
    count = len(buckets)
    known = all(b["cost_known"] for b in buckets)
    average = sum(b["cost"] for b in buckets) / count
    priciest = max(buckets, key=lambda b: b["cost"])
    longest = max(buckets, key=views.total_tokens)

    def outlier(tag: str, bucket: dict, figure: str) -> dict:
        return {"tag": tag, "when": views.started(bucket),
                "label": views.label_of(bucket, ledger.PROMPT_CAP,
                                        views.UNKNOWN_LONG),
                "figure": figure}

    return {
        "count": f"{count:,} Sessions",
        "rate": f"{view.tasks / count:.1f} Tasks/session",
        "average": f"avg {ledger.fmt_usd(average, known)}/session",
        "rows": [
            outlier("Priciest", priciest,
                    ledger.fmt_usd(priciest["cost"], priciest["cost_known"])),
            outlier("Longest", longest,
                    f"{ledger.fmt_tokens(views.total_tokens(longest))} Tokens"),
        ],
    }


def tabs_payload(rows: list) -> list[dict]:
    """Every tab, built up front.

    All six at once because they are all in the one file: there is no
    keypress to build one on, and a page that has to fetch its next tab is a
    page that needs a server.
    """
    out = []
    for label, mode, period in views.TABS:
        if mode == "overview":
            out.append({"label": label, "mode": mode})
            continue
        built = views.Tab(rows, mode, period)
        # The two tabs the UI lets you search, for the same reason: they
        # are the ones whose rows carry a name. What one of their rows is
        # called goes with them, for the filter to count in.
        searchable = mode in ("tasks", "sessions")
        tab = {"label": label, "mode": mode, "scope": built.scope,
               "searchable": searchable,
               "noun": ("Tasks" if mode == "tasks" else "Sessions")
                       if searchable else ""}
        if not built.view.buckets:
            tab["empty"] = f"Nothing recorded in this window ({built.scope})."
            out.append(tab)
            continue
        if built.summary is not None:
            tab["summary"] = view_payload(built.summary)
            tab["summaryTitle"] = built.summary_title
        else:
            tab["sessions"] = sessions_summary(built.view)
        tab["main"] = view_payload(built.main)
        tab["mainTitle"] = built.main_title
        out.append(tab)
    return out


def payload(rows: list, project: str) -> dict:
    """Everything the page draws, in one object."""
    tasks = views.count_tasks(rows)
    cost = sum(row.get("cost_usd") or 0.0 for row in rows)
    known = all(row.get("cost_usd") is not None for row in rows)
    tokens = sum(sum(row.get(key, 0) for key in ledger.TOKEN_KEYS)
                 for row in rows)
    days = sorted({ledger.local_day(row.get("ts")) for row in rows}
                  - {"unknown"})
    span = f"{days[0]} → {days[-1]}" if days else "no dated rows"
    return {
        "project": project,
        "version": report.version(),
        "logo": LOGO,
        "generated": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
        # The Project panel's four facts, in the order the UI stacks them.
        "facts": [f"{tasks:,} Tasks", span,
                  f"{ledger.fmt_tokens(tokens)} Tokens",
                  ledger.fmt_usd(cost, known)],
        "overview": overview_payload(rows),
        "tabs": tabs_payload(rows),
    }


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------

def render(rows: list, project: str) -> str:
    """The template with this project's ledger in it."""
    template = TEMPLATE.read_text(encoding="utf-8")
    data = json.dumps(payload(rows, project), ensure_ascii=False)
    # The JSON sits inside a <script> element, where the one sequence that
    # can end the element early is "</". Escaping the slash closes that off
    # and leaves valid JSON behind -- \/ is a legal escape, and a prompt
    # containing </script> is a prompt somebody has certainly typed here.
    return (template
            .replace(TITLE_SLOT, html.escape(f"token-cost · {project}"))
            .replace(DATA_SLOT, data.replace("</", "<\\/")))


def report_path(cwd: str) -> Path:
    """Where this project's page lives.

    One path per project, beside the ledger it was made from and named the
    same way, so re-running the command replaces the page rather than
    leaving a trail of them -- and the tab already open on it reloads.
    """
    return ledger.ledger_dir() / ".reports" / f"{ledger.slug_for(cwd)}.html"


def write(cwd: str, page: str) -> Path:
    """The page on disk, in one rename.

    Staged the way the ledger's own rebuilds are: a browser reading the file
    while we write it should see the old page or the new one, never half of
    each.
    """
    target = report_path(cwd)
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_suffix(f".{os.getpid()}.tmp")
    scratch.write_text(page, encoding="utf-8")
    os.replace(scratch, target)
    return target


def open_browser(path: Path) -> bool:
    """Show the page. True if something took it."""
    import webbrowser
    url = path.as_uri()
    try:
        if webbrowser.open(url):
            return True
    except Exception:
        pass
    # A macOS with no browser registered through webbrowser still has `open`,
    # and it is the same thing the UI's window-opener leans on.
    if sys.platform == "darwin":
        import shutil
        import subprocess
        if shutil.which("open"):
            try:
                done = subprocess.run(["open", url], capture_output=True,
                                      timeout=20)
            except (OSError, subprocess.SubprocessError):
                return False
            return done.returncode == 0
    return False


def build(cwd: str, rows: list, project: str) -> tuple[Path, bool]:
    """(where the page is, whether a browser took it)."""
    path = write(cwd, render(rows, project))
    return path, open_browser(path)


# --------------------------------------------------------------------------
# what the command says
# --------------------------------------------------------------------------

def launch_block(cwd: str, rows: list, project: str, width: int) -> str:
    """What `/token-cost html` prints.

    Whoever reads this has their data in another window, so say that first
    and plainly -- as with `ui`, there is nothing to read here.
    """
    path, opened = build(cwd, rows, project)
    tabs = " · ".join(label for label, _, _ in views.TABS)
    keys = "Click or ←/→ tabs · 1-6 jump · / search · Esc clears"
    lines = []
    if opened:
        lines += report.wrap(f"The token-cost report for {project} is now open"
                             f" in your browser.", width)
        lines += report.wrap("Switch to that window — your usage data is"
                             " there, not here.", width)
    else:
        lines += report.wrap(f"The token-cost report for {project} has been"
                             f" written, and no browser could be opened for"
                             f" you here. Open this file:", width)
    lines += [""]
    lines += report.pairs([("Tabs", tabs), ("Keys", keys)], width, gap=3)
    lines += [""]
    lines += report.wrap("Every task on record, with every column, and"
                         " nothing dropped for room — click a header to sort,"
                         " type in the box to filter.", width)
    lines += ["", f"    {path}"]
    return "\n".join(lines)


def cli(cwd: str) -> int:
    """`token-cost html`, straight from a shell."""
    try:
        import record
        record.sync(cwd)
    except Exception:
        pass  # a sync failure must never stop the page rendering
    rows = ledger.read_ledger(ledger.ledger_path(cwd))
    project = Path(cwd).resolve().name
    if not rows:
        print(f"token-cost: no token usage recorded yet for {project}.",
              file=sys.stderr)
        return 1
    path, opened = build(cwd, rows, project)
    print(f"token-cost: {'opened' if opened else 'written to'} {path}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    cwd = str(Path.cwd())
    if "--cwd" in args:
        cwd = args[args.index("--cwd") + 1]
    return cli(cwd)


if __name__ == "__main__":
    sys.exit(main())
