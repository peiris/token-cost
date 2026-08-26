#!/usr/bin/env python3
"""Check that the browser report is whole, and is the UI it claims to be.

Three properties, and none of them is about how it looks.

It has to be whole. One file opened with file:// has nothing to fall back
on: a placeholder left unfilled, a fetch at a host that isn't there, or a
prompt that closes the script element early, and the page is blank with no
error anyone will see. So the built page is parsed back apart and checked.

It has to hold everything. The other two frontends have ceilings -- a chat
message has a character budget, a terminal has a bottom edge -- and this one
is the answer to both. Every tab must carry every row its view holds.

And it has to agree. Every cell is rendered by views.py, so the page and the
terminal cannot disagree about a figure they both show. The exception is a
table the reader has filtered, whose footer nobody computed in advance: the
page adds that one up itself, and the arithmetic it uses has to be Python's.
It isn't, by default -- Python rounds half to even and JavaScript rounds half
away from zero, which puts 1,250 tokens at 1.2k in the terminal and 1.3k in
the browser. So the page's own formatters are lifted out and run against
ledger.py's over a sweep of values, ties included.

The last two need a JavaScript engine, and run only where node is on PATH.
Everything above them runs anywhere.

Usage: python3 dev/smoke_html.py [--cwd PATH] [--keep]
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import html_report  # noqa: E402
import ledger  # noqa: E402
import views  # noqa: E402

FAILURES: list[str] = []


def check(ok: bool, what: str) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {what}")
    if not ok:
        FAILURES.append(what)


# --------------------------------------------------------------------------
# ledgers to build against
# --------------------------------------------------------------------------

# A prompt that would end the script element it is embedded in, and one that
# is nothing but characters no ASCII terminal has. Both are prompts somebody
# has certainly typed at Claude Code.
HOSTILE = '</script><img src=x onerror="alert(1)"> — 你好 «déjà»'


def synthetic(days: int = 9, models: int = 4) -> list[dict]:
    """A ledger with enough shape to fill every panel of every tab."""
    random.seed(11)
    names = ["claude-opus-5", "claude-opus-5 (1m)", "claude-fable-5",
             "claude-haiku-4-5-20251001", "nothing-we-have-a-price-for"]
    # Stamped UTC, the way a transcript does, but placed at local midday and
    # counted back from the viewer's today -- so the last day lands on it
    # whatever zone this runs in, and the Today tab is never empty.
    noon = datetime.now().astimezone().replace(hour=12, minute=0, second=0,
                                               microsecond=0)
    rows = []
    for d in range(days):
        for turn in range(3):
            name = names[(d + turn) % min(models, len(names))]
            at = (noon - timedelta(days=days - 1 - d, hours=turn)
                  ).astimezone(timezone.utc)
            rows.append({
                "ts": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "session": f"session-{d // 3}", "turn": turn, "model": name,
                "prompt": HOSTILE if turn == 1 else ("" if turn == 2 else
                                                     f"task {d}.{turn}"),
                "input": random.randrange(0, 3000),
                "output": random.randrange(0, 90000),
                "cache_read": random.randrange(0, 9_000_000),
                "cache_write_5m": 1250,     # an exact tie, both ways
                "cache_write_1h": random.randrange(0, 40000),
                "cost_usd": None if name.startswith("nothing")
                            else round(random.random() * 12, 6),
                "ctx": random.randrange(0, 900_000),
            })
    return rows


def ledgers(cwd: str) -> list[tuple[str, list[dict]]]:
    real = ledger.read_ledger(ledger.ledger_path(cwd))
    out = [("synthetic", synthetic()),
           ("one row", synthetic(days=1, models=1)[:1])]
    if real:
        out.append(("this project", real))
    return out


# --------------------------------------------------------------------------
# it has to be whole
# --------------------------------------------------------------------------

# Anything the page would have to go and get. A file:// page that reaches for
# a host gets nothing and says nothing about it.
REMOTE = re.compile(r"""(?:src|href)\s*=\s*["'](?!#)([^"']+)""", re.I)


def embedded(page: str) -> dict:
    """The payload back out of the built page, the way a browser reads it."""
    head = '<script id="token-cost-data" type="application/json">'
    body = page.split(head, 1)[1].split("</script>", 1)[0]
    return json.loads(body)


def check_whole(name: str, page: str, project: str,
                rows: list[dict]) -> dict | None:
    print(f"\n{name}: whole")
    check(html_report.DATA_SLOT not in page and html_report.TITLE_SLOT
          not in page, "every placeholder filled")
    remote = [url for url in REMOTE.findall(page)
              if not url.startswith("data:")]
    check(not remote, f"nothing to fetch{'' if not remote else f': {remote}'}")
    try:
        data = embedded(page)
    except (IndexError, ValueError) as exc:
        check(False, f"the payload parses as JSON ({exc})")
        return None
    check(True, "the payload parses as JSON")
    # The prompt that tries to close the element early has to come back out
    # of the page intact, or it was mangled rather than escaped. Only worth
    # asking of a ledger that actually holds one.
    if any(HOSTILE == (row.get("prompt") or "") for row in rows):
        cells = [cell for tab in data["tabs"] if "main" in tab
                 for line in tab["main"]["rows"] for cell in line]
        check(any(HOSTILE in cell for cell in cells),
              "a script-closing prompt survives escaping")
    check(data["project"] == project, "the masthead names the project")
    return data


# --------------------------------------------------------------------------
# it has to hold everything
# --------------------------------------------------------------------------

def check_complete(name: str, rows: list[dict], data: dict) -> None:
    print(f"\n{name}: complete")
    for label, mode, period in views.TABS:
        if mode == "overview":
            continue
        tab = next(t for t in data["tabs"] if t["label"] == label)
        built = views.Tab(rows, mode, period)
        if not built.view.buckets:
            check("empty" in tab, f"{label}: says so when the window is empty")
            continue
        held = len(tab["main"]["rows"])
        check(held == len(built.main.buckets),
              f"{label}: every row ({held} of {len(built.main.buckets)})")
        check(len(tab["main"]["figures"]) == held,
              f"{label}: a figure behind every row")
        check(len(tab["main"]["columns"]) == len(built.main.columns),
              f"{label}: every column, none dropped for width")

    over = data["overview"]
    models = views.model_buckets(rows)
    days = [b for b in views.day_buckets(rows) if b["key"] != "unknown"]
    check(len(over["models"]) == len(models),
          f"overview: every model ({len(models)})")
    check(len(over["days"]) == len(days), f"overview: every day ({len(days)})")


# --------------------------------------------------------------------------
# it has to agree
# --------------------------------------------------------------------------

def check_agrees(name: str, rows: list[dict], data: dict) -> None:
    print(f"\n{name}: agrees with views.py")
    for label, mode, period in views.TABS:
        if mode == "overview":
            continue
        tab = next(t for t in data["tabs"] if t["label"] == label)
        if "main" not in tab:
            continue
        built = views.Tab(rows, mode, period)
        for which, view in (("summary", built.summary), ("main", built.main)):
            payload = tab.get(which if which == "main" else "summary")
            if view is None or payload is None:
                continue
            table = views.rendered(view)
            headers = [c[0] for c in view.columns]
            want = [[table.cells[h][i] for h in headers]
                    for i in range(table.count)]
            check(payload["rows"] == want,
                  f"{label}/{which}: every cell is the view's own")
            total = [table.foot[h] for h in headers]
            total[0] = "TOTAL"
            check(payload["total"] == total, f"{label}/{which}: the TOTAL row")


# --------------------------------------------------------------------------
# and its arithmetic has to be Python's
# --------------------------------------------------------------------------

DOM_STUB = r"""
class Node {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = []; this.attrs = {}; this.dataset = {};
    this.style = {setProperty() {}}; this.className = ""; this._text = "";
    this.listeners = {};
  }
  appendChild(c) { this.children.push(c); return c; }
  set textContent(v) { this.children = []; this._text = String(v); }
  get textContent() {
    return this._text + this.children.map(c => c.textContent).join("");
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(n, f) { (this.listeners[n] ||= []).push(f); }
  fire(n, e) { for (const f of this.listeners[n] || []) f(e); }
  get offsetHeight() { return 96; }
  select() {} focus() {} blur() {}
  matches(sel) {
    if (sel[0] === "#") return this.attrs.id === sel.slice(1);
    if (sel[0] === ".") return this.className.split(/\s+/).includes(sel.slice(1));
    return this.tagName === sel.toUpperCase();
  }
  walk(out = []) { for (const c of this.children) { out.push(c); c.walk(out); } return out; }
  querySelectorAll(sel) {
    const steps = sel.trim().split(/\s+/);
    let pool = this.walk();
    for (let i = 0; i < steps.length; i++) {
      const kept = pool.filter(n => n.matches(steps[i]));
      if (i === steps.length - 1) return kept;
      pool = kept.flatMap(n => n.walk());
    }
    return [];
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
function makeDocument(dataText) {
  const root = new Node("html");
  const ids = {};
  const mk = (tag, id, cls) => {
    const n = new Node(tag);
    if (id) { n.attrs.id = id; ids[id] = n; }
    if (cls) n.className = cls;
    root.appendChild(n);
    return n;
  };
  mk("script", "token-cost-data").textContent = dataText;
  mk("div", "mast"); mk("nav", "tabs"); mk("main", "body");
  mk("footer", "legend");
  const sticky = mk("div", null, "sticky");
  return {ids, document: {
    documentElement: root, body: root,
    getElementById: id => ids[id] || null,
    createElement: tag => new Node(tag),
    createTextNode: t => { const n = new Node("#text"); n._text = String(t); return n; },
    addEventListener: (n, f) => root.addEventListener(n, f),
    querySelector: sel => (sel === ".sticky" ? sticky : root.querySelector(sel)),
    querySelectorAll: sel => root.querySelectorAll(sel),
  }};
}
module.exports = {makeDocument};
"""

DRIVER = r"""
const fs = require("fs"), vm = require("vm");
const {makeDocument} = require("./dom.js");
const page = fs.readFileSync(process.argv[2], "utf8");
const task = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const data = page.split('type="application/json">')[1].split("</script>")[0];
const script = page.split('<script>\n"use strict";')[1].split("</script>")[0];
const {document, ids} = makeDocument(data);
const box = {document, console, location: {hash: ""},
  history: {replaceState(_a, _b, h) { box.location.hash = h; }},
  window: {addEventListener() {}, scrollTo() {}, ResizeObserver: null},
  HTMLInputElement: class {}};
vm.createContext(box);
vm.runInContext('"use strict";' + script, box, {filename: "report.html"});
const call = (name, ...args) => vm.runInContext(name, box)(...args);

const out = {tokens: [], usd: [], tabs: [], filtered: null};
for (const n of task.tokens) out.tokens.push(call("fmtTokens", n));
for (const c of task.usd) out.usd.push(call("fmtUsd", c, true));

const tabs = ids.tabs.children.map(b => b.textContent);
tabs.forEach((label, i) => {
  ids.tabs.children[i].fire("click");
  const tables = ids.body.querySelectorAll("table");
  const last = tables[tables.length - 1];
  out.tabs.push({
    label,
    panels: ids.body.querySelectorAll(".title").map(t => t.textContent),
    rows: last ? last.querySelectorAll("tbody tr").length : 0,
    total: last ? last.querySelectorAll("tfoot td").map(t => t.textContent) : [],
  });
});

if (task.filter) {
  ids.tabs.children[task.filter.tab].fire("click");
  const input = ids.body.querySelector(".search input");
  input.value = task.filter.query;
  input.fire("input");
  const tables = ids.body.querySelectorAll("table");
  const last = tables[tables.length - 1];
  out.filtered = {
    title: ids.body.querySelectorAll(".title").pop().textContent,
    rows: last.querySelectorAll("tbody tr").length,
    total: last.querySelectorAll("tfoot td").map(t => t.textContent),
  };
}
console.log(JSON.stringify(out));
"""


def sweep():
    """Values whose formatting a browser is likely to get wrong.

    The exact ties are the point -- 1250 tokens, $0.125 -- because those are
    the only ones where half-to-even and half-away-from-zero part company,
    and they are not rare values.
    """
    random.seed(3)
    tokens = [0, 1, 999, 1000, 1001, 1050, 1250, 1350, 2500, 9950, 99950,
              999500, 999999, 1_000_000, 1_250_000, 1_500_000, 123_456_789]
    tokens += list(range(1000, 2600, 25))
    tokens += [random.randrange(0, 900_000_000) for _ in range(300)]
    usd = [0, 0.005, 0.015, 0.125, 0.135, 1.005, 2.675, 1234.565,
           1_000_000.005, 0.2103355]
    usd += [random.random() * scale
            for scale in (1, 10, 1000, 100_000) for _ in range(75)]
    return tokens, usd


def run_page(page: str, task: dict, work: Path):
    (work / "dom.js").write_text(DOM_STUB, encoding="utf-8")
    (work / "drive.js").write_text(DRIVER, encoding="utf-8")
    (work / "page.html").write_text(page, encoding="utf-8")
    (work / "task.json").write_text(json.dumps(task), encoding="utf-8")
    done = subprocess.run(
        ["node", "drive.js", "page.html", "task.json"],
        cwd=work, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        check(False, f"the page's own script runs ({done.stderr.strip()[:400]})")
        return None
    check(True, "the page's own script runs")
    return json.loads(done.stdout)


def check_in_browser(name: str, rows: list[dict], page: str,
                     work: Path) -> None:
    print(f"\n{name}: as the browser runs it")
    tokens, usd = sweep()
    # Which tab to filter, and by what: a word out of a prompt the ledger
    # actually holds, so the filter matches something.
    tasks_tab = next(i for i, (_, mode, _) in enumerate(views.TABS)
                     if mode == "tasks")
    built = views.Tab(rows, "tasks", None)
    labels = [views.label_of(b, ledger.PROMPT_CAP, views.UNKNOWN_LONG)
              for b in built.main.buckets]
    word = next((w for label in labels for w in label.split()
                 if len(w) > 3 and w.isalnum()), "")
    task = {"tokens": tokens, "usd": usd,
            "filter": {"tab": tasks_tab, "query": word} if word else None}

    got = run_page(page, task, work)
    if got is None:
        return

    bad = [(n, mine, theirs)
           for n, mine, theirs in zip(tokens, [ledger.fmt_tokens(n) for n in tokens],
                                      got["tokens"]) if mine != theirs]
    check(not bad, f"fmt_tokens agrees on {len(tokens)} values"
                   + (f" (e.g. {bad[0]})" if bad else ""))
    bad = [(c, mine, theirs)
           for c, mine, theirs in zip(usd, [ledger.fmt_usd(c, True) for c in usd],
                                      got["usd"]) if mine != theirs]
    check(not bad, f"fmt_usd agrees on {len(usd)} values"
                   + (f" (e.g. {bad[0]})" if bad else ""))

    drawn = {tab["label"]: tab for tab in got["tabs"]}
    check(set(drawn) == {label for label, _, _ in views.TABS},
          "every tab is on the bar and draws")
    for label, mode, period in views.TABS:
        if mode == "overview":
            continue
        want = views.Tab(rows, mode, period)
        if not want.view.buckets:
            continue
        check(drawn[label]["rows"] == len(want.main.buckets),
              f"{label}: draws every row ({drawn[label]['rows']})")

    if got["filtered"] and word:
        matched = [b for b in built.main.buckets
                   if word.casefold() in views.label_of(
                       b, ledger.PROMPT_CAP, views.UNKNOWN_LONG).casefold()]
        check(got["filtered"]["rows"] == len(matched),
              f'filtering by "{word}" keeps {len(matched)} rows')
        # The footer the page added up itself, against the one views.py
        # would have printed over exactly those rows.
        narrowed = views.View(built.main.columns, matched,
                              "", len(matched))
        table = views.rendered(narrowed)
        headers = [c[0] for c in narrowed.columns]
        want_total = [table.foot[h] for h in headers]
        want_total[0] = "TOTAL"
        check(got["filtered"]["total"] == want_total,
              "a filtered TOTAL is the one views.py would print")


# --------------------------------------------------------------------------
# and it has to keep itself current
# --------------------------------------------------------------------------

def check_freshness() -> None:
    """The page the recorder keeps in step, and the check that doesn't trust
    it to have run.

    Two halves that have to hold together. The recorder rewrites the page as
    rows land, so a browser refresh is all it takes to see the latest -- but
    only where a page exists, because a project nobody has opened one in
    must not start paying for one. And the command never assumes the
    recorder ran: hooks can be switched off, and a session that installed
    the plugin mid-flight has none registered until it restarts.

    Run against a ledger root of its own, so nothing here touches a real one.
    """
    print("\nkeeping current")
    import os
    import time

    import record
    root = Path(tempfile.mkdtemp(prefix="token-cost-fresh-"))
    project = root / "a-project"
    project.mkdir()
    cwd = str(project)
    was, ledger._ROOT_OVERRIDE = ledger._ROOT_OVERRIDE, str(root / "ledger")
    try:
        rows = synthetic(days=2)
        path = ledger.ledger_path(cwd)
        ledger.append_rows(path, rows)

        page = ledger.report_path(cwd)
        check(not page.exists(), "no page until somebody asks for one")
        check(html_report.refresh(cwd) is False,
              "the recorder builds no page where there is none")
        check(not page.exists(), "and still none afterwards")
        record.refresh_page(cwd)
        check(not page.exists(), "nor through the recorder's own entry point")

        html_report.write(cwd, html_report.render(rows, "a-project"))
        check(page.is_file(), "the command builds one")
        check(html_report.is_current(cwd), "freshly built, it is current")

        # A task finishes: rows land, and the page is a row behind until
        # whoever wrote them says so.
        time.sleep(0.02)
        more = synthetic(days=3)[-3:]
        ledger.append_rows(path, more)
        check(not html_report.is_current(cwd), "a new row makes it stale")

        before = page.stat().st_mtime
        check(html_report.refresh(cwd) is True, "the recorder rewrites it")
        check(html_report.is_current(cwd), "and it is current again")
        check(page.stat().st_mtime >= before, "the file actually changed")
        held = len(embedded(page.read_text(encoding="utf-8"))
                   ["tabs"][4]["main"]["rows"])
        want = len(views.Tab(ledger.read_ledger(path), "tasks", None)
                   .main.buckets)
        check(held == want, f"the rewritten page holds every row ({want})")

        # A plugin update ships a new template; every page built by the old
        # one is behind it, however recent the ledger is.
        time.sleep(0.02)
        html_report.TEMPLATE.touch()
        check(not html_report.is_current(cwd),
              "a newer template makes it stale")

        os.environ["TOKEN_COST_NO_REFRESH"] = "1"
        try:
            check(html_report.refresh(cwd) is False,
                  "TOKEN_COST_NO_REFRESH switches it off")
        finally:
            del os.environ["TOKEN_COST_NO_REFRESH"]
    finally:
        ledger._ROOT_OVERRIDE = was
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------------------

def main() -> int:
    args = sys.argv[1:]
    cwd = str(Path.cwd())
    if "--cwd" in args:
        cwd = args[args.index("--cwd") + 1]
    keep = "--keep" in args

    has_node = shutil.which("node") is not None
    if not has_node:
        print("node not on PATH: skipping the checks that run the page.\n")

    work = Path(tempfile.mkdtemp(prefix="token-cost-html-"))
    try:
        for name, rows in ledgers(cwd):
            project = "smoke"
            page = html_report.render(rows, project)
            data = check_whole(name, page, project, rows)
            if data is None:
                continue
            check_complete(name, rows, data)
            check_agrees(name, rows, data)
            if has_node:
                check_in_browser(name, rows, page, work)
        check_freshness()
    finally:
        if keep:
            print(f"\nleft behind: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed:")
        for what in FAILURES:
            print(f"  - {what}")
        return 1
    print("all good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
