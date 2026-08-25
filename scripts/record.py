#!/usr/bin/env python3
"""Record token usage for a completed task.

Normally invoked as a Stop hook, with the hook payload on stdin. Run with
--backfill to import sessions that finished before the plugin was installed.

As a hook this must be invisible: no stdout, no stderr, always exit 0.
A Stop hook that writes to stdout or exits non-zero can surface text to the
user or interfere with the turn, and a usage tracker has no business doing
either. Every failure here is swallowed deliberately -- a lost row is a far
better outcome than a broken session.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger  # noqa: E402


def rows_for(records, session_id, turn, prompt=""):
    """Turn deduped requests into priced ledger rows, one per model+kind.

    Every row of a turn repeats that turn's prompt. It costs a little space
    over storing labels separately, but it keeps the ledger a flat file of
    self-contained rows -- a row still means something on its own, and the
    report never has to join two files to name a task.
    """
    rows = []
    for group in ledger.group_records(records):
        tokens = {k: group[k] for k in ledger.TOKEN_KEYS}
        bill = group.get("bill") or {}
        row = {
            "ts": group["ts"],
            "session": session_id,
            "turn": turn,
            "prompt": prompt,
            "model": group["model"],
            "kind": group["kind"],
            "reqs": group["reqs"],
            "ctx": group.get("ctx", 0),
            "cost_usd": ledger.cost_of(group["model"], tokens, bill),
            **tokens,
        }
        # Only carried when the request was billed as something other than
        # standard, so an ordinary ledger line stays exactly what it was.
        if bill:
            row["bill"] = bill
        rows.append(row)
    return rows


# --------------------------------------------------------------------------
# live hook path
# --------------------------------------------------------------------------

def run_hook() -> None:
    payload = json.loads(sys.stdin.read() or "{}")

    # Never re-enter: a Stop hook that fires again off its own continuation
    # would record the same turn twice.
    if payload.get("stop_hook_active"):
        return

    session_id = payload.get("session_id")
    transcript = payload.get("transcript_path")
    cwd = payload.get("cwd") or str(Path.cwd())
    if not session_id or not transcript:
        return

    transcript = Path(transcript)
    state = ledger.load_state(session_id, transcript)
    offsets = state["offsets"]
    skip = set(state["seen"])

    records = []
    prompts = []
    # The main transcript, plus every subagent transcript for this session --
    # subagents are billed separately and often run a different model.
    targets = [("main", transcript)] + [
        (p.name, p) for p in ledger.subagent_files(transcript)
    ]
    for name, path in targets:
        found, new_offset, found_prompts = ledger.scan(path, offsets.get(name, 0), skip)
        offsets[name] = new_offset
        records.extend(found)
        # Only the main transcript names the task: a subagent transcript
        # opens with the instructions we gave the agent, not the user's ask.
        if name == "main":
            prompts = found_prompts

    prompt = ledger.pick_prompt(prompts, state.get("prompt", ""))
    state["prompt"] = prompt

    if not records:
        ledger.save_state(session_id, state, transcript)
        return

    state["turn"] += 1
    ledger.append_rows(
        ledger.ledger_path(cwd, transcript),
        rows_for(records, session_id, state["turn"], prompt),
    )
    state["seen"].extend(r["request_id"] for r in records)
    ledger.save_state(session_id, state, transcript)


# --------------------------------------------------------------------------
# backfill path
# --------------------------------------------------------------------------

def import_session(transcript: Path, state: dict):
    """(rows, new state) for everything in a transcript the state hasn't seen.

    Driven entirely by byte offsets, which makes one operation out of two:
    the first import of a finished session, and the catch-up for one whose
    Stop hook never ran. That second case is not exotic -- a session is live
    while it is being imported, hooks reload only on restart, and a session
    can be recorded from another machine. Before this, sync skipped any
    session that had state at all, so a session imported while still running
    had its remaining turns stranded for good.

    Turn boundaries are reconstructed from real user prompts (see
    ledger.is_turn_start), so task counts are close but not guaranteed
    identical to what the live hook would have recorded. Token and cost
    totals are exact either way.
    """
    session_id = transcript.stem
    offsets = dict(state.get("offsets") or {})
    seen = set(state.get("seen") or [])
    opening = int(state.get("turn") or 0)
    turn = opening
    carried = state.get("prompt", "")

    turns: dict[int, list] = {}
    labels: dict[int, str] = {}
    boundaries: list[tuple[str, int]] = []

    try:
        size = transcript.stat().st_size
    except OSError:
        return [], state
    start = offsets.get("main", 0)
    if size < start:                      # rotated or truncated underneath us
        start, seen = 0, set()

    with open(transcript, "rb") as fh:
        fh.seek(start)
        data = fh.read()
    complete, newline, partial = data.rpartition(b"\n")
    if newline:
        offsets["main"] = start + len(data) - len(partial)
        for raw in complete.split(b"\n"):
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if ledger.is_turn_start(entry):
                turn += 1
                boundaries.append((entry.get("timestamp") or "", turn))
                label = ledger.prompt_of(entry)
                # An interrupt marker opens a turn of its own but isn't a
                # task anyone asked for; if it ends up owning work, it is a
                # continuation of the task before it.
                if (label and not ledger.is_human_prompt(entry)
                        and not label.startswith("\u21ba")):
                    label = labels.get(turn - 1, carried)
                labels[turn] = label or carried
                continue
            rec = ledger.usage_of(entry)
            if rec is None or rec["request_id"] in seen:
                continue
            seen.add(rec["request_id"])
            turns.setdefault(max(turn, 1), []).append(rec)

    # Subagent transcripts are separate files, so attribute each of their
    # requests to whichever turn was open at that timestamp.
    for path in ledger.subagent_files(transcript):
        found, new_offset, _ = ledger.scan(path, offsets.get(path.name, 0), seen)
        offsets[path.name] = new_offset
        for rec in found:
            seen.add(rec["request_id"])
            ts = rec["ts"] or ""
            owner = max(opening, 1)
            for b_ts, b_turn in boundaries:
                if b_ts <= ts:
                    owner = b_turn
                else:
                    break
            turns.setdefault(owner, []).append(rec)

    rows = []
    for turn_no in sorted(turns):
        rows.extend(rows_for(turns[turn_no], session_id, turn_no,
                             labels.get(turn_no, carried)))

    return rows, {
        "turn": turn,
        "offsets": offsets,
        "seen": list(seen)[-ledger.SEEN_CAP:],
        "prompt": labels.get(turn, carried),
    }


def run_sync() -> None:
    """SessionStart: bring this project's ledger up to date.

    This is what makes the plugin self-sufficient. Without it, history sitting
    in ~/.claude/projects/ only gets imported if someone runs /token-cost, and
    the first thing they see is an empty table. Silent and non-blocking: a
    SessionStart hook's stdout would be injected into the session as context,
    so this must never print.
    """
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    sync(payload.get("cwd") or str(Path.cwd()), transcript=payload.get("transcript_path"))


def sync(cwd: str, force: bool = False, transcript=None) -> dict:
    """Import any sessions for this project that aren't in the ledger yet.

    Cheap to call speculatively: a session with nothing new past its
    recorded offset costs a stat and a seek to the end of the file, so a
    fully-synced project stays a directory listing's worth of work.
    Returns counts; prints nothing.
    """
    result = {"imported": 0, "resumed": 0, "tasks": 0, "skipped": 0,
              "transcripts": False}
    path = ledger.ledger_path(cwd, transcript)
    tdir = ledger.project_transcript_dir(cwd, transcript)
    if tdir is None or not tdir.is_dir():
        return result
    result["transcripts"] = True

    if force:
        if path.exists():
            path.unlink()
        # The offsets have to go with the rows, or a rebuild resumes from
        # where the deleted ledger had reached and imports nothing. Only
        # this project's sessions: state lives in one directory for every
        # project, and clearing all of it would have every other project
        # re-import from zero and append a duplicate of everything it has.
        for session_file in tdir.glob("*.jsonl"):
            state_file = ledger.state_path(session_file.stem, transcript)
            try:
                state_file.unlink()
            except OSError:
                pass

    # One writer at a time per project, so two concurrent /token-cost runs
    # can't both import the same session. Whoever loses the race just skips
    # the sync -- the ledger is already being brought up to date.
    lock = _acquire_lock(path)
    if lock is None:
        return result

    try:
        for session_file in sorted(p for p in tdir.glob("*.jsonl") if p.is_file()):
            session_id = session_file.stem
            # Sessions already tracked are read from their recorded offset,
            # not skipped: what has already been counted is behind that mark,
            # and anything past it is a turn nobody recorded.
            tracked = ledger.state_path(session_id, transcript).exists()
            state = ledger.load_state(session_id, transcript)
            rows, new_state = import_session(session_file, state)
            if not rows:
                if new_state.get("offsets") != state.get("offsets"):
                    ledger.save_state(session_id, new_state, transcript)
                result["skipped"] += 1 if tracked else 0
                continue
            ledger.append_rows(path, rows)
            ledger.save_state(session_id, new_state, transcript)
            result["resumed" if tracked else "imported"] += 1
            result["tasks"] += len({r["turn"] for r in rows})
    finally:
        _release_lock(lock)
    return result


def _acquire_lock(ledger_file: Path):
    """Non-blocking exclusive lock. Returns a path, or None if held."""
    import time
    lock = ledger_file.with_suffix(".sync.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Clear a lock left behind by a process that died mid-sync.
        if lock.exists() and time.time() - lock.stat().st_mtime > 300:
            lock.unlink()
    except OSError:
        pass
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    except OSError:
        return None
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return lock


def _release_lock(lock) -> None:
    try:
        lock.unlink()
    except OSError:
        pass


def run_backfill(cwd: str, force: bool) -> int:
    r = sync(cwd, force)
    if not r["transcripts"]:
        print("No transcripts on disk for this project.")
        return 0
    parts = [f"Imported {r['imported']} session(s)"]
    if r["resumed"]:
        parts.append(f"resumed {r['resumed']}")
    parts.append(f"{r['tasks']} task(s)")
    if r["skipped"]:
        parts.append(f"{r['skipped']} already up to date")
    print(", ".join(parts) + ".")
    print(f"Ledger: {ledger.ledger_path(cwd)}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--sync" in args:
        try:
            run_sync()
        except Exception:
            pass  # never let a sync failure surface at session start
        return 0

    if "--backfill" in args:
        cwd = str(Path.cwd())
        if "--cwd" in args:
            cwd = args[args.index("--cwd") + 1]
        return run_backfill(cwd, force="--force" in args)

    try:
        run_hook()
    except Exception:
        # Deliberate catch-all: see module docstring.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
