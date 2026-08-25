#!/usr/bin/env python3
"""Time the UI's draw path, in a real terminal, one frame at a time.

60fps is 16.67ms a frame. Everything the UI does between one keypress and
the pixels landing has to fit inside that, and the only way to know whether
it does is to run the real draw functions against a real curses screen --
layout arithmetic changes with the width it is given, and a pad in a pipe
would measure something else.

So: allocate a pty, start curses inside it, and time whole frames the way
run() draws them -- erase, chrome, tab, refresh. Reports the median and the
worst frame per tab, because a UI that is fast on average and stutters every
tenth frame is a UI that stutters.

Usage: python3 dev/bench_tui.py [--cwd PATH] [--rows N] [--cols N]
                                [--frames N] [--synthetic N]

--synthetic writes a ledger of N rows to a scratch directory and measures
that instead: this project's own ledger is small, and the frame cost that
matters is the one a heavy project pays.
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import struct
import sys
import termios
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


# --------------------------------------------------------------------------
# a ledger big enough to hurt
# --------------------------------------------------------------------------

def synthesise(root: Path, rows: int, cwd: str) -> None:
    """Write a plausible ledger of `rows` rows under `root`.

    Shaped like a real one -- many turns per session, a handful of models,
    a month of days, prompts of realistic length -- because the draw cost
    tracks the number of distinct buckets, not the number of bytes.
    """
    import ledger

    ledger._ROOT_OVERRIDE = str(root)
    root.mkdir(parents=True, exist_ok=True)
    models = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"]
    words = ("refactor the ledger reader so it stops re-parsing every line "
             "on each frame and explain why the old one was slow").split()
    out = []
    for i in range(rows):
        session = f"{i // 14:08x}-0000-0000-0000-000000000000"
        turn = i % 14
        day = 1 + (i // 30) % 28
        ts = f"2026-08-{day:02d}T{(i % 24):02d}:{(i % 60):02d}:00.000Z"
        model = models[i % len(models)]
        tokens = {
            "input": 40 + i % 900,
            "output": 300 + i % 4000,
            "cache_read": 100_000 + (i * 977) % 900_000,
            "cache_write_5m": 2_000 + i % 20_000,
            "cache_write_1h": 0,
        }
        prompt = " ".join(words[: 4 + i % len(words)])
        row = {
            "session": session, "turn": turn, "ts": ts, "model": model,
            "kind": "subagent" if i % 7 == 0 else "main",
            "bill": {"speed": "fast"} if i % 11 == 0 else {},
            "reqs": 1 + i % 9, "prompt": prompt[:ledger.PROMPT_CAP],
            "ctx": ledger.input_total(tokens), **tokens,
        }
        row["cost_usd"] = ledger.cost_of(model, tokens, row["bill"])
        out.append(row)
    path = ledger.ledger_path(cwd)
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n"
                for r in out), encoding="utf-8")
    ledger.format_path(cwd).write_text(str(ledger.FORMAT), encoding="utf-8")


# --------------------------------------------------------------------------
# the timed run, inside the pty
# --------------------------------------------------------------------------

def child(cwd: str, frames: int, out_path: str, root: str | None) -> None:
    import curses

    def timed(stdscr):
        import tui

        if root:
            import ledger
            ledger._ROOT_OVERRIDE = root
        curses.curs_set(0)
        stdscr.keypad(True)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            for pair in (tui.C_ACCENT, tui.C_MUTED, tui.C_HEAD, tui.C_TOTAL):
                curses.init_pair(pair, 209 if curses.COLORS >= 256 else 3, -1)
            if curses.COLORS >= 256:
                curses.init_pair(tui.C_HOVER, 253, 237)
                curses.init_pair(tui.C_HOVER_MUTED, 245, 237)

        t0 = time.perf_counter()
        data = tui.Data(cwd)
        startup = (time.perf_counter() - t0) * 1000

        sc = tui.Screen(stdscr)
        search = tui.Search()
        results = {"startup_ms": startup, "rows": len(data.rows), "tabs": []}

        def frame(tab: int) -> float:
            sc.measure()
            stdscr.erase()
            top = tui.draw_chrome(sc, data, tab, search)
            tui.draw_tab(sc, data, tab, top, 0, search)
            stdscr.refresh()
            return time.perf_counter()

        # What the input loop does while nobody is typing. Everything after
        # this measures the UI as someone actually meets it: by the time a
        # person has read the opening screen, this has long since finished.
        t0 = time.perf_counter()
        while data.warm():
            pass
        results["warm_ms"] = (time.perf_counter() - t0) * 1000

        for tab, (label, _, _) in enumerate(tui.TABS):
            # Arriving from somewhere else, which is how anyone gets here:
            # draw another tab, then this one, and time the second.
            sc.mouse = None
            frame((tab + 1) % len(tui.TABS))
            start = time.perf_counter()
            arrive = (frame(tab) - start) * 1000

            # And the same arrival with nothing built, which is what someone
            # who outruns the warming sees. Reported, never hidden: it is
            # the honest ceiling on a keypress.
            data._tabs.pop(tab, None)
            data._overview = None
            frame((tab + 1) % len(tui.TABS))
            start = time.perf_counter()
            cold = (frame(tab) - start) * 1000

            times = []
            for i in range(frames):
                # Hover moves every frame: that is the case that has to hold
                # 60fps, because a pointer crossing the table sends one
                # report per cell and each is a frame.
                sc.mouse = (8 + i % 12, 20)
                start = time.perf_counter()
                frame(tab)
                times.append((time.perf_counter() - start) * 1000)
            times.sort()
            results["tabs"].append({
                "label": label,
                "arrive": arrive,
                "cold": cold,
                "median": times[len(times) // 2],
                "p95": times[int(len(times) * 0.95)],
                "worst": times[-1],
            })

        # And a reload, which is what `r` costs.
        t0 = time.perf_counter()
        data.reload()
        results["reload_ms"] = (time.perf_counter() - t0) * 1000

        Path(out_path).write_text(json.dumps(results), encoding="utf-8")

    try:
        curses.wrapper(timed)
    except BaseException:
        # curses.wrapper puts the terminal back before it re-raises, which
        # scrolls the traceback off a screen nobody is watching anyway. Put
        # it somewhere the parent can find it.
        import traceback
        Path(out_path + ".err").write_text(traceback.format_exc(),
                                           encoding="utf-8")
        raise


def bench(cwd: str, rows: int, cols: int, frames: int, root: str | None):
    out = Path(os.environ.get("TMPDIR", "/tmp")) / f"bench-{os.getpid()}.json"
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        try:
            child(cwd, frames, str(out), root)
        finally:
            os._exit(0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    seen = []
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        seen.append(chunk)
    os.waitpid(pid, 0)
    os.close(fd)
    failure = Path(str(out) + ".err")
    if not out.is_file():
        # Whatever went wrong went wrong inside the pty, where nobody can
        # see it. Hand it back rather than failing on the missing results.
        detail = (failure.read_text() if failure.is_file()
                  else b"".join(seen).decode("utf-8", "replace")[-3000:])
        failure.unlink(missing_ok=True)
        raise SystemExit("the timed run never finished:\n" + detail)
    failure.unlink(missing_ok=True)
    try:
        return json.loads(out.read_text())
    finally:
        out.unlink(missing_ok=True)


BUDGET = 1000 / 60      # ms in one frame at 60fps


def report(title: str, results: dict) -> bool:
    print(f"\n{title} — {results['rows']} ledger rows")
    print(f"  startup {results['startup_ms']:.0f}ms   "
          f"reload {results['reload_ms']:.0f}ms   "
          f"warm {results['warm_ms']:.0f}ms")
    print(f"  {'tab':<12} {'switch':>9} {'median':>9} {'p95':>9} "
          f"{'(cold)':>9}   fps")
    ok = True
    for tab in results["tabs"]:
        fps = 1000 / tab["p95"] if tab["p95"] else 999
        worst = max(tab["p95"], tab["arrive"])
        flag = "" if worst <= BUDGET else "  ← over budget"
        ok = ok and worst <= BUDGET
        print(f"  {tab['label']:<12} {tab['arrive']:8.2f}ms "
              f"{tab['median']:8.2f}ms {tab['p95']:8.2f}ms "
              f"{tab['cold']:8.2f}ms  {fps:5.0f}{flag}")
    return ok


def main() -> int:
    args = sys.argv[1:]

    def opt(name, fallback):
        return args[args.index(name) + 1] if name in args else fallback

    cwd = opt("--cwd", str(Path.cwd()))
    rows = int(opt("--rows", 44))
    cols = int(opt("--cols", 150))
    frames = int(opt("--frames", 60))
    synthetic = int(opt("--synthetic", 0))

    print(f"{rows}x{cols}, {frames} frames per tab, "
          f"budget {BUDGET:.2f}ms/frame for 60fps")

    ok = report("this project", bench(cwd, rows, cols, frames, None))

    if synthetic:
        scratch = Path(os.environ.get("TMPDIR", "/tmp")) / "token-cost-bench"
        synthesise(scratch, synthetic, cwd)
        ok = report(f"synthetic ({synthetic} rows)",
                    bench(cwd, rows, cols, frames, str(scratch))) and ok

    print("\n" + ("PASS — every tab inside the frame budget"
                  if ok else "over budget"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
