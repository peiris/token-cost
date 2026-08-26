#!/usr/bin/env python3
"""Fill this project's ledger with a month of plausible usage, for demos.

The recorder derives rows from real transcripts; this derives the same rows
from a script -- a build of this plugin as it might have gone, laid over the
last few weeks. It goes through `ledger` and `record` rather than writing
JSON by hand, so every mock row is grouped, billed and priced by exactly the
code the live path uses. A demo ledger cannot drift from a real one.

    python3 dev/mock_ledger.py             # replace this project's ledger
    python3 dev/mock_ledger.py --days 45   # a longer history
    python3 dev/mock_ledger.py --restore   # put the real ledger back

The history always ends today, with the last day filled up to the current
hour, so re-running it just before a take gives a ledger whose "Today" tab
has something in it. The real ledger is copied to `<slug>.jsonl.real` on the
first run and that copy is never overwritten, so --restore always finds the
original.

Nothing here touches another project: the ledger written is the one for
--cwd, which defaults to this checkout.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ledger  # noqa: E402
import record  # noqa: E402

SEED = 20260827

# --------------------------------------------------------------------------
# the story
# --------------------------------------------------------------------------
# (prompt, size, flags). Size sets how much of a turn it was:
#   t tiny   one request        a slash command, a glance at the numbers
#   s small  a couple           a rename, a one-line fix
#   m mid    a handful          the ordinary unit of work
#   b big    a long turn        a feature, with tools running all through it
#   e epic   a very long turn   the ones that end up top of the cost table
# Flags: a = spawned subagents, h = ran on the command's own model (haiku),
#        f = the turn ran under /fast.

TASKS = [
    # -- finding the numbers ------------------------------------------------
    ("Where does Claude Code write per-request token usage, and what is actually in a transcript line?", "b", "a"),
    ("Every content block repeats the same usage block — dedupe by request id before summing anything", "m", ""),
    ("Trace one request id through a whole turn and show me why the naive sum is 2.5x too big", "m", ""),
    ("Sketch the plugin: a Stop hook that appends one priced row per turn to a per-project ledger", "b", ""),
    ("Derive the ledger location from the transcript path instead of guessing where config lives", "s", ""),
    ("Split cache writes by TTL — an hour-long write costs twice a five-minute one", "m", ""),
    ("Put the rate table in pricing.json and match model ids by longest prefix", "m", ""),
    ("Price a row: five counters, five rates, one number", "s", ""),
    ("/token-cost", "t", "h"),
    ("Round-trip the first ledger row end to end and check it against the transcript by hand", "m", ""),
    ("A Stop hook must be invisible — no stdout, no stderr, always exit zero", "s", ""),
    ("Swallow every failure in the hook: a lost row beats a broken session", "s", ""),
    ("Handle a transcript that was rotated or truncated underneath us", "s", ""),
    ("Add --backfill so sessions that finished before the plugin existed still land in the ledger", "b", "a"),
    ("/token-cost", "t", "h"),
    ("Name each turn with the prompt that opened it, capped so the ledger stays a usage record", "m", ""),

    # -- counting honestly --------------------------------------------------
    ("Subagents bill separately and often on another model — find their transcripts and count them", "b", "a"),
    ("Carry the billing modifiers a request reports, rather than inferring price from the model name", "b", ""),
    ("A turn can switch to fast mode halfway through, so billing belongs in the row key", "m", ""),
    ("Record the context window each request actually ran in, so 1M usage is visible in the ledger", "m", ""),
    ("Advance the read offset only past complete lines, so a hook firing mid-flush loses nothing", "m", ""),
    ("Two sessions in one project must never interleave half a line into the ledger", "s", ""),
    ("/token-cost week", "t", "h"),
    ("Strip system reminders and command wrappers before labelling, or every task reads the same", "m", ""),
    ("A dragged-in screenshot should read as [image], not as a 200-character path", "s", ""),
    ("Label a turn from the first human prompt in the chunk, not from whatever woke it", "m", ""),
    ("An unknown model should read as a question mark, never as free", "m", ""),
    ("Cache reads are a tenth of input — make that a fact of the table, not of the code", "s", ""),
    ("Add haiku 4.5 to the price table and check every derived rate against the docs", "s", ""),
    ("Long-context requests price at base rates from 4.6 on; confirm that before we bake it in", "m", "a"),
    ("Verify a week of the ledger against Claude Code's own per-project cost figures", "b", "a"),
    ("Compare what the ledger says against the usage page, and account for the difference", "m", "a"),

    # -- the ledger holds ---------------------------------------------------
    ("Stamp the ledger with the format that wrote it", "s", ""),
    ("Rebuild a project's ledger from its own transcripts when the stamp disagrees", "b", ""),
    ("Stage the rebuild: scratch file, one rename, stamp written last, .bak left beside", "m", ""),
    ("Refuse to replace a ledger that has rows with a ledger that has none", "s", ""),
    ("Lock per project so two concurrent syncs can't import the same session twice", "m", ""),
    ("The plugin acts on the current project only — never enumerate anyone else's ledger", "m", ""),
    ("/token-cost sessions", "t", "h"),
    ("Write a verifier that re-derives every row from the transcripts and diffs it against the ledger", "b", "a"),
    ("Make sync cheap enough to call on every SessionStart", "m", ""),
    ("A fully-synced project should cost a directory listing — profile it and prove that", "b", ""),
    ("Sessions with no cwd on the first line still belong to a project; scan a little further in", "m", ""),
    ("Handle a project whose transcripts have already aged out", "m", ""),
    ("Keep the history when a rebuild finds nothing to rebuild from", "s", ""),
    ("Document the ledger format in the README, field by field", "m", ""),

    # -- the report ---------------------------------------------------------
    ("Roll rows up by day, by task, by session and by model — in one module both frontends read", "b", ""),
    ("Fit a table to the terminal: flex the prompt column, drop numeric columns in a fixed order", "b", ""),
    ("Never drop a row to make a table fit; say which columns survived instead", "m", ""),
    ("Draw the overview — project totals, model split, cost per day, most expensive tasks", "e", ""),
    ("Cost per day wants a bar chart, not a column of numbers", "m", ""),
    ("Give the bar chart a scale that survives one enormous day", "m", ""),
    ("Format tokens as 1.2M, dollars to the cent, and an unpriceable row as a question mark", "s", ""),
    ("Right-align every number, left-align every word, and keep one gap between columns", "s", ""),
    ("A bare period should name a tab and let the tab decide what it holds", "s", ""),
    ("/token-cost today", "t", "h"),
    ("/token-cost tasks", "t", "h"),
    ("Group the day table by local day, not by UTC", "m", ""),
    ("Read the machine's timezone once and reuse it for every row", "s", ""),
    ("Print the timezone in the footer so a day boundary is never a mystery", "s", ""),
    ("Keep the chat report inside a token budget without ever silently dropping a row", "m", ""),
    ("Give the report a header that says which project and which version produced it", "s", ""),
    ("Show tokens and dollars side by side, never one without the other", "s", ""),
    ("How much has this project cost me this week?", "t", "h"),
    ("Which task was the most expensive, and what made it expensive?", "s", ""),
    ("Write the smoke test for the plain-text report and pin every column", "b", "a"),

    # -- the terminal UI ----------------------------------------------------
    ("Migrate the report into a full TUI with navigable tabs, one view per tab", "e", "a"),
    ("Render each view once and re-fit on resize, so dragging the window stays smooth", "b", ""),
    ("Show the peak context a task reached, not the sum of its requests", "m", ""),
    ("Sort any column by clicking its header, and re-total the footer after a filter", "b", ""),
    ("Filter tasks by substring and keep the totals honest while the filter is on", "m", ""),
    ("Highlight the row under the mouse — turn on tracking inside the alternate screen", "m", ""),
    ("Keyboard map: tab between views, slash to filter, q to leave", "m", ""),
    ("Two colours and a frame — keep the palette to the accent and the foreground", "m", "f"),
    ("Close the gap between the accent background and the frame edge", "s", "f"),
    ("A task list can run to hundreds of rows — page it rather than truncate it", "m", ""),
    ("Open the TUI in its own terminal window when the report is asked for one", "b", ""),
    ("Restore the cursor and leave the alternate screen even when the TUI is killed", "m", ""),
    ("Cache the rendered cells on the view, so they die at the moment they stop being true", "m", ""),
    ("Benchmark a redraw over 3,000 rows and hold a frame under sixteen milliseconds", "b", "a"),
    ("/token-cost ui", "t", "h"),
    ("Snapshot every tab of the TUI and diff the frames in a smoke test", "b", "a"),
    ("Property-test the fitter: no terminal width should ever cost us a row", "b", "a"),
    ("Run the whole suite against a ledger with a hundred thousand rows", "b", ""),
    ("Name a turn with no prompt on record 'Unknown', and say why in the UI", "s", ""),
    ("Sort the sessions view by cost first, then by when it started", "s", ""),

    # -- the browser report -------------------------------------------------
    ("Add an HTML report — same views, same numbers, rendered as a page", "e", "a"),
    ("Write the page atomically so a browser never reads it half-written", "m", ""),
    ("Keep the browser report current as tasks finish, instead of on demand", "b", ""),
    ("Draw the browser report's frames in the accent, as the UI does", "m", ""),
    ("Make the page readable in light and dark without asking the reader to choose", "m", ""),
    ("Add a search box to the browser report and keep the footer totals in step", "m", ""),
    ("/token-cost html", "t", "h"),
    ("Inline everything — a report that needs the network is a report that breaks offline", "m", ""),
    ("Sparklines per day, drawn as SVG rather than as blocks", "b", ""),

    # -- shipping -----------------------------------------------------------
    ("Reduce the hook's import cost — nothing heavy at module scope", "m", ""),
    ("Measure what the Stop hook adds to a turn, and get it under fifty milliseconds", "b", ""),
    ("Add the install shim so the CLI entry point works from anywhere", "m", ""),
    ("Ship the marketplace manifest and the install instructions", "m", ""),
    ("Check the plugin loads from a clean install with no ledger at all", "m", "a"),
    ("An empty ledger should say nothing has been recorded yet, not print an empty table", "s", ""),
    ("Add a dev script that re-syncs the desktop install from GitHub and proves it landed", "b", "a"),
    ("The desktop install wins where both exist — write that down in CLAUDE.md", "m", ""),
    ("Explain why there is no legacy support here, and what a format bump does instead", "m", ""),
    ("Reach the desktop install too, not just the CLI one", "b", ""),
    ("Say the same thing about the plugin everywhere", "m", ""),
    ("Write the README as the plugin's whole story, with a shot of each view", "b", "a"),
    ("Version the plugin and burn the number — never reuse or roll one back", "s", ""),
    ("/token-cost month", "t", "h"),
    ("Call it 1.0.0", "s", ""),
]

# Filler for a history longer than the story. Real weeks have these in them:
# a glance at the numbers, a small polish, a re-check after a rate change.
FILLER = [
    ("/token-cost", "t", "h"),
    ("/token-cost tasks", "t", "h"),
    ("/token-cost ui", "t", "h"),
    ("/token-cost week", "t", "h"),
    ("/token-cost today", "t", "h"),
    ("/token-cost sessions", "t", "h"),
    ("Re-check the price table against the published rates and note the date", "s", ""),
    ("Tighten the wording in the report footer", "s", ""),
    ("Add a test for the case we just fixed", "m", ""),
    ("Tidy the module docstrings so each one says what it is for", "s", ""),
    ("Run the smoke tests and tell me what moved", "m", ""),
    ("Read back what we changed today and check it against the rules in CLAUDE.md", "m", ""),
    ("Shorten the column headers so the table survives an eighty-column terminal", "s", ""),
    ("Rename the thing we have been calling a bucket to something that means it", "s", ""),
    ("Pull the repeated width arithmetic into one place", "m", ""),
    ("Add the case where a session has exactly one turn", "s", ""),
    ("Commit what is finished and leave the rest dirty", "s", ""),
    ("Bump the version and push, then update both installs", "m", ""),
    ("Walk the diff with me before I push it", "m", "a"),
    ("Where did the extra two seconds in the report come from?", "m", "a"),
    ("Trim the imports that nothing uses any more", "s", ""),
    ("Give this function a docstring that says why, not what", "s", ""),
]

# --------------------------------------------------------------------------
# how much a turn costs to run
# --------------------------------------------------------------------------
# (requests, output per request, tokens a tool call puts back into context)
SHAPES = {
    "t": ((1, 1), (110, 420), (0, 0)),
    "s": ((2, 6), (240, 1400), (400, 4500)),
    "m": ((7, 18), (350, 1900), (1200, 12000)),
    "b": ((19, 41), (400, 2400), (1800, 20000)),
    "e": ((44, 84), (500, 2800), (2400, 26000)),
}

MAIN_MODELS = [
    # (model, weight early, weight middle, weight late)
    ("claude-opus-5", 0.55, 0.58, 0.53),
    ("claude-sonnet-5", 0.36, 0.22, 0.14),
    ("claude-fable-5", 0.09, 0.20, 0.33),
]
AGENT_MODELS = ["claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-sonnet-5",
                "claude-haiku-4-5-20251001", "claude-opus-5"]
COMMAND_MODEL = "claude-haiku-4-5-20251001"

# A context that has grown past this gets compacted, the way a real one does.
COMPACT_AT = 520_000


def has_fast(model):
    """Whether /fast has a published rate for this model. Without one the row
    prices as unknown, which in a demo reads as a broken tracker."""
    return ledger.effective_rates(model, {"speed": "fast"}) is not None


class Rng(random.Random):
    def span(self, lo, hi):
        return self.randint(lo, hi)

    def pick(self, weighted):
        total = sum(w for _, w in weighted)
        mark = self.random() * total
        for value, weight in weighted:
            mark -= weight
            if mark <= 0:
                return value
        return weighted[-1][0]


def request(rng, model, kind, when, inp, out, cache_read, write, ttl, fast):
    """One priceable request, shaped exactly as `ledger.usage_of` shapes one."""
    rec = {
        "request_id": f"req_mock_{rng.getrandbits(48):012x}",
        "model": model,
        "kind": kind,
        "ts": (when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.")
               + f"{when.microsecond // 1000:03d}Z"),
        "input": inp,
        "output": out,
        "cache_read": cache_read,
        "cache_write_5m": write if ttl == "5m" else 0,
        "cache_write_1h": write if ttl == "1h" else 0,
    }
    usage = {"speed": "fast"} if fast and has_fast(model) else {}
    rec["bill"] = ledger.bill_of(model, usage, rec)
    rec["ctx"] = ledger.input_total(rec)
    return rec


def run_turn(rng, model, size, ctx, when, ttl, fast, kind="main"):
    """A turn's requests, and where it leaves the context and the clock.

    Modelled the way a turn really runs: each request re-reads the cached
    prefix, writes whatever has been added since the last one, and answers.
    The answer and any tool output become the next request's write.
    """
    (lo_reqs, hi_reqs), (lo_out, hi_out), (lo_tool, hi_tool) = SHAPES[size]
    recs = []
    pending = rng.span(60, 900)          # the prompt that opened the turn
    for _ in range(rng.span(lo_reqs, hi_reqs)):
        inp = rng.choice([1, 2, 3, 4, 6, 9, 14, 22, 40])
        out = rng.span(lo_out, hi_out)
        recs.append(request(rng, model, kind, when, inp, out, ctx, pending, ttl, fast))
        ctx += pending
        if ctx > COMPACT_AT:             # the session compacted
            ctx = rng.span(48_000, 92_000)
        tool = rng.span(lo_tool, hi_tool) if hi_tool else 0
        pending = out + tool
        when += timedelta(seconds=rng.span(4, 70))
    return recs, ctx, when


def agent_turns(rng, when, count):
    """Subagent requests for one turn: each agent carries its own context."""
    recs = []
    for _ in range(count):
        model = rng.choice(AGENT_MODELS)
        size = rng.choice(["m", "m", "b", "s"])
        got, _, _ = run_turn(rng, model, size, rng.span(24_000, 128_000),
                             when + timedelta(seconds=rng.span(2, 40)),
                             "1h", False, kind="subagent")
        recs.extend(got)
    return recs


# --------------------------------------------------------------------------
# laying the story over a calendar
# --------------------------------------------------------------------------

def day_weights(rng, days, today):
    """How busy each day was. Weekends are quieter, a few days are pushes,
    and a couple are missed entirely -- a month of real work is not a rectangle."""
    weights = []
    for i in range(days):
        date = today - timedelta(days=days - 1 - i)
        w = 1.0 if date.weekday() < 5 else 0.45
        w *= rng.uniform(0.55, 1.45)
        if rng.random() < 0.14:          # a push
            w *= rng.uniform(1.8, 2.9)
        if rng.random() < 0.07 and 0 < i < days - 1:
            w = 0.0                      # a day away
        weights.append(w)
    weights[-1] = max(weights[-1], 1.1)  # today always has something in it
    return weights


def share_out(weights, total):
    """Largest remainder: every task lands on exactly one day."""
    scale = total / sum(weights)
    raw = [w * scale for w in weights]
    counts = [int(x) for x in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - counts[i], reverse=True)
    for i in order[: total - sum(counts)]:
        counts[i] += 1
    return counts


def day_window(date, today, now):
    """When a day's sessions may run. Today stops at the clock."""
    start = datetime.combine(date, datetime.min.time(), tzinfo=now.tzinfo)
    if date == today:
        first = max(start + timedelta(minutes=10), now - timedelta(hours=9))
        return first, max(first + timedelta(minutes=25), now - timedelta(minutes=6))
    return start + timedelta(hours=9, minutes=20), start + timedelta(hours=23, minutes=40)


def build(days, seed):
    rng = Rng(seed)
    now = datetime.now().astimezone()
    today = now.date()

    script = list(TASKS)
    weights = day_weights(rng, days, today)
    # Enough work for the calendar: the story first, then the small recurring
    # things a real week is padded with.
    target = max(len(script), int(round(sum(weights) * 7.0)))
    while len(script) < target:
        at = rng.span(6, len(script))
        item = rng.choice(FILLER)
        # Never twice in a row, and never against another one-line glance:
        # two identical prompts one after the other read as a bug in the
        # tracker rather than as a habit.
        near = script[max(0, at - 1): at + 1]
        if any(n[0] == item[0] for n in near):
            continue
        if item[1] == "t" and any(n[1] == "t" for n in near):
            continue
        script.insert(at, item)

    counts = share_out(weights, len(script))
    rows, cursor = [], 0
    for i, count in enumerate(counts):
        if not count:
            continue
        date = today - timedelta(days=days - 1 - i)
        opens, closes = day_window(date, today, now)
        phase = i / max(1, days - 1)
        models = [(m, (e if phase < 0.3 else mid if phase < 0.72 else late))
                  for m, e, mid, late in MAIN_MODELS]

        todo = script[cursor:cursor + count]
        cursor += count
        # A day of nothing but one-line glances is a true thing that reads
        # as an empty day: give every day at least one piece of real work.
        if len(todo) > 1 and all(t[1] == "t" for t in todo):
            swap = next((j for j in range(cursor, len(script))
                         if script[j][1] != "t"), None)
            if swap is not None:
                script[swap], script[cursor - count] = \
                    script[cursor - count], script[swap]
                todo = script[cursor - count:cursor]
        # Split the day into sessions of a few tasks each.
        sessions = []
        while todo:
            take = min(len(todo), rng.span(2, 6))
            sessions.append(todo[:take])
            todo = todo[take:]

        room = (closes - opens) / max(1, len(sessions))
        for s, tasks in enumerate(sessions):
            session_id = f"{rng.getrandbits(32):08x}-{rng.getrandbits(16):04x}-" \
                         f"4{rng.getrandbits(12):03x}-a{rng.getrandbits(12):03x}-" \
                         f"{rng.getrandbits(48):012x}"
            when = opens + room * s + timedelta(seconds=rng.span(0, 900))
            model = rng.pick(models)
            ttl = "5m" if rng.random() < 0.06 else "1h"
            fast_session = rng.random() < 0.08
            ctx = rng.span(21_000, 46_000)
            for turn, (prompt, size, flags) in enumerate(tasks, start=1):
                fast = fast_session or "f" in flags
                turn_model = COMMAND_MODEL if "h" in flags else model
                recs, ctx, when = run_turn(rng, turn_model, size, ctx, when, ttl,
                                           fast and "h" not in flags)
                if "a" in flags:
                    recs += agent_turns(rng, when, rng.span(1, 4))
                rows.extend(record.rows_for(recs, session_id, turn, prompt))
                when += timedelta(seconds=rng.span(30, 900))
    rows.sort(key=lambda r: r["ts"])
    return rows


# --------------------------------------------------------------------------
# writing it out
# --------------------------------------------------------------------------

def backup_of(path):
    return path.with_suffix(".jsonl.real")


def restore(path):
    keep = backup_of(path)
    if not keep.is_file():
        print(f"No saved ledger at {keep} — nothing to restore.")
        return 1
    shutil.copyfile(keep, path)
    print(f"Restored {len(ledger.read_ledger(path))} real rows to {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=33)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--cwd", default=str(ROOT))
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    path = ledger.ledger_path(args.cwd)
    if args.restore:
        return restore(path)

    keep = backup_of(path)
    if path.is_file() and not keep.is_file():
        shutil.copyfile(path, keep)
        print(f"Real ledger saved to {keep}")

    rows = build(args.days, args.seed)
    tmp = path.with_suffix(f".mock.{os.getpid()}")
    if tmp.exists():
        tmp.unlink()
    ledger.append_rows(tmp, rows)
    os.replace(tmp, path)
    ledger.stamp_format(args.cwd)        # so no sync rebuilds this away
    record.refresh_page(args.cwd)

    total = sum(r["cost_usd"] or 0 for r in rows)
    tokens = sum(r[k] for r in rows for k in ledger.TOKEN_KEYS)
    tasks = len({(r["session"], r["turn"]) for r in rows})
    print(f"{len(rows)} rows · {tasks} tasks · "
          f"{len({r['session'] for r in rows})} sessions · "
          f"{ledger.fmt_tokens(tokens)} tokens · {ledger.fmt_usd(total)}")
    print(f"Ledger: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
