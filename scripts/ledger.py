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
    """The cwd recorded on the first entry of a transcript, if any."""
    try:
        with open(transcript, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    return json.loads(line).get("cwd")
                except ValueError:
                    return None
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
            _PRICING_CACHE = json.load(fh)["models"]
    return _PRICING_CACHE


def rates_for(model: str):
    """Longest-prefix match, so dated ids (claude-haiku-4-5-20251001) and
    undated ones (claude-haiku-4-5) resolve to the same entry. Returns None
    for models we have no price for, so the caller can show '?' rather than
    pretending the usage was free."""
    pricing = load_pricing()
    best = None
    for key in pricing:
        if model.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return pricing[best] if best else None


def cost_of(model: str, tokens: dict) -> float | None:
    rates = rates_for(model)
    if rates is None:
        return None
    return sum(tokens.get(k, 0) * rates[k] for k in TOKEN_KEYS) / 1_000_000


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
    return {
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


def is_turn_start(entry: dict) -> bool:
    """A real user prompt, as opposed to a tool result or a system-injected
    meta entry. Used only to reconstruct turn boundaries during backfill.

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

    Returns (records, new_offset). The offset only ever advances past
    complete newline-terminated lines, so a hook that fires while Claude
    Code is still flushing a line will re-read that line next time rather
    than losing or truncating it.
    """
    skip = skip or set()
    if not path.is_file():
        return [], offset

    size = path.stat().st_size
    if size < offset:  # file was rotated or truncated underneath us
        offset = 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read()

    if not data:
        return [], offset

    complete, _, partial = data.rpartition(b"\n")
    if not _:
        return [], offset  # no complete line yet
    new_offset = offset + len(data) - len(partial)

    records, seen = [], set()
    for raw in complete.split(b"\n"):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        rec = usage_of(entry)
        if rec is None:
            continue
        rid = rec["request_id"]
        if rid in seen or rid in skip:
            continue
        seen.add(rid)
        records.append(rec)
    return records, new_offset


def group_records(records: list[dict]) -> list[dict]:
    """Collapse a turn's requests into one row per (model, kind)."""
    groups: dict[tuple, dict] = {}
    for rec in records:
        key = (rec["model"], rec["kind"])
        row = groups.get(key)
        if row is None:
            row = groups[key] = {
                "model": rec["model"], "kind": rec["kind"],
                "ts": rec["ts"], "reqs": 0,
                **{k: 0 for k in TOKEN_KEYS},
            }
        row["reqs"] += 1
        for k in TOKEN_KEYS:
            row[k] += rec[k]
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
        return {"turn": 0, "offsets": {}, "seen": []}
    state.setdefault("turn", 0)
    state.setdefault("offsets", {})
    state.setdefault("seen", [])
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
    """
    buckets: dict = {}
    for row in rows:
        key = key_fn(row)
        b = buckets.get(key)
        if b is None:
            b = buckets[key] = {
                "key": key, "tasks": set(), "cost": 0.0,
                "cost_known": True, "first_ts": row.get("ts"),
                **{k: 0 for k in TOKEN_KEYS},
            }
        b["tasks"].add((row.get("session"), row.get("turn")))
        for k in TOKEN_KEYS:
            b[k] += row.get(k, 0)
        if row.get("cost_usd") is None:
            b["cost_known"] = False
        else:
            b["cost"] += row["cost_usd"]
        ts = row.get("ts")
        if ts and (not b["first_ts"] or ts < b["first_ts"]):
            b["first_ts"] = ts
    out = []
    for b in buckets.values():
        b["tasks"] = len(b["tasks"])
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
