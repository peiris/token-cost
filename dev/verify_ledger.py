#!/usr/bin/env python3
"""Prove the ledger against its sources.

Three independent checks, strongest first:

  A. Arithmetic -- every stored row's cost re-derives from its own token
     counts, billing modifiers, and the rate table. No row is taken on
     faith from the day it was written.
  B. Replay -- for every session whose transcript still exists, re-scan the
     transcript to exactly the byte offsets the recorder stopped at and
     reproduce the ledger's per-session sums: tokens, request counts, and
     dollars. The ledger is only trustworthy if an independent second pass
     over the same bytes lands on the same numbers.
  C. Cross-check -- Claude Code keeps its own per-project cost accounting
     (`lastModelUsage` in ~/.claude.json, with a costUSD it computed
     itself, keyed by the configured model id -- including `[1m]`
     variants). Feeding its token counts through our rate table must
     reproduce its dollars. Two implementations, one written by Anthropic,
     agreeing on the money is the closest thing to ground truth a
     subscription plan exposes.

Prints a summary; with an argument, also writes the full results as JSON.
Exit code 0 only if every check passes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import ledger  # noqa: E402

TOL_USD = 5e-3   # half a cent: Claude Code rounds its own floats too
TOL_ROW = 1e-6


# --------------------------------------------------------------------------
# A. arithmetic: rows re-derive from their own fields
# --------------------------------------------------------------------------

def check_arithmetic() -> dict:
    rows = mismatches = 0
    unpriced = 0
    for f in sorted(ledger.ledger_dir().glob("*.jsonl")):
        for row in ledger.read_ledger(f):
            rows += 1
            tokens = {k: row.get(k, 0) for k in ledger.TOKEN_KEYS}
            want = ledger.cost_of(row.get("model", ""), tokens, row.get("bill"))
            got = row.get("cost_usd")
            if want is None or got is None:
                if want is not got:
                    mismatches += 1
                else:
                    unpriced += 1
                continue
            if abs(want - got) > TOL_ROW:
                mismatches += 1
    return {"rows": rows, "mismatches": mismatches, "unpriced": unpriced,
            "pass": mismatches == 0}


# --------------------------------------------------------------------------
# B. replay: transcripts re-scanned to the recorded offsets
# --------------------------------------------------------------------------

def _find_transcript(session_id: str):
    root = ledger.claude_dir() / "projects"
    hits = list(root.glob(f"*/{session_id}.jsonl"))
    return hits[0] if hits else None


def _sum_side(acc: dict, key: tuple, tokens: dict, reqs: int, cost):
    slot = acc.setdefault(key, {"reqs": 0, "cost": 0.0, "cost_known": True,
                                **{k: 0 for k in ledger.TOKEN_KEYS}})
    slot["reqs"] += reqs
    for k in ledger.TOKEN_KEYS:
        slot[k] += tokens.get(k, 0)
    if cost is None:
        slot["cost_known"] = False
    else:
        slot["cost"] += cost


def _compare(ledger_side: dict, replay_side: dict):
    diffs = []
    for key in sorted(set(ledger_side) | set(replay_side)):
        a = ledger_side.get(key)
        b = replay_side.get(key)
        if a is None or b is None:
            diffs.append({"key": list(key), "ledger": a, "replay": b})
            continue
        for field in (*ledger.TOKEN_KEYS, "reqs"):
            if a[field] != b[field]:
                diffs.append({"key": list(key), "field": field,
                              "ledger": a[field], "replay": b[field]})
        if a["cost_known"] and b["cost_known"] and abs(a["cost"] - b["cost"]) > TOL_ROW:
            diffs.append({"key": list(key), "field": "cost",
                          "ledger": a["cost"], "replay": b["cost"]})
    return diffs


def check_replay() -> dict:
    verified = gone = no_state = partial = 0
    tokens_verified = 0
    cost_verified = 0.0
    failures = []

    for f in sorted(ledger.ledger_dir().glob("*.jsonl")):
        by_session: dict[str, dict] = {}
        for row in ledger.read_ledger(f):
            sid = row.get("session") or "?"
            tokens = {k: row.get(k, 0) for k in ledger.TOKEN_KEYS}
            _sum_side(by_session.setdefault(sid, {}),
                      (row.get("model"), row.get("kind")),
                      tokens, row.get("reqs", 0), row.get("cost_usd"))

        for sid, ledger_side in by_session.items():
            transcript = _find_transcript(sid)
            if transcript is None:
                gone += 1
                continue
            if not ledger.state_path(sid, transcript).is_file():
                no_state += 1
                continue
            state = ledger.load_state(sid, transcript)
            offsets = state.get("offsets") or {}

            targets = [("main", transcript)] + [
                (p.name, p) for p in ledger.subagent_files(transcript)]
            known = {name for name, _ in targets}
            if any(k not in known and offsets.get(k) for k in offsets):
                partial += 1   # a subagent transcript was deleted underneath us
                continue

            replay_side: dict = {}
            for name, path in targets:
                # scan() has no end bound, so replay exactly the byte range
                # the recorder had consumed when it wrote these rows.
                recs = _scan_to(path, offsets.get(name, 0))
                for rec in recs:
                    tokens = {k: rec[k] for k in ledger.TOKEN_KEYS}
                    _sum_side(replay_side, (rec["model"], rec["kind"]),
                              tokens, 1,
                              ledger.cost_of(rec["model"], tokens, rec.get("bill")))

            diffs = _compare(ledger_side, replay_side)
            if diffs:
                failures.append({"session": sid, "ledger_file": f.name,
                                 "diffs": diffs[:6]})
            else:
                verified += 1
                for slot in ledger_side.values():
                    tokens_verified += sum(slot[k] for k in ledger.TOKEN_KEYS)
                    cost_verified += slot["cost"]

    return {"verified": verified, "transcript_gone": gone,
            "no_state": no_state, "partial": partial,
            "tokens_verified": tokens_verified,
            "cost_verified": round(cost_verified, 6),
            "failures": failures, "pass": not failures}


def _scan_to(path: Path, end: int):
    """ledger.scan over only the bytes the recorder had consumed, so the
    replay sees exactly what the ledger was written from -- no more, even
    if the session has kept running since."""
    if end <= 0:
        return []
    import tempfile
    with open(path, "rb") as fh:
        data = fh.read(end)
    # scan() wants a file; feed it a truncated copy rather than reimplement
    # its parsing (the whole point is to run the real code path). The copy
    # goes to the system temp dir -- never next to real transcripts, where
    # a concurrent sync's *.jsonl glob could mistake it for a session.
    fd, tmp = tempfile.mkstemp(suffix=".verify")
    try:
        os.write(fd, data)
        os.close(fd)
        recs, _, _ = ledger.scan(Path(tmp), 0)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return recs


# --------------------------------------------------------------------------
# C. cross-check against Claude Code's own accounting
# --------------------------------------------------------------------------

def check_claude_code() -> dict:
    """Claude Code's lastModelUsage stores, per configured model id -- the
    `[1m]` suffix included -- the token counts it saw and the costUSD it
    computed for them. Its summary collapses both cache-write TTLs into one
    number, so its dollars can't be re-derived to a point; what CAN be
    proved is an interval: with every write priced at the 5-minute rate the
    figure is the lowest our table allows, at the 1-hour rate the highest,
    and Claude Code's own number must land inside. On a model whose retired
    1M beta really billed a premium (sonnet-4-5), the ceiling extends to
    the premium rates -- Claude Code knew which requests crossed 200K; its
    summary doesn't say. Every entry also yields the write rate implied by
    Claude Code's arithmetic, which must be a legal blend of the two TTLs.
    """
    cfg = Path.home() / ".claude.json"
    try:
        projects = json.load(open(cfg)).get("projects") or {}
    except (OSError, ValueError):
        return {"entries": 0, "skipped": True, "pass": True, "results": []}

    results = []
    mismatches = 0
    for proj, meta in sorted(projects.items()):
        for model, u in (meta.get("lastModelUsage") or {}).items():
            theirs = u.get("costUSD")
            if not theirs:
                continue
            rates = ledger.rates_for(model)
            if rates is None:
                continue
            tok = {
                "input": u.get("inputTokens") or 0,
                "output": u.get("outputTokens") or 0,
                "cache_read": u.get("cacheReadInputTokens") or 0,
            }
            creation = u.get("cacheCreationInputTokens") or 0
            web = (u.get("webSearchRequests") or 0) * 0.01
            base = sum(tok[k] * rates[k] for k in tok) / 1e6 + web
            low = base + creation * rates["cache_write_5m"] / 1e6
            high = base + creation * rates["cache_write_1h"] / 1e6
            band = "standard"
            prem = rates.get("long_context") or {}
            if all(k in prem for k in ledger.TOKEN_KEYS):
                # the beta premium ceiling: every token billed in-tier
                high_prem = (sum(tok[k] * prem[k] for k in tok) / 1e6 + web
                             + creation * prem["cache_write_1h"] / 1e6)
                if theirs > high + TOL_USD:
                    band = "premium"
                high = high_prem
            tol = max(TOL_USD, theirs * 1e-4)
            ok = (low - tol) <= theirs <= (high + tol)
            if not ok:
                mismatches += 1
            implied = ((theirs - base) * 1e6 / creation) if creation else None
            results.append({
                "project": Path(proj).name, "model": model,
                "claude_code_usd": round(theirs, 6),
                "low_usd": round(low, 6), "high_usd": round(high, 6),
                "implied_write_rate": round(implied, 4) if implied is not None else None,
                "w5m": rates["cache_write_5m"], "w1h": rates["cache_write_1h"],
                "band": band, "ok": ok,
            })
    return {"entries": len(results), "mismatches": mismatches,
            "results": results, "pass": mismatches == 0}


# --------------------------------------------------------------------------
# repair: rebuild a session the replay proved wrong
# --------------------------------------------------------------------------

def repair() -> int:
    """Re-derive every replay-failing session from its surviving transcript
    and make the ledger agree with the evidence.

    Two shapes of damage, one cause -- a pre-0.9 importer read subagent
    transcripts without recording their offsets, so a later pass could read
    them again:

      * rows right, state incomplete: the session is primed to double-count
        on its next sync. Heal the state; leave the rows alone.
      * rows already doubled: replace that session's rows with a fresh
        import of the full transcript, and store the state that goes with
        them.

    Everything happens under the same per-project lock the recorder uses.
    """
    import record

    fixed_state = rewritten = 0
    for f in sorted(ledger.ledger_dir().glob("*.jsonl")):
        rows = ledger.read_ledger(f)
        sessions = sorted({r.get("session") for r in rows if r.get("session")})
        bad = []
        for sid in sessions:
            transcript = _find_transcript(sid)
            if transcript is None or not ledger.state_path(sid, transcript).is_file():
                continue
            ledger_side: dict = {}
            for row in rows:
                if row.get("session") != sid:
                    continue
                tokens = {k: row.get(k, 0) for k in ledger.TOKEN_KEYS}
                _sum_side(ledger_side, (row.get("model"), row.get("kind")),
                          tokens, row.get("reqs", 0), row.get("cost_usd"))
            state = ledger.load_state(sid, transcript)
            offsets = state.get("offsets") or {}
            replay_side: dict = {}
            for name, path in [("main", transcript)] + [
                    (p.name, p) for p in ledger.subagent_files(transcript)]:
                for rec in _scan_to(path, offsets.get(name, 0)):
                    tokens = {k: rec[k] for k in ledger.TOKEN_KEYS}
                    _sum_side(replay_side, (rec["model"], rec["kind"]), tokens, 1,
                              ledger.cost_of(rec["model"], tokens, rec.get("bill")))
            if _compare(ledger_side, replay_side):
                bad.append((sid, transcript, ledger_side))
        if not bad:
            continue

        lock = record._acquire_lock(f)
        if lock is None:
            print(f"  {f.name}: locked by a live sync, skipping")
            continue
        try:
            for sid, transcript, ledger_side in bad:
                blank = {"turn": 0, "offsets": {}, "seen": [], "prompt": ""}
                fresh_rows, fresh_state = record.import_session(transcript, blank)
                fresh_side: dict = {}
                for row in fresh_rows:
                    tokens = {k: row.get(k, 0) for k in ledger.TOKEN_KEYS}
                    _sum_side(fresh_side, (row.get("model"), row.get("kind")),
                              tokens, row.get("reqs", 0), row.get("cost_usd"))
                if not _compare(ledger_side, fresh_side):
                    # rows already match a full import -- only the state was
                    # short. Store the state that covers what the rows hold.
                    ledger.save_state(sid, fresh_state, transcript)
                    fixed_state += 1
                    print(f"  {sid[:8]}: rows correct, state healed")
                    continue
                current = ledger.read_ledger(f)
                kept = [r for r in current if r.get("session") != sid]
                tmp = f.with_suffix(f".repair.{os.getpid()}")
                with open(tmp, "w", encoding="utf-8") as out:
                    for r in kept + fresh_rows:
                        out.write(json.dumps(r, separators=(",", ":"),
                                             sort_keys=True) + "\n")
                os.replace(tmp, f)
                ledger.save_state(sid, fresh_state, transcript)
                rewritten += 1
                print(f"  {sid[:8]}: rows rebuilt from transcript "
                      f"({len(current) - len(kept)} rows -> {len(fresh_rows)})")
        finally:
            record._release_lock(lock)
    print(f"repair: {rewritten} sessions rebuilt, {fixed_state} states healed")
    return 0


# --------------------------------------------------------------------------

def main() -> int:
    if "--repair" in sys.argv[1:]:
        return repair()
    out = {
        "arithmetic": check_arithmetic(),
        "replay": check_replay(),
        "claude_code": check_claude_code(),
    }
    a, r, c = out["arithmetic"], out["replay"], out["claude_code"]
    print(f"A. arithmetic : {a['rows']} rows, {a['mismatches']} mismatches"
          f" -> {'PASS' if a['pass'] else 'FAIL'}")
    print(f"B. replay     : {r['verified']} sessions reproduce exactly"
          f" ({ledger.fmt_tokens(r['tokens_verified'])} tokens,"
          f" ${r['cost_verified']:,.2f});"
          f" {r['transcript_gone']} transcripts gone, {r['no_state']} no state,"
          f" {r['partial']} partial -> {'PASS' if r['pass'] else 'FAIL'}")
    for fail in r["failures"][:5]:
        print("   MISMATCH", fail["session"][:8], fail["diffs"][:2])
    print(f"C. claude-code: {c['entries']} usage entries re-derived,"
          f" {c.get('mismatches', 0)} beyond half a cent"
          f" -> {'PASS' if c['pass'] else 'FAIL'}")
    for res in c["results"]:
        if not res["ok"]:
            print("   MISMATCH", res)

    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(out, indent=1))
        print(f"\nfull results -> {sys.argv[1]}")
    return 0 if all(x["pass"] for x in out.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
