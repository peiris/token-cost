# token-cost

A Claude Code plugin that records what each task costs, per project.

When a task finishes, a `Stop` hook reads the token usage that turn actually
consumed and appends it to a private ledger outside your repo. `/token-cost`
renders that ledger as a table.

```
Project: token-cost    54 tasks    2026-08-22 → 2026-08-24

DATE        TASKS  INPUT  OUTPUT  CACHE R  CACHE W   EST. $
2026-08-22     14   1.2k   41.0k     3.1M   198.0k    $2.10
2026-08-23     31   2.6k   98.7k     7.2M   612.0k    $6.44
2026-08-24      9   1.2k   56.6k     2.4M   294.0k    $4.40
───────────────────────────────────────────────────────────
TOTAL          54   5.0k  196.3k    12.7M     1.1M   $12.94
```

## Why

`/cost` only covers the current session, and Claude Code deletes transcripts
after about 30 days. This ledger is append-only and permanent, so a project's
history keeps accumulating long after the transcripts behind it are gone.

## Install

```
/plugin marketplace add peiris/token-cost
/plugin install token-cost@token-cost
```

That's it. Nothing else to run. When a session starts, the plugin imports
whatever history Claude Code still has on disk for that project, and from
then on every completed task is recorded as it happens — so `/token-cost`
shows real numbers the first time you use it.

Requires `python3` (stdlib only — no dependencies).

<details>
<summary>Installing from a local clone instead</summary>

```
git clone https://github.com/peiris/token-cost
/plugin marketplace add /path/to/token-cost
/plugin install token-cost@token-cost
```

</details>

## Updating

```
/plugin marketplace update token-cost
/plugin update token-cost@token-cost
```

The update lands on disk immediately but applies on restart. Check what you're
running with `claude plugin list` and compare against the `version` in
[`.claude-plugin/plugin.json`](.claude-plugin/plugin.json) on `main`.

To remove the plugin entirely:

```
/plugin uninstall token-cost@token-cost
```

Your ledger is left alone either way — delete the `token-cost/` directory
inside your Claude Code data directory if you want the recorded history gone.

## Commands

| Command                | Shows                                        |
| ---------------------- | -------------------------------------------- |
| `/token-cost`          | one row per day, plus a project total         |
| `/token-cost sessions` | one row per session, newest first             |
| `/token-cost today`    | today only, broken down by model              |

The ledger is brought up to date on every session start, and again whenever
you run one of these — so it also picks up sessions another machine recorded.
Once a project is synced that check costs about 40ms, even across 500
sessions; a cold import of 500 sessions takes about two.

If you ever want to force a re-import from scratch:

```
python3 scripts/record.py --backfill --force --cwd "$PWD"
```

## Where the data lives

```
<claude-data-dir>/token-cost/<project-key>.jsonl     the ledger
<claude-data-dir>/token-cost/.state/<session>.json   per-session scan cursors
```

`<claude-data-dir>` is wherever Claude Code already keeps its data — usually
`~/.claude`, or `$CLAUDE_CONFIG_DIR` if you have moved it. The plugin doesn't
assume: the hooks are handed a real transcript path and read the location off
that, so a relocated or shared config directory works without configuration.

Nothing is written inside your project, so there is no chance of committing it
by accident, and it survives re-cloning the repo. Nothing leaves your machine.

One ledger line per task, per model:

```json
{"ts":"2026-08-24T23:40:11Z","session":"98351a41-…","turn":7,
 "model":"claude-opus-5","kind":"main","reqs":4,
 "input":412,"output":3180,"cache_read":1204331,
 "cache_write_5m":0,"cache_write_1h":88210,"cost_usd":0.9421}
```

## How the numbers are produced

Claude Code writes each content block of a reply as its own transcript line,
all sharing one `requestId` and one *identical* usage block. Counting them
line-by-line over-counts tokens by roughly 2.5×, so every request is deduped
by `requestId` before anything is summed.

Subagents are billed separately, write to their own transcript files, and
often run a different model than the main loop — those are read too, and show
up with `"kind":"subagent"`.

Cache writes are tracked separately by TTL, because a 1-hour write costs 2×
base input while a 5-minute write costs 1.25×.

Rates live in `scripts/pricing.json` and are applied at write time, so old
rows stay correct after prices change. A model with no entry records
`cost_usd: null` and reports as `?` rather than silently counting as free.

## Limits

- **Costs are estimates** computed from published API rates. On a subscription
  plan nothing is billed per token, so read the figure as "what this would
  have cost on the API".
- **No long-context premium.** Transcripts record `claude-opus-5` with no 1M
  marker, so the higher price tier for >200K-token inputs can't be detected.
  Long-context-heavy projects read low.
- **Backfilled task counts are approximate** — turn boundaries are
  reconstructed from the transcript. Token and cost totals are exact.
- Backfill can only reach as far back as Claude Code still keeps transcripts.
