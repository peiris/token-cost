"""Shared core for the token-cost plugin.

Everything that knows how to read a Claude Code transcript, count tokens
without double-counting them, price them, and roll them up lives here.
Both the Stop hook (record.py) and the report command (report.py) go
through this module, so the live path and the backfill path can never
drift apart in how they count.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

# Where Claude Code keeps its data. Never assume: a hook hands us a real
# transcript path, and everything can be derived from that. The fallbacks
# below only matter for `--backfill` invoked straight from a shell.
_ROOT_OVERRIDE = None  # tests point this at a temp directory

# The five counters we track per request. Cache writes are split by TTL
# because a 1-hour write costs 2x base input while a 5-minute write costs
# 1.25x -- collapsing them would quietly under-bill every long session.
TOKEN_KEYS = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")

# How long a task label may be in the ledger. Long enough that a truncated
# prompt still identifies the task, short enough that the ledger stays a
# usage record rather than a copy of the conversation.
PROMPT_CAP = 140

# How many recent request ids to carry in session state. Guards the case
# where a straggler content block for an already-recorded request lands
# after the byte offset we resumed from.
SEEN_CAP = 200


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

def claude_dir(transcript_path=None) -> Path:
    """Claude Code's data directory.

    A transcript always lives at <claude_dir>/projects/<project>/... , so when
    a hook gives us one we read the location straight off it rather than
    guessing where the user keeps their config. Only when there's no
    transcript do we fall back to the conventional locations.
    """
    if transcript_path:
        for parent in Path(transcript_path).resolve().parents:
            if parent.name == "projects" and parent.parent != parent:
                return parent.parent
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude"


def ledger_dir(transcript_path=None) -> Path:
    return Path(_ROOT_OVERRIDE) if _ROOT_OVERRIDE else claude_dir(transcript_path) / "token-cost"


def state_dir(transcript_path=None) -> Path:
    return ledger_dir(transcript_path) / ".state"


def slug_for(cwd: str) -> str:
    """A stable, filesystem-safe key for a project directory.

    This names our own ledger file, so it only has to be consistent between
    the recorder and the reporter -- it deliberately does not try to predict
    how Claude Code names its own directories.
    """
    text = str(Path(cwd).resolve())
    for ch in (os.sep, "/", "\\", ":"):
        text = text.replace(ch, "-")
    return text


def ledger_path(cwd: str, transcript_path=None) -> Path:
    return ledger_dir(transcript_path) / f"{slug_for(cwd)}.jsonl"


# The ledger format this code writes. Bump it whenever rows gain a field or
# change meaning. A project whose stamp disagrees is rebuilt from its
# transcripts at the next sync -- that is the whole migration story, and why
# no reader ever needs a "this may be an old row" branch (CLAUDE.md, "No
# legacy support"). Format 2: rows carry prompt, ctx, and bill.
FORMAT = 2


def format_path(cwd: str, transcript_path=None) -> Path:
    return ledger_dir(transcript_path) / f"{slug_for(cwd)}.format"


def stamped_format(cwd: str, transcript_path=None) -> int:
    """The format that wrote this project's ledger; 0 when never stamped,
    which reads as 'older than every numbered format' and forces a rebuild."""
    try:
        return int(format_path(cwd, transcript_path).read_text().strip())
    except (OSError, ValueError):
        return 0


def stamp_format(cwd: str, transcript_path=None) -> None:
    target = format_path(cwd, transcript_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(str(FORMAT), encoding="utf-8")
    os.replace(tmp, target)


def state_path(session_id: str, transcript_path=None) -> Path:
    return state_dir(transcript_path) / f"{session_id}.json"


def project_transcript_dir(cwd: str, transcript_path=None):
    """The directory holding this project's transcripts, or None.

    With a transcript path in hand this is exact. Without one we try the
    conventional slug, and if that misses we look for the project directory
    whose transcripts record this cwd -- transcript entries carry their own
    `cwd`, so we can identify the right directory by reading it rather than
    by reverse-engineering a naming scheme.
    """
    if transcript_path:
        for parent in Path(transcript_path).resolve().parents:
            if parent.parent.name == "projects":
                return parent

    root = claude_dir() / "projects"
    if not root.is_dir():
        return None

    guess = root / slug_for(cwd)
    if guess.is_dir():
        return guess

    target = str(Path(cwd).resolve())
    for candidate in sorted(root.iterdir()):
        if not candidate.is_dir():
            continue
        for transcript in candidate.glob("*.jsonl"):
            recorded = _first_cwd(transcript)
            if recorded and str(Path(recorded)) == target:
                return candidate
            break
    return None


def subagent_files(transcript) -> list:
    """Every subagent transcript belonging to one session.

    They sit in a directory named for the session, alongside it, and are
    billed separately -- often on a different model than the main loop. The
    location comes from the session's own transcript path, so this holds
    wherever Claude Code keeps its data.
    """
    transcript = Path(transcript)
    sub = transcript.parent / transcript.stem / "subagents"
    if not sub.is_dir():
        return []
    return sorted(p for p in sub.glob("agent-*.jsonl") if p.is_file())


def _first_cwd(transcript: Path):
    """The first cwd a transcript records, if any.

    Not the first entry's: transcripts open with mode and session-bridge
    entries that carry no cwd at all, and stopping there reads every
    transcript as placeless. Scan into the file -- bounded, because a cwd
    shows up within the first few real entries or not at all."""
    try:
        with open(transcript, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 50:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    cwd = json.loads(line).get("cwd")
                except ValueError:
                    continue
                if cwd:
                    return cwd
    except OSError:
        return None
    return None


# --------------------------------------------------------------------------
# pricing
# --------------------------------------------------------------------------

_PRICING_CACHE: dict | None = None


def load_pricing() -> dict:
    global _PRICING_CACHE
    if _PRICING_CACHE is None:
        with open(Path(__file__).resolve().parent / "pricing.json", encoding="utf-8") as fh:
            _PRICING_CACHE = json.load(fh)
    return _PRICING_CACHE


def rates_for(model: str):
    """Longest-prefix match, so dated ids (claude-haiku-4-5-20251001) and
    undated ones (claude-haiku-4-5) resolve to the same entry. Returns None
    for models we have no price for, so the caller can show '?' rather than
    pretending the usage was free."""
    pricing = load_pricing()["models"]
    best = None
    for key in pricing:
        if model.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return pricing[best] if best else None


def input_total(tokens: dict) -> int:
    """Every token a request sent. This is what a context tier is measured
    against -- fresh input, cache reads and cache writes alike all occupy the
    window, and the usage block counts each of them."""
    return sum(tokens.get(k, 0) for k in TOKEN_KEYS if k != "output")


def bill_of(model: str, usage: dict, tokens: dict) -> dict:
    """The rate modifiers in force for one request.

    A model id is not the whole price. The same `claude-opus-5` request costs
    twice as much under /fast, and 10% more with inference pinned to the US.
    None of that shows up in the model name -- but all of it is reported in
    the request's own usage block, so it is read rather than inferred. A
    context tier is the same shape: which tier a request lands in follows
    from the tokens it actually sent, which that block already counts.

    Returns only what differs from standard, so an ordinary request carries
    nothing extra in the ledger and prices exactly as it always did.
    """
    bill = {}

    if usage.get("speed") == "fast":
        bill["speed"] = "fast"

    # 'not_available' is what a request reports when it never had the choice.
    geo = usage.get("inference_geo")
    if geo and geo not in ("global", "not_available"):
        bill["geo"] = geo

    tier = usage.get("service_tier")
    if tier and tier != "standard":
        bill["tier"] = tier

    window = (rates_for(model) or {}).get("long_context")
    if window and input_total(tokens) > window["threshold"]:
        bill["long_context"] = True

    return bill


def bill_key(bill: dict | None) -> str:
    """A stable key for one set of modifiers, so requests billed differently
    never land in the same row."""
    return json.dumps(bill or {}, sort_keys=True, separators=(",", ":"))


def effective_rates(model: str, bill: dict | None = None):
    """The five per-token rates one request was actually billed at.

    Variants replace the base rates; modifiers scale whatever the variant
    left. That order is Anthropic's own: fast mode and long context each set
    a rate -- and fast mode sets it across the full context window, so it
    wins over a tier -- while data residency multiplies whatever applied.

    Any modifier we have no number for returns None, which surfaces as '?'.
    A modifier exists precisely because it changes the price, so guessing it
    away as standard would be the one answer certain to be wrong.
    """
    entry = rates_for(model)
    if entry is None:
        return None
    bill = bill or {}

    rates = entry
    if bill.get("long_context"):
        block = entry.get("long_context")
        if block is None:
            return None
        # A block is usually just a threshold: the marker records that the
        # request ran past 200K, and base rates apply, because that is what
        # Anthropic charges on every current 1M model. A block carrying all
        # five rate keys is a real premium tier (sonnet-4-5's retired beta)
        # and takes over; one carrying some of the five is a half-filled
        # table, and prices as unknown rather than as a guess.
        if any(k in block for k in TOKEN_KEYS):
            if not all(k in block for k in TOKEN_KEYS):
                return None
            rates = block
    if bill.get("speed"):
        rates = (entry.get("speed") or {}).get(bill["speed"])
        if rates is None:
            return None

    scale = 1.0
    modifiers = load_pricing().get("modifiers", {})
    for field, key in (("inference_geo", "geo"), ("service_tier", "tier")):
        value = bill.get(key)
        if value is None:
            continue
        factor = modifiers.get(field, {}).get(value)
        if factor is None:
            return None
        scale *= factor

    return {k: rates[k] * scale for k in TOKEN_KEYS}


def cost_of(model: str, tokens: dict, bill: dict | None = None) -> float | None:
    rates = effective_rates(model, bill)
    if rates is None:
        return None
    return sum(tokens.get(k, 0) * rates[k] for k in TOKEN_KEYS) / 1_000_000


def bill_note(bill: dict | None) -> str:
    """How a row's pricing differed from standard, in a word or two.

    Without this a fast-mode turn reads as the same model at twice the price,
    which looks like the model got expensive rather than like a setting did.
    """
    if not bill:
        return ""
    marks = [bill[k] for k in ("speed", "geo", "tier") if bill.get(k)]
    if bill.get("long_context"):
        marks.append("1m")
    return " ".join(marks)


def model_label(row: dict) -> str:
    """What to call a row's model in a report: the id, plus any note about
    how it was billed."""
    model = row.get("model") or "?"
    note = bill_note(row.get("bill"))
    return f"{model} ({note})" if note else model


# --------------------------------------------------------------------------
# transcript parsing
# --------------------------------------------------------------------------

def usage_of(entry: dict) -> dict | None:
    """Pull the five token counters out of one assistant transcript entry.

    Returns None for anything that isn't a real, priceable assistant
    request: non-assistant lines, `<synthetic>` placeholder entries (all
    zeros, sometimes with a null requestId), and entries with no request id
    to dedup on.
    """
    if entry.get("type") != "assistant":
        return None
    message = entry.get("message") or {}
    model = message.get("model")
    if not model or model == "<synthetic>":
        return None
    request_id = entry.get("requestId")
    if not request_id:
        return None
    u = message.get("usage") or {}
    creation = u.get("cache_creation") or {}
    rec = {
        "request_id": request_id,
        "model": model,
        "kind": "subagent" if entry.get("isSidechain") else "main",
        "ts": entry.get("timestamp"),
        "input": u.get("input_tokens") or 0,
        "output": u.get("output_tokens") or 0,
        "cache_read": u.get("cache_read_input_tokens") or 0,
        "cache_write_5m": creation.get("ephemeral_5m_input_tokens") or 0,
        "cache_write_1h": creation.get("ephemeral_1h_input_tokens") or 0,
    }
    rec["bill"] = bill_of(model, u, rec)
    # The request's whole prompt, in tokens. This is the figure the 200K
    # tier is measured against, and the only per-request record of how far
    # into the 1M window a session actually reached -- the configured model
    # id (claude-opus-5[1m]) never lands in the transcript, but every
    # request's own usage block proves the window it ran in.
    rec["ctx"] = input_total(rec)
    return rec


# Blocks the client wraps around a prompt that aren't part of what the user
# actually asked for. Stripped before labelling, or every task in a session
# with a slash command or an injected reminder would read the same.
_NOISE_BLOCK = re.compile(
    r"<(system-reminder|local-command-stdout|local-command-caveat|command-message"
    r"|command-args|task-id|tool-use-id|output-file|note|result)>.*?</\1>",
    re.S,
)
_COMMAND = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_SUMMARY = re.compile(r"<summary>(.*?)</summary>", re.S)


# A dragged-in screenshot arrives as an absolute path, often quoted and
# often longer than the label itself -- one of those eats the whole line and
# says nothing. What matters is that something was attached, and what kind.
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif",
                   ".bmp", ".svg", ".tiff", ".tif", ".avif"}
_QUOTED_PATH = re.compile(r"""(['"])((?:~|/)[^'"]+)\1""")
_BARE_PATH = re.compile(r'(?:~|/)[^\s\'"]*/[^\s\'"]*')


def _tag(path: str) -> str:
    dot = path.rfind(".")
    slash = path.rfind("/")
    suffix = path[dot:].lower() if dot > slash else ""
    return "[image]" if suffix in _IMAGE_SUFFIXES else "[file]"


def tag_paths(text: str) -> str:
    """Replace absolute file paths with [image] or [file]."""
    text = _QUOTED_PATH.sub(lambda m: _tag(m.group(2)), text)
    return _BARE_PATH.sub(lambda m: _tag(m.group(0)), text)


def _text_of(content) -> str:
    """Flatten a user message's content down to its plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def condense(text: str, cap: int = PROMPT_CAP) -> str:
    """One line, no runs of whitespace, capped."""
    text = " ".join(text.split())
    if len(text) > cap:
        return text[:cap - 1].rstrip() + "\u2026"
    return text


def is_human_prompt(entry: dict) -> bool:
    """Whether a turn was started by a person typing, as opposed to an
    interrupt marker or a system-generated notification.

    Newer transcripts say so directly; older ones predate both fields, so
    fall back to 'anything that isn't an interrupt marker'.
    """
    origin = entry.get("origin")
    if isinstance(origin, dict) and origin.get("kind"):
        return origin["kind"] == "human"
    source = entry.get("promptSource")
    if source:
        return source in ("typed", "queued")
    return not entry.get("interruptedMessageId")


def prompt_of(entry: dict) -> str:
    """A short label for the prompt that opened a turn.

    Slash commands are labelled by the command rather than by the wrapper
    the client expands them into, and task notifications by their summary,
    so a task list reads like the work that was asked for.
    """
    text = _text_of((entry.get("message") or {}).get("content"))
    origin = entry.get("origin")
    if isinstance(origin, dict) and origin.get("kind") == "task-notification":
        found = _SUMMARY.search(text)
        return condense("\u21ba " + (found.group(1) if found else "task notification"))

    command = _COMMAND.search(text)
    if command:
        args = _COMMAND_ARGS.search(text)
        label = command.group(1).strip()
        if args and args.group(1).strip():
            label += " " + args.group(1).strip()
        # A slash command is often followed by the real prompt in the same
        # entry (the client appends its output, then the user's text).
        rest = condense(tag_paths(
            _NOISE_BLOCK.sub(" ", _COMMAND.sub(" ", text))))
        return condense(f"{label} {rest}".strip())

    return condense(tag_paths(_NOISE_BLOCK.sub(" ", text)))


def is_turn_start(entry: dict) -> bool:
    """A real user prompt, as opposed to a tool result or a system-injected
    meta entry. Marks where one turn ends and the next begins, and so where
    a task's label comes from.

    Note: `promptId` looks like it would be the natural key here but is
    session-scoped, not per-turn -- it is identical across every user entry
    in a session, so it cannot separate turns.
    """
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return not any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
    return False


def scan(path: Path, offset: int = 0, skip: set | None = None):
    """Read new assistant usage records from a transcript, deduped.

    Claude Code writes each content block (text / tool_use / thinking) as
    its own JSONL line, all sharing one requestId and one *identical* usage
    block. Summing them line-by-line over-counts by ~2.5x, so we keep only
    the first line seen per requestId.

    Returns (records, new_offset, prompts). `prompts` holds the turn-start
    prompts seen in the same chunk, as (is_human, label) pairs, so the
    caller can label the turn without a second pass over the file.

    The offset only ever advances past complete newline-terminated lines,
    so a hook that fires while Claude Code is still flushing a line will
    re-read that line next time rather than losing or truncating it.
    """
    skip = skip or set()
    if not path.is_file():
        return [], offset, []

    size = path.stat().st_size
    if size < offset:  # file was rotated or truncated underneath us
        offset = 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read()

    if not data:
        return [], offset, []

    complete, _, partial = data.rpartition(b"\n")
    if not _:
        return [], offset, []  # no complete line yet
    new_offset = offset + len(data) - len(partial)

    records, prompts, seen = [], [], set()
    for raw in complete.split(b"\n"):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if is_turn_start(entry):
            label = prompt_of(entry)
            if label:
                prompts.append((is_human_prompt(entry), label))
            continue
        rec = usage_of(entry)
        if rec is None:
            continue
        rid = rec["request_id"]
        if rid in seen or rid in skip:
            continue
        seen.add(rid)
        records.append(rec)
    return records, new_offset, prompts


def pick_prompt(prompts: list, fallback: str = "") -> str:
    """The best label for a turn out of the prompts seen in its chunk.

    A turn that follows an interrupt, that a notification woke, or that the
    user added to mid-flight has more than one candidate. The first human
    prompt wins: it is the ask the work actually started from, and the one
    the user would recognise as "the task". Failing that -- a turn no human
    opened -- the most recent prompt of any kind, so a notification-driven
    turn is still named after what woke it.
    """
    for human, label in prompts:
        if human:
            return label
    return prompts[-1][1] if prompts else fallback


def group_records(records: list[dict]) -> list[dict]:
    """Collapse a turn's requests into one row per (model, kind, billing).

    Billing joins the key because a turn can change price mid-flight -- half
    of it under /fast, half not -- and a row is only priceable if every
    request in it was billed the same way.
    """
    groups: dict[tuple, dict] = {}
    for rec in records:
        bill = rec.get("bill") or {}
        key = (rec["model"], rec["kind"], bill_key(bill))
        row = groups.get(key)
        if row is None:
            row = groups[key] = {
                "model": rec["model"], "kind": rec["kind"], "bill": bill,
                "ts": rec["ts"], "reqs": 0, "ctx": 0,
                **{k: 0 for k in TOKEN_KEYS},
            }
        row["reqs"] += 1
        for k in TOKEN_KEYS:
            row[k] += rec[k]
        # Peak, not sum: token counters accumulate across requests, but a
        # context window is a size one request either fits in or doesn't.
        if (rec.get("ctx") or 0) > row["ctx"]:
            row["ctx"] = rec["ctx"]
        if rec["ts"] and (not row["ts"] or rec["ts"] > row["ts"]):
            row["ts"] = rec["ts"]
    return list(groups.values())


# --------------------------------------------------------------------------
# ledger io
# --------------------------------------------------------------------------

def append_rows(path: Path, rows: list[dict]) -> None:
    """Append rows as JSONL in a single write.

    One write() on a file opened 'a' is atomic for payloads this small, so
    two Claude Code sessions running in the same project cannot interleave
    half-lines into the ledger.
    """
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n" for r in rows
    )
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(payload)


def read_ledger(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def load_state(session_id: str, transcript=None) -> dict:
    try:
        with open(state_path(session_id, transcript), encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return {"turn": 0, "offsets": {}, "seen": [], "prompt": ""}
    state.setdefault("turn", 0)
    state.setdefault("offsets", {})
    state.setdefault("seen", [])
    state.setdefault("prompt", "")
    return state


def save_state(session_id: str, state: dict, transcript=None) -> None:
    state["seen"] = state["seen"][-SEEN_CAP:]
    state_dir(transcript).mkdir(parents=True, exist_ok=True)
    target = state_path(session_id, transcript)
    tmp = target.with_suffix(f".{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, separators=(",", ":"))
    os.replace(tmp, target)


# --------------------------------------------------------------------------
# aggregation + formatting
# --------------------------------------------------------------------------

def local_day(ts: str | None) -> str:
    """Bucket a UTC transcript timestamp into the viewer's local calendar
    day, so 'today' means the user's today rather than UTC's."""
    if not ts:
        return "unknown"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d")


def aggregate(rows: list[dict], key_fn) -> list[dict]:
    """Roll ledger rows up by an arbitrary key.

    Task counts come from distinct (session, turn) pairs, so a turn that
    used two models contributes two rows but still counts as one task.
    Cost is None-poisoned: if any row in a bucket has an unknown model, the
    bucket reports '?' instead of a total that silently omits it.

    Each bucket also carries the models it drew on, and the prompt of its
    earliest row -- which is what names a task or a session in the report.
    """
    buckets: dict = {}
    for row in rows:
        key = key_fn(row)
        b = buckets.get(key)
        if b is None:
            b = buckets[key] = {
                "key": key, "tasks": set(), "cost": 0.0,
                "cost_known": True, "first_ts": None, "prompt": "",
                "models": {}, "ctx": 0,
                **{k: 0 for k in TOKEN_KEYS},
            }
        b["tasks"].add((row.get("session"), row.get("turn")))
        for k in TOKEN_KEYS:
            b[k] += row.get(k, 0)
        if (row.get("ctx") or 0) > b["ctx"]:
            b["ctx"] = row["ctx"]
        if row.get("cost_usd") is None:
            b["cost_known"] = False
        else:
            b["cost"] += row["cost_usd"]
        model = model_label(row)
        if row.get("model"):
            # Ranked by spend, so the model a bucket mostly ran on leads --
            # a one-request haiku subagent shouldn't headline an opus task.
            b["models"][model] = b["models"].get(model, 0.0) + (row.get("cost_usd") or 0.0)
        ts = row.get("ts")
        if ts and (not b["first_ts"] or ts < b["first_ts"]):
            b["first_ts"] = ts
            if row.get("prompt"):
                b["prompt"] = row["prompt"]
        elif not b["prompt"]:
            b["prompt"] = row.get("prompt") or ""
    out = []
    for b in buckets.values():
        b["tasks"] = len(b["tasks"])
        b["models"] = [m for m, _ in sorted(b["models"].items(), key=lambda kv: -kv[1])]
        out.append(b)
    return out


def fmt_tokens(n: int) -> str:
    if n < 1_000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1_000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


def fmt_usd(cost: float, known: bool = True) -> str:
    """`known` is False when some usage in this bucket came from a model
    with no price entry. Rather than dropping the whole figure, show what
    we can price and flag that the real number is higher."""
    if known:
        return f"${cost:,.2f}"
    return f"${cost:,.2f}+?" if cost else "?"
