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
        rows.append({
            "ts": group["ts"],
            "session": session_id,
            "turn": turn,
            "prompt": prompt,
            "model": group["model"],
            "kind": group["kind"],
            "reqs": group["reqs"],
            "cost_usd": ledger.cost_of(group["model"], tokens),
            **tokens,
        })
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

def backfill_session(transcript: Path) -> list[dict]:
    """Re-derive a finished session's rows from its transcript.

    Turn boundaries are reconstructed from real user prompts (see
    ledger.is_turn_start), so backfilled task counts are close but not
    guaranteed identical to what the live hook would have recorded. Token
    and cost totals are exact either way.

    Each turn is labelled with the prompt that opened it, so history
    imported from disk names its tasks exactly like live recording does.
    """
    session_id = transcript.stem
    turns: dict[int, list] = {}
    labels: dict[int, str] = {}
    boundaries: list[tuple[str, int]] = []
    turn = 0
    seen = set()

    with open(transcript, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if ledger.is_turn_start(entry):
                turn += 1
                boundaries.append((entry.get("timestamp") or "", turn))
                label = ledger.prompt_of(entry)
                # An interrupt marker opens a turn of its own but isn't a
                # task anyone asked for; if it ends up owning work, it is a
                # continuation of the task before it.
                if label and not ledger.is_human_prompt(entry) and not label.startswith("\u21ba"):
                    label = labels.get(turn - 1, label)
                labels[turn] = label
                continue
            rec = ledger.usage_of(entry)
            if rec is None or rec["request_id"] in seen:
                continue
            seen.add(rec["request_id"])
            turns.setdefault(max(turn, 1), []).append(rec)

    # Subagent transcripts are separate files, so attribute each of their
    # requests to whichever turn was open at that timestamp.
    for path in ledger.subagent_files(transcript):
        for rec in ledger.scan(path)[0]:
            if rec["request_id"] in seen:
                continue
            seen.add(rec["request_id"])
            ts = rec["ts"] or ""
            owner = 1
            for b_ts, b_turn in boundaries:
                if b_ts <= ts:
                    owner = b_turn
                else:
                    break
            turns.setdefault(owner, []).append(rec)

    rows = []
    for turn_no in sorted(turns):
        rows.extend(rows_for(turns[turn_no], session_id, turn_no,
                             labels.get(turn_no, "")))
    return rows


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

    Cheap to call speculatively: sessions already tracked are recognised by
    their state file and skipped without reading the transcript, so a
    fully-synced project costs a directory listing (~40ms even at 500
    sessions). Returns counts; prints nothing.
    """
    result = {"imported": 0, "tasks": 0, "skipped": 0, "transcripts": False}
    path = ledger.ledger_path(cwd, transcript)
    if force and path.exists():
        path.unlink()

    tdir = ledger.project_transcript_dir(cwd, transcript)
    if tdir is None or not tdir.is_dir():
        return result
    result["transcripts"] = True

    # One writer at a time per project, so two concurrent /token-cost runs
    # can't both import the same session. Whoever loses the race just skips
    # the sync -- the ledger is already being brought up to date.
    lock = _acquire_lock(path)
    if lock is None:
        return result

    try:
        for session_file in sorted(p for p in tdir.glob("*.jsonl") if p.is_file()):
            session_id = session_file.stem
            # A session the hook already tracks has state; leave it alone so
            # sync can never double-count it.
            if not force and ledger.state_path(session_id, transcript).exists():
                result["skipped"] += 1
                continue
            rows = backfill_session(session_file)
            if not rows:
                continue
            ledger.append_rows(path, rows)
            result["imported"] += 1
            result["tasks"] += len({r["turn"] for r in rows})
            last = max(rows, key=lambda r: r["turn"])
            ledger.save_state(session_id, {
                "turn": last["turn"],
                "offsets": {"main": session_file.stat().st_size},
                "seen": [],
                "prompt": last.get("prompt", ""),
            }, transcript)
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
    note = f", skipped {r['skipped']} already tracked" if r["skipped"] else ""
    print(f"Imported {r['imported']} session(s), {r['tasks']} task(s){note}.")
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
