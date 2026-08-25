# token-cost

A Claude Code plugin that records what each task costs, per project.

When a task finishes, a `Stop` hook reads the token usage that turn actually
consumed and appends it to a private ledger outside your repo. `/token-cost`
renders that ledger as a table.

```
Project: token-cost    16 tasks    2026-08-23 → 2026-08-25

DATE        TASKS  INPUT  OUTPUT  CACHE R  CACHE W  EST. $
2026-08-23      6    161   34.8k     9.6M   214.0k   $7.25
2026-08-24      6    134   29.0k     7.3M   152.3k   $5.41
2026-08-25      4     98   17.6k     4.8M   108.2k   $3.48
──────────────────────────────────────────────────────────
TOTAL          16    393   81.4k    21.8M   474.4k  $16.14
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

| Command                | Shows                                          |
| ---------------------- | ---------------------------------------------- |
| `/token-cost`          | one row per day, plus a project total          |
| `/token-cost tasks`    | one row per task — prompt, model, what it cost |
| `/token-cost sessions` | one row per session, newest first              |
| `/token-cost today`    | today only, broken down by model               |
| `/token-cost week`     | the last 7 days, broken down by model          |
| `/token-cost month`    | the last 30 days, broken down by model         |
| `/token-cost ui`       | opens the full-screen UI in a new window       |

They all read the same ledger and end with a TOTAL row; they differ only in
what a row means, and any of them can be narrowed to a period. The examples
below are one small project seen several ways.

### `/token-cost` — by day

```
Project: token-cost    16 tasks    2026-08-23 → 2026-08-25

DATE        TASKS  INPUT  OUTPUT  CACHE R  CACHE W  EST. $
2026-08-23      6    161   34.8k     9.6M   214.0k   $7.25
2026-08-24      6    134   29.0k     7.3M   152.3k   $5.41
2026-08-25      4     98   17.6k     4.8M   108.2k   $3.48
──────────────────────────────────────────────────────────
TOTAL          16    393   81.4k    21.8M   474.4k  $16.14
```

The default view: the whole life of the project, a row per calendar day. Days
are local rather than UTC, so "today" means your today. TOTAL covers every
task ever recorded — including sessions whose transcripts Claude Code has
long since deleted.

### `/token-cost tasks` — by task

```
Project: token-cost    16 tasks    every task

WHEN         TASK                                                  MODEL      INPUT  OUTPUT  CACHE R  CACHE W  EST. $
08-25 10:41  /token-cost tasks                                     opus-5         1     189    26.2k     3.5k   $0.05
08-25 10:22  Add a per-task breakdown to the report                opus-5        33    4.6k   951.2k    26.4k   $0.85
08-25 09:58  Slash commands should record as the command, not th…  opus-5        17    1.4k   446.6k     3.5k   $0.29
08-25 09:23  Save the prompt that started each task                opus-5 +1     47   11.5k     3.4M    74.7k   $2.28
08-24 13:31  Add update instructions to the README                 opus-5        10    1.2k   427.4k     3.4k   $0.28
08-24 13:06  Publish the plugin to the marketplace                 opus-5        25    5.4k     1.4M    15.7k   $1.02
08-24 10:05  Add a today view broken down by model                 opus-5        20    4.6k     1.5M    17.6k   $1.02
08-24 09:33  /token-cost sessions                                  opus-5         1     202    28.7k     5.2k   $0.07
08-24 09:20  Guard the sync with a lock, two sessions can race     opus-5        17    1.5k   462.5k     5.1k   $0.32
08-24 08:47  Import history automatically on session start inste…  opus-5 +1     61   16.1k     3.5M   105.3k   $2.70
08-23 14:40  Add a --backfill flag for sessions that predate the…  opus-5        45   10.2k     2.9M    58.6k   $2.30
08-23 14:12  Read subagent transcripts too — they're billed sepa…  opus-5        19    4.2k     1.2M    24.6k   $0.95
08-23 10:38  Write the README                                      opus-5        10    1.6k   266.4k     3.4k   $0.21
08-23 10:02  Dedupe requests by requestId, we're counting every…   opus-5 +1     48   11.9k     3.6M   101.9k   $2.55
08-23 09:41  The hook fires but nothing lands in the ledger — fi…  opus-5        11    1.6k   390.9k     5.0k   $0.29
08-23 09:14  Set up the project skeleton and wire the Stop hook    opus-5        28    5.3k     1.2M    20.5k   $0.95
─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
TOTAL                                                                           393   81.4k    21.8M   474.4k  $16.14
```

Newest first, each task named by the prompt that opened it — so an expensive
turn is identifiable rather than anonymous. Three of the sixteen tasks above
account for nearly half of what the project cost.

`opus-5 +1` means one other model also worked on that task, usually a
subagent. The model named is the one that carried the spend, not just the
first one seen.

Every task is listed. No cut-off, no "latest N" — a table that quietly drops
rows is worse than a long one, because nothing on screen tells you something
is missing. Use a period filter when you want less (below).

In the conversation there is one limit, and it isn't ours: inline shell output
reaches Claude through the Bash tool, which carries about 30,000 characters.
Past that the model is handed a file path and a preview instead of a table, so
printing more doesn't produce a longer table — it produces none. When a task
list would cross that line, the command prints the totals and says where the
rows are rather than pretending:

```
Project: labfriend-v2    930 tasks    every task

WHEN         TASK                          MODEL       INPUT  OUTPUT  CACHE R  CACHE W     EST. $
─────────────────────────────────────────────────────────────────────────────────────────────────
TOTAL                                                  82.9k    7.4M  1479.0M    34.0M  $1,325.84

930 rows is 91,433 characters — past the ~30,000 a conversation
can carry, so the table would arrive as a file preview rather than rows.

  token-cost                 the full list, scrollable
  /token-cost tasks week     the last 7 days, in chat
```

Run `report.py` from a shell and there is no budget at all — it prints all 930
rows. The ceiling only applies to what has to travel through a chat message.

### `/token-cost sessions` — by session

```
Project: token-cost    16 tasks    5 sessions

SESSION   STARTED      OPENED WITH                         TASKS  INPUT  OUTPUT  CACHE R  CACHE W  EST. $
b7f51c83  08-25 09:23  Save the prompt that started each…      4     98   17.6k     4.8M   108.2k   $3.48
6d09b3f4  08-24 13:06  Publish the plugin to the marketp…      2     35    6.6k     1.9M    19.1k   $1.29
c41e7d92  08-24 08:47  Import history automatically on s…      4     99   22.4k     5.5M   133.2k   $4.12
3ba8d0e6  08-23 14:12  Read subagent transcripts too — t…      2     64   14.3k     4.1M    83.2k   $3.25
9f2c4a71  08-23 09:14  Set up the project skeleton and w…      4     97   20.5k     5.5M   130.7k   $4.00
─────────────────────────────────────────────────────────────────────────────────────────────────────────
TOTAL                                                         16    393   81.4k    21.8M   474.4k  $16.14
```

One row per Claude Code session, newest first. OPENED WITH is the session's
first prompt, which is a better handle on "which session was that" than eight
characters of a uuid. Long sessions are worth watching: cache reads grow with
the conversation, so a session's tenth task costs more than its first.

### `/token-cost today` — a period, by model

```
Project: token-cost    4 tasks    today, 2026-08-25

MODEL      TASKS  INPUT  OUTPUT  CACHE R  CACHE W  EST. $
opus-5         4     91   14.5k     4.5M    80.2k   $3.39
haiku-4-5      1      7    3.2k   351.0k    27.9k   $0.09
─────────────────────────────────────────────────────────
TOTAL          4     98   17.6k     4.8M   108.2k   $3.48
```

Today only — your local calendar day — split by model. The per-model task
counts overlap on purpose: one of today's four tasks also ran a haiku
subagent, so it appears on both rows while TOTAL still counts four tasks.

`week` and `month` are the same view over a longer window — the last 7 and
last 30 days, rolling, not calendar-aligned:

```
Project: token-cost    16 tasks    last 7 days, 2026-08-19 → 2026-08-25

MODEL      TASKS  INPUT  OUTPUT  CACHE R  CACHE W  EST. $
opus-5        16    368   71.0k    20.7M   374.1k  $15.85
haiku-4-5      3     25   10.4k     1.1M   100.3k   $0.29
─────────────────────────────────────────────────────────
TOTAL         16    393   81.4k    21.8M   474.4k  $16.14
```

### Narrowing any view

A period is a filter, not a mode, so it composes with the other views:

```
/token-cost tasks week        every task in the last 7 days
/token-cost sessions month    every session in the last 30 days
/token-cost days month        a row per day for the last 30 days
```

This is the only thing that ever shortens a report. Whatever the window,
every row inside it is printed and TOTAL covers exactly what you can see.

### What running this costs

Recording costs nothing at all: the `Stop` and `SessionStart` hooks are plain
Python, and no model is involved in reading a transcript or writing a row.

The only model in the loop is the one that prints the table. Inline shell
output is substituted into the prompt rather than shown directly, so the
command asks for it to be echoed back — transcription, with no judgement in
it, and no reason to spend a frontier model on. The command is pinned to the
cheapest model that can copy a wide table faithfully:

```yaml
model: haiku
```

The override lasts for that turn only; your session model resumes on your next
prompt. To change it, edit `model:` in `commands/token-cost.md` — it takes the
same values as `/model`, or `inherit` to stay on whatever the session is using.
Adding `effort: low` alongside it trims the cost further.

### Keeping the ledger current

The ledger is brought up to date on every session start, and again whenever
you run one of these — so it also picks up sessions another machine recorded.
Once a project is synced that check costs about 40ms, even across 500
sessions; a cold import of 500 sessions takes about two.

If you ever want to force a re-import from scratch:

```
python3 scripts/record.py --backfill --force --cwd "$PWD"
```

That rebuilds the ledger from the transcripts still on disk, so anything older
than Claude Code's retention window is dropped. It is how you re-label old
rows after an upgrade, but it trades history away to do it.

## The UI

`/token-cost` prints a table into the conversation. `token-cost` opens the
same ledger as a full-screen app, with tabs and no limit on how much it can
show:

```
  ╭────────────────────────────────────────────────────────────────────────────────────────────────────────╮
  │                                                                                                        │
  │                                 ▂▄▆█  token-cost v0.8.0  ·  token-cost                                 │
  │                                                                                                        │
  ╰────────────────────────────────────────────────────────────────────────────────────────────────────────╯

            ─────┬─────────┬──────────┬─────┬──────────────────────────────────────────────────────────────╮
   Overview Today│This Week│This Month│Tasks│Sessions                                                      │
            ─────┴─────────┴──────────┴─────┴──────────────────────────────────────────────────────────────╯
  ╭─ Project ───────────────────────╮  ╭─ Models ──────────────────────────────────────────────────────────╮
  │                                 │  │                                                                   │
  │  16 Tasks                       │  │  opus-5            16 Tasks                               $15.85  │
  │  2026-08-23 → 2026-08-25        │  │  haiku-4-5          3 Tasks                                $0.29  │
  │  $16.14                         │  │                                                                   │
  │                                 │  │                                                                   │
  ╰─────────────────────────────────╯  ╰───────────────────────────────────────────────────────────────────╯

  ╭─ Cost Per Day ─────────────────────────────────────────────────────────────────────────────────────────╮
  │                                                                                                        │
  │  08-25 ▕▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈▏      $3.48  │
  │                                                                                                        │
  ╰────────────────────────────────────────────────────────────────────────────────────────────────────────╯

  ╭─ Most Expensive Tasks ─────────────────────────────────────────────────────────────────────────────────╮
  │                                                                                                        │
  │  Import history automatically on session start instead of asking                                $2.70  │
  │                                                                                                        │
  ╰────────────────────────────────────────────────────────────────────────────────────────────────────────╯

  ←/→ Tabs · ↑/↓ Scroll · r Refresh · q Quit
```

Tabs: **Overview**, **Today**, **This Week**, **This Month**, **Tasks**,
**Sessions**. `←`/`→` or `1`–`6` to move between them, `↑`/`↓` and PgUp/PgDn
to scroll, `g`/`G` for top and bottom, `r` to re-read the ledger, `q` to quit.

The Tasks tab is the one the conversation can't carry — every task on record,
scrollable, however many there are:

```
  ╭────────────────────────────────────────────────────────────────────────────────────────────────────────╮
  │                                                                                                        │
  │                                 ▂▄▆█  token-cost v0.8.0  ·  token-cost                                 │
  │                                                                                                        │
  ╰────────────────────────────────────────────────────────────────────────────────────────────────────────╯

  ╭────────┬─────┬─────────┬──────────       ──────────────────────────────────────────────────────────────╮
  │Overview│Today│This Week│This Month Tasks Sessions                                                      │
  ╰────────┴─────┴─────────┴──────────       ──────────────────────────────────────────────────────────────╯
  ╭─ Every Task ───────────────────────────────────────────────────────────────────────────────────────────╮
  │                                                                                                        │
  │  WHEN         TASK                                 MODEL      INPUT  OUTPUT  CACHE R  CACHE W  EST. $  │
  │  08-25 10:41  /token-cost tasks                    opus-5         1     189    26.2k     3.5k   $0.05  │
  │  08-25 10:22  Add a per-task breakdown to the re…  opus-5        33    4.6k   951.2k    26.4k   $0.85  │
  │  08-25 09:58  Slash commands should record as th…  opus-5        17    1.4k   446.6k     3.5k   $0.29  │
  │  ────────────────────────────────────────────────────────────────────────────────────────────────────  │
  │  TOTAL                                                          393   81.4k    21.8M   474.4k  $16.14  │
  │                                                                                                        │
  ╰────────────────────────────────────────────────────────────────────────────────────────────────────────╯
  ←/→ Tabs · ↑/↓ Scroll · r Refresh · q Quit
```

### Starting it

```
token-cost
```

That's it — there is no install step. Installing the plugin installs the
command: it appears on your PATH at the first session start after
`/plugin install`, which is the same restart the plugin needs anyway. Running
`/token-cost` puts it there too, so it exists whether or not hooks are on.

`/token-cost ui` opens the UI for you in a new terminal window, pointed at
the project you're working in. A slash command can't host a full-screen app
itself — its shell output is captured text with no TTY attached — but it can
open a window that can. Where that isn't possible (not macOS, no `osascript`),
it prints the one command to run instead of doing nothing.

<details>
<summary>What gets installed, and where</summary>

A copy of [`bin/token-cost`](bin/token-cost) is written to `~/.local/bin`
(or `$XDG_BIN_HOME`) by a `SessionStart` hook, and again by `/token-cost`
itself. Nothing can run at the moment you install a plugin — Claude Code has
no install-time hook, and a plugin's hooks don't exist until it loads — so
the first session start is the earliest this can happen. It is a copy rather than a
symlink on purpose: Claude Code caches each plugin version in its own
directory, so a symlink would point into a directory that the next update
replaces and eventually prunes. The copy resolves the newest installed
plugin every time it runs, so it survives updates — including updates to
itself, since the hook rewrites the file whenever the launcher changes.

It never overwrites a `token-cost` it didn't write, the file says where it
came from and that it's safe to delete, and `TOKEN_COST_NO_SHIM=1` turns the
whole thing off.

</details>

It needs nothing beyond `python3` — the UI is stdlib `curses`. Box-drawing and
colour are used when the locale supports them and ASCII stands in when it
doesn't, so `LC_ALL=C` degrades instead of drawing blanks.

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

One ledger line per task, per model. This is the "Save the prompt that started
each task" row from the tasks table above, before the haiku subagent line that
shares its turn is folded in:

```json
{"ts":"2026-08-25T09:23:00Z","session":"b7f51c83-…","turn":1,
 "prompt":"Save the prompt that started each task",
 "model":"claude-opus-5","kind":"main","reqs":30,
 "input":40,"output":8273,"cache_read":3033033,
 "cache_write_5m":0,"cache_write_1h":46790,"cost_usd":2.1914415}
```

`prompt` is the first 140 characters of the prompt that opened the task,
flattened to one line — enough to recognise the task, not a copy of the
conversation. Slash commands are recorded as the command, and a task a
subagent notification woke is recorded as that notification's summary. It is
the one piece of your own text the ledger holds; like everything else here it
stays on your machine. Rows written before this existed show as `—`.

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
- **Imported task counts are approximate** — turn boundaries are
  reconstructed from the transcript. Token and cost totals are exact.
- **A session's own hooks load at startup**, so a plugin installed or updated
  mid-session records nothing further until you restart. Nothing is lost:
  the next sync reads that session's transcript from where recording stopped.
- Backfill can only reach as far back as Claude Code still keeps transcripts.
