# Claude Code token cost

A Claude Code plugin that records what each task actually costs based on the token usage, per project.

When a task finishes, a `Stop` hook reads the token usage that turn actually
consumed and appends it to a private ledger outside your repo. `/token-cost`
renders that ledger as a visual overview.

```
╭──────────────────────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│                   ▂▄▆█  Claude Token Cost v0.10.18  ·  token-cost                    │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

╭─ Project ──────────────────╮  ╭─ Models ─────────────────────────────────────────────╮
│                            │  │                                                      │
│  9 Tasks                   │  │  opus-5        7 Tasks        21.1M Tokens   $14.53  │
│  2026-08-23 → 2026-08-25   │  │  haiku-4-5     2 Tasks         1.3M Tokens    $0.29  │
│  22.4M Tokens              │  │                                                      │
│  $14.82                    │  ╰──────────────────────────────────────────────────────╯
│                            │
╰────────────────────────────╯

╭─ Cost Per Day ───────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│  08-23 ▕▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▏  10.4M   $7.25  │
│  08-24 ▕▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈▏   6.6M   $4.16  │
│  08-25 ▕▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈▏   5.4M   $3.41  │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

╭─ Most Expensive Tasks ───────────────────────────────────────────────────────────────╮
│                                                                                      │
│  Import history automatically on session start instead of asking       3.9M   $2.70  │
│  Record slash commands under the prompt that ran them                  3.4M   $2.31  │
│  Price the 1M context window separately                                3.1M   $2.24  │
│  Add a per-task breakdown to the report                                3.3M   $2.20  │
│  Give the overview a chart of spend per day                            3.0M   $2.05  │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯
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
| `/token-cost`          | the visual overview: project, models, days, and costly tasks |
| `/token-cost days`     | one row per day, plus a project total          |
| `/token-cost tasks`    | one row per task — prompt, model, what it cost |
| `/token-cost sessions` | one row per session, newest first              |
| `/token-cost today`    | today only: the model split, then its tasks    |
| `/token-cost week`     | the last 7 days, a row per day                 |
| `/token-cost month`    | the last 30 days, a row per day                |
| `/token-cost ui`       | opens the full-screen UI in a new window       |

They all read the same ledger, and every one of them is a tab of the
full-screen UI, printed. Each names its tab on the bar, leads with the
summary that tab leads with, and boxes its table the same way — sized to the
pane it lands in rather than to the terminal. Any view can be narrowed to a
period. The examples below are one small project seen several ways.

### `/token-cost days` — by day

```
╭──────────────────────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│                   ▂▄▆█  Claude Token Cost v0.10.18  ·  token-cost                    │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

╭─ By Model ───────────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│  MODEL                       TASKS  INPUT  OUTPUT  CACHE R  CACHE W     CTX  EST. $  │
│  opus-5                         16    393   81.5k    21.7M   474.4k  190.0k  $15.84  │
│  haiku-4-5                       3     25   10.4k     1.1M   100.3k  190.0k   $0.29  │
│  ──────────────────────────────────────────────────────────────────────────────────  │
│  TOTAL                          16    418   91.9k    22.8M   574.7k  190.0k  $16.13  │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

╭─ 2026-08-23 → 2026-08-25 ────────────────────────────────────────────────────────────╮
│                                                                                      │
│  DATE                                TASKS  INPUT  OUTPUT  CACHE R  CACHE W  EST. $  │
│  2026-08-23                              6    171   38.4k     9.9M   255.4k   $7.25  │
│  2026-08-24                              6    142   32.6k     7.7M   183.3k   $5.41  │
│  2026-08-25                              4    105   20.9k     5.2M   136.0k   $3.47  │
│  ──────────────────────────────────────────────────────────────────────────────────  │
│  TOTAL                                  16    418   91.9k    22.8M   574.7k  $16.13  │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

Estimated from published API rates; subscription plans are not billed per token.
Run /token-cost tasks for a per-task breakdown.
```

The detailed day view: the whole life of the project, a row per calendar day.
Days are local rather than UTC, so "today" means your today. TOTAL covers
every task ever recorded — including sessions whose transcripts Claude Code
has long since deleted.

### `/token-cost tasks` — by task

```
╭──────────────────────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│                   ▂▄▆█  Claude Token Cost v0.10.18  ·  token-cost                    │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

╭─ By Model ───────────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│  MODEL                       TASKS  INPUT  OUTPUT  CACHE R  CACHE W     CTX  EST. $  │
│  opus-5                         16    393   81.5k    21.7M   474.4k  190.0k  $15.84  │
│  haiku-4-5                       3     25   10.4k     1.1M   100.3k  190.0k   $0.29  │
│  ──────────────────────────────────────────────────────────────────────────────────  │
│  TOTAL                          16    418   91.9k    22.8M   574.7k  190.0k  $16.13  │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

╭─ Every Task ─────────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│  TIME         TASK                        MODEL      INPUT  OUTPUT  CACHE R  EST. $  │
│  08-25 16:11  /token-cost tasks           opus-5         1     189    26.2k   $0.05  │
│  08-25 15:52  Add a per-task breakdown …  opus-5        33    4.6k   951.2k   $0.85  │
│  08-25 15:28  Slash commands should rec…  opus-5        17    1.4k   446.6k   $0.29  │
│  08-25 14:53  Save the prompt that star…  opus-5 +1     54   14.7k     3.8M   $2.28  │
│  08-24 19:01  Add update instructions t…  opus-5        10    1.2k   427.4k   $0.28  │
│  08-24 18:36  Publish the plugin to the…  opus-5        25    5.4k     1.4M   $1.02  │
│  08-24 15:35  Add a today view broken d…  opus-5        20    4.6k     1.5M   $1.02  │
│  08-24 15:03  /token-cost sessions        opus-5         1     202    28.7k   $0.07  │
│  08-24 14:50  Guard the sync with a loc…  opus-5        17    1.5k   462.5k   $0.32  │
│  08-24 14:17  Import history automatica…  opus-5 +1     69   19.7k     3.9M   $2.70  │
│  08-23 20:10  Add a --backfill flag for…  opus-5        45   10.2k     2.9M   $2.30  │
│  08-23 19:42  Read subagent transcripts…  opus-5        19    4.2k     1.2M   $0.95  │
│  08-23 16:08  Write the README            opus-5        10    1.6k   266.4k   $0.21  │
│  08-23 15:32  Dedupe requests by reques…  opus-5 +1     58   15.5k     4.0M   $2.55  │
│  08-23 15:11  The hook fires but nothin…  opus-5        11    1.6k   390.9k   $0.29  │
│  08-23 14:44  Set up the project skelet…  opus-5        28    5.3k     1.2M   $0.95  │
│  ──────────────────────────────────────────────────────────────────────────────────  │
│  TOTAL                                                 418   91.9k    22.8M  $16.13  │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

Estimated from published API rates; subscription plans are not billed per token.
Narrow it with /token-cost tasks week or tasks month.
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
reaches Claude through the Bash tool, which carries about 30,000 characters
(the command allows itself 28,000 of them). Past that the model is handed a
file path and a preview instead of a table, so printing more doesn't produce a
longer table — it produces none. When a task list would cross that line the
page stays the page: same tab, same model split, and a table down to its
header and its total, with a note saying where the rows are:

```
╭──────────────────────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│                  ▂▄▆█  Claude Token Cost v0.10.18  ·  labfriend-v2                   │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

╭─ By Model ───────────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│  MODEL                   TASKS   INPUT  OUTPUT  CACHE R  CACHE W     CTX     EST. $  │
│  opus-5                    783   96.1k   19.9M  8496.8M    23.2M  190.0k  $6,976.89  │
│  haiku-4-5                 147    6.1k    2.5M   431.2M     4.9M  190.0k    $127.89  │
│  ──────────────────────────────────────────────────────────────────────────────────  │
│  TOTAL                     930  102.3k   22.5M  8928.0M    28.1M  190.0k  $7,104.78  │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

╭─ Every Task ─────────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│  TIME         TASK                    MODEL       INPUT  OUTPUT  CACHE R     EST. $  │
│  ──────────────────────────────────────────────────────────────────────────────────  │
│  TOTAL                                           102.3k   22.5M  8928.0M  $7,104.78  │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

930 rows is 83,392 characters — past the 28,000 a conversation can carry, so the table
would arrive as a file preview rather than rows.

  token-cost                 the full list, scrollable
  /token-cost tasks week     the last 7 days, in chat
```

The summary above it is the point: it is the part that still fits, and on a
project this size it is most of what you wanted anyway. Run `report.py` from a
shell and there is no budget at all — it prints all 930 rows. The ceiling only
applies to what has to travel through a chat message.

### `/token-cost sessions` — by session

```
╭──────────────────────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│                   ▂▄▆█  Claude Token Cost v0.10.18  ·  token-cost                    │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

╭─ Sessions ───────────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│  5 Sessions                                   3.2 Tasks/session   avg $3.23/session  │
│  Priciest 08-24 14:17 Import history automatically on session start i…        $4.11  │
│  Longest  08-24 14:17 Import history automatically on session start i…  6.1M Tokens  │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

╭─ 5 Sessions ─────────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│  SESSION   TIME         OPENED WITH           TASKS  INPUT  OUTPUT  CACHE R  EST. $  │
│  b7f51c83  08-25 14:53  Save the prompt tha…      4    105   20.9k     5.2M   $3.47  │
│  6d09b3f4  08-24 18:36  Publish the plugin …      2     35    6.6k     1.8M   $1.30  │
│  c41e7d92  08-24 14:17  Import history auto…      4    107   26.0k     5.9M   $4.11  │
│  3ba8d0e6  08-23 19:42  Read subagent trans…      2     64   14.4k     4.1M   $3.25  │
│  9f2c4a71  08-23 14:44  Set up the project …      4    107   24.0k     5.8M   $4.00  │
│  ──────────────────────────────────────────────────────────────────────────────────  │
│  TOTAL                                           16    418   91.9k    22.8M  $16.13  │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

Estimated from published API rates; subscription plans are not billed per token.
Run /token-cost tasks for a per-task breakdown.
```

One row per Claude Code session, newest first. OPENED WITH is the session's
first prompt, which is a better handle on "which session was that" than eight
characters of a uuid. Long sessions are worth watching: cache reads grow with
the conversation, so a session's tenth task costs more than its first.

The panel above it is the one thing a list ordered by recency can't show you:
how many sessions there are, what an average one costs, and the two outliers
— the priciest, and the one that read the most — which recency ordering
buries wherever they happen to fall.

### `/token-cost today` — a period, by model

```
╭──────────────────────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│                   ▂▄▆█  Claude Token Cost v0.10.18  ·  token-cost                    │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

╭─ Today, 2026-08-26 ──────────────────────────────────────────────────────────────────╮
│                                                                                      │
│  MODEL                          TASKS  INPUT  OUTPUT  CACHE R  CACHE W  CTX  EST. $  │
│  ──────────────────────────────────────────────────────────────────────────────────  │
│  TOTAL                              0      0       0        0        0    0   $0.00  │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

╭─ 0 Tasks ────────────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│  TIME   TASK                         MODEL  INPUT  OUTPUT  CACHE R  CACHE W  EST. $  │
│  ──────────────────────────────────────────────────────────────────────────────────  │
│  TOTAL                                          0       0        0        0   $0.00  │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

Estimated from published API rates; subscription plans are not billed per token.
```

Today only — your local calendar day. The model split first, then the tasks
that made it up, which is the order the UI's Today tab puts them in. The
per-model task counts overlap on purpose: one of today's four tasks also ran
a haiku subagent, so it appears on both rows while TOTAL still counts four
tasks.

`week` and `month` cover a longer window — the last 7 and last 30 days,
rolling, not calendar-aligned — and lead with the same split over a table of
days rather than tasks, because a week is too many tasks to read as a list
and its shape is a shape per day:

```
╭──────────────────────────────────────────────────────────────────────────────────────╮
│                                                                                      │
│                   ▂▄▆█  Claude Token Cost v0.10.18  ·  token-cost                    │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

╭─ Last 7 Days, 2026-08-20 → 2026-08-26 ───────────────────────────────────────────────╮
│                                                                                      │
│  MODEL                       TASKS  INPUT  OUTPUT  CACHE R  CACHE W     CTX  EST. $  │
│  opus-5                         16    393   81.5k    21.7M   474.4k  190.0k  $15.84  │
│  haiku-4-5                       3     25   10.4k     1.1M   100.3k  190.0k   $0.29  │
│  ──────────────────────────────────────────────────────────────────────────────────  │
│  TOTAL                          16    418   91.9k    22.8M   574.7k  190.0k  $16.13  │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

╭─ Last 7 Days, 2026-08-20 → 2026-08-26 ───────────────────────────────────────────────╮
│                                                                                      │
│  DATE                                TASKS  INPUT  OUTPUT  CACHE R  CACHE W  EST. $  │
│  2026-08-23                              6    171   38.4k     9.9M   255.4k   $7.25  │
│  2026-08-24                              6    142   32.6k     7.7M   183.3k   $5.41  │
│  2026-08-25                              4    105   20.9k     5.2M   136.0k   $3.47  │
│  ──────────────────────────────────────────────────────────────────────────────────  │
│  TOTAL                                  16    418   91.9k    22.8M   574.7k  $16.13  │
│                                                                                      │
╰──────────────────────────────────────────────────────────────────────────────────────╯

Estimated from published API rates; subscription plans are not billed per token.
Run /token-cost tasks for a per-task breakdown.
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

Nothing, in the ordinary case: no model is asked anything.

Recording never involved one — the `Stop` and `SessionStart` hooks are plain
Python, and reading a transcript to write a row needs no judgement.

Printing used to. Inline shell output is substituted into the prompt rather
than shown directly, so the command asked the model to echo it back, and a
page of table is expensive to retype: 2929 output tokens for one overview,
arriving a line at a time while you watch. A `UserPromptSubmit` hook now
recognises the command before it expands, prints the report itself and stops
the turn — the page arrives whole, and the model is never woken.

The hook sees every prompt you type, so it is built to be cheap and to be
wrong in the safe direction. A prompt that isn't this command costs one
interpreter start, about 30ms, and nothing heavier is imported until the text
matches. Matching is strict about the whole prompt rather than its opening,
because `/token-cost is slow, fix it` is a question for the model and
answering it with a table would be worse than being slow. Anything it doesn't
recognise — an unknown argument, an unreadable ledger — falls through
untouched, and the slash command answers it the old way.

`TOKEN_COST_NO_INTERCEPT=1` always takes that older route.

It is still worth keeping cheap, because it is the fallback:

```yaml
model: haiku
effort: low
```

That override lasts for that turn only; your session model resumes on your
next prompt. To change it, edit `model:` in `commands/token-cost.md` — it takes
the same values as `/model`, or `inherit` to stay on whatever the session is
using.

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
than Claude Code's retention window is dropped — it trades history away for a
clean slate.

## The UI

`/token-cost` prints a tab of the UI into the conversation — the overview by
default, or whichever tab you name. Not a summary of that tab: the same
chrome, the same panels, the same titles, the same columns and cells, drawn
to fit the pane it lands in.

Claude Code hands the report a pipe rather than a terminal, so it finds the
width by asking the terminal Claude Code itself is attached to, and every
panel, bar and column is sized from that. A panel too narrow to hold its
figures gives up words first and whole columns after, in that order; it never
gives up a row.

The two share the code that decides all of it — which tabs exist, what each
one holds, how a table's columns fit a width, where a row's figures sit — so
they can't drift into two different-looking things. `dev/smoke_report.py`
checks that they haven't.

`token-cost` opens the same ledger as a full-screen app, with tabs and no
limit on how much it can show. It holds its shape at any size: panels stack
when the terminal narrows, rows shed their words and then their columns,
and every box keeps its own four edges down to a window too small to be
worth opening.

```
  ╭──────────────────────────────────────────────────────────────────────────────────────────────────────╮
  │                                                                                                      │
  │                           ▂▄▆█  Claude Token Cost v0.10.18  ·  token-cost                            │
  │                                                                                                      │
  ├──────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │  ▌ Overview     Today     This Week     This Month     All Tasks     Sessions                        │
  ╰──────────────────────────────────────────────────────────────────────────────────────────────────────╯

  ╭─ Project ──────────────────────╮  ╭─ Models ─────────────────────────────────────────────────────────╮
  │                                │  │                                                                  │
  │  96 Tasks                      │  │  opus-5 (1m)     46 Tasks               198.4M Tokens   $128.34  │
  │  2026-08-24 → 2026-08-26       │  │  fable-5 (1m)    11 Tasks                42.3M Tokens    $58.38  │
  │  311.1M Tokens                 │  │  fable-5         16 Tasks                21.0M Tokens    $43.40  │
  │  $274.80                       │  │  opus-5          25 Tasks                46.4M Tokens    $42.07  │
  │                                │  │  sonnet-5         4 Tasks                 2.1M Tokens     $2.38  │
  ╰────────────────────────────────╯  │                                                                  │
                                      ╰──────────────────────────────────────────────────────────────────╯

  ╭─ Cost Per Day ───────────────────────────────────────────────────────────────────────────────────────╮
  │  08-25 ▕▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▏  224.2M   $204.03  │
  │  08-26 ▕▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈▏   64.7M    $54.22  │
  ╰──────────────────────────────────────────────────────────────────────────────────────────────────────╯

  ╭─ Most Expensive Tasks ───────────────────────────────────────────────────────────────────────────────╮
  │                                                                                                      │
  │  Can we migrate the output to TUI instead of plain table like what we have? I'd l…   24.7M   $16.48  │
  │                                                                                                      │
  ╰──────────────────────────────────────────────────────────────────────────────────────────────────────╯

  Click or ←/→ Tabs · ↑/↓ Scroll · r Refresh · q/Esc Quit
```

Tabs: **Overview**, **Today**, **This Week**, **This Month**, **All Tasks**,
**Sessions**. `←`/`→` or `1`–`6` to move between them, `↑`/`↓` and PgUp/PgDn
to scroll, `g`/`G` for top and bottom, `r` to re-read the ledger, `q` to quit.
**All Tasks** and **Sessions** each have a search input inside the table. Click
it or press `/` to focus it; typing filters names live, Enter keeps the filter,
and Esc cancels an edit or clears an applied filter.
The mouse counts too: click a tab (or the arrows in the compact nav) to open
it, point at a table row to light it up, and use the wheel to scroll.

The Tasks tab is the one the conversation can't carry — every task on record,
scrollable, however many there are:

```
  ╭──────────────────────────────────────────────────────────────────────────────────────────────────────╮
  │                                                                                                      │
  │                           ▂▄▆█  Claude Token Cost v0.10.18  ·  token-cost                            │
  │                                                                                                      │
  ├──────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │    Overview     Today     This Week     This Month   ▌ All Tasks     Sessions                        │
  ╰──────────────────────────────────────────────────────────────────────────────────────────────────────╯

  ╭─ Every Task ─────────────────────────────────────────────────────────────────────────────────────────╮
  │  ╭────────────────────────────────────────────────────────────────────────────────────────────────╮  │
  │  │  Press / or click, then type to filter tasks                                                   │  │
  │  ╰────────────────────────────────────────────────────────────────────────────────────────────────╯  │
  │  TIME         TASK                         MODEL           INPUT  OUTPUT  CACHE R  CACHE W   EST. $  │
  │  08-26 13:56  continue                     opus-5 (1m)        46   22.4k     8.4M   368.2k    $8.44  │
  │  08-26 04:33  you stuck?                   opus-5 (1m)        16    6.3k     2.9M     9.4k    $1.68  │
  │  08-26 04:26  [image] bro where's the cl…  opus-5 (1m)        30   26.1k     4.9M    40.3k    $3.52  │
  │  08-26 04:23  publish and update the loc…  opus-5 (1m)         6    1.3k   924.2k     1.9k    $0.51  │
  │  08-26 04:18  /goal sweet. now lets do t…  opus-5 (1m)         4    1.9k   609.8k     2.6k    $0.38  │
  │  08-26 04:04  /goal sweet. now lets do t…  opus-5 (1m)        78   57.4k    10.6M    76.6k    $7.49  │
  │  08-26 03:55  [image] bro the tty UI suc…  opus-5 (1m)         8    2.3k   901.8k     1.8k    $0.53  │
  │  08-26 03:35  [image] bro the tty UI suc…  opus-5 +1         156   90.3k    11.8M   197.3k   $10.13  │
  │  08-26 00:44  yes implement, but dont co…  sonnet-5            4   22.3k   365.4k    30.5k    $0.63  │
  │  08-26 00:37  continue                     opus-5             16    3.5k     2.0M     3.7k    $1.15  │
  │  08-26 00:29  but claude code shows "Chu…  sonnet-5           10   33.3k   797.7k    30.8k    $0.85  │
  │  08-26 00:24  but claude code shows "Chu…  sonnet-5            4    3.6k   285.4k     5.0k    $0.11  │
  │  ──────────────────────────────────────────────────────────────────────────────────────────────────  │
  │  TOTAL                                                      3.4k    1.5M   305.9M     3.7M  $274.80  │
  │                                                                                                      │
  ╰──────────────────────────────────────────────────────────────────────────────────────────────────────╯
  Click or ←/→ Tabs · ↑/↓ Scroll · r Refresh · q/Esc Quit
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

A copy of [`scripts/token-cost`](scripts/token-cost) is written to `~/.local/bin`
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
stays on your machine. A task whose prompt can't be reconstructed is named by
the session's `ai-title` — the tldr Claude Code itself generates for the
resume picker — and one with neither shows as `—`.

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

A model id is not the whole price. The same `claude-opus-5` request costs
twice as much under `/fast`, and 10% more with inference pinned to the US —
and neither shows up in the model name. Both are reported in the request's
own `usage` block, so they are read off it rather than inferred: `usage.speed`,
`usage.inference_geo`, and `usage.service_tier` each select the rate the
request was really billed at. A turn that changed mode mid-flight splits into
one row per mode, and a row priced as anything but standard carries a `bill`
field and shows the reason next to the model — `opus-5 (fast)` — so a doubled
rate never reads as a model that got expensive. A modifier with no known
factor prices as `?`; a modifier exists because it changes the price, so
guessing it away as standard is the one answer certain to be wrong.

### 1M context

Running a session on a 1M-context model — `claude-opus-5[1m]` in the model
picker — never shows up in the transcript's model id: Claude Code stores the
API's response, and the API answers with the canonical `claude-opus-5`. Hooks
don't carry it either (only `SessionStart` may name a model, undocumented to
survive a mid-session `/model` switch). What every request *does* carry is
its own usage block, and that is the stronger record: a prompt of more than
200K tokens can only have run in the 1M window, and the usage block counts
exactly how many tokens each request sent.

So the recorder measures every request against the 200K line. One that
crosses it is recorded with `bill: {"long_context": true}` and reports as its
own row — `opus-5 (1m)` — and every row carries `ctx`, the largest single
prompt among its requests, which the by-model views show as `CTX`:

```
MODEL         TASKS  INPUT  OUTPUT  CACHE R  CACHE W     CTX   EST. $
opus-5           54   1.1k  643.5k   171.9M     1.5M  147.8k  $116.59
opus-5 (1m)       2     18    9.1k     4.3M    15.2k  685.8k    $3.12
```

What the split costs is the same per token, and that is verified two ways,
not assumed: Anthropic's pricing page states that Claude 4.6+ include the
full 1M window at standard rates ("a 900k-token request is billed at the same
per-token rate as a 9k-token request"), and Claude Code's own per-project
`costUSD` (in `~/.claude.json`, keyed by the `[1m]` id) reconstructs to
exactly the standard rate table against the token counts stored beside it.
The one model that ever did bill a premium above 200K — Sonnet 4.5's retired
1M beta — carries its real tier rates in `pricing.json` ($6/$22.50 against
$3/$15), so an old transcript from that era backfills at what it actually
cost. If a premium ever returns, it's rate keys on a model's existing
`long_context` block — a data edit the recorder already knows how to apply.

## Proving it

`dev/verify_ledger.py` re-derives the ledger three independent ways, and
passes only when all three agree:

- **Arithmetic** — every stored row's dollars recompute from its own token
  counts, billing modifiers, and the rate table.
- **Replay** — every session with a surviving transcript is re-scanned to
  exactly the byte offsets the recorder stopped at, and must reproduce the
  ledger's tokens, request counts, and dollars to the row.
- **Cross-check** — Claude Code's own per-project cost accounting
  (`lastModelUsage.costUSD` in `~/.claude.json`) is re-priced through our
  rate table and must land inside the band the two cache-write TTLs allow.
  Two implementations agreeing on the money, one of them Anthropic's.

`dev/smoke_report.py` holds the chat report to two properties.

It fits. Every panel and every table, in every view the command offers, is
rendered at every width from the layout's floor to past its ceiling — with
and without a budget, against synthetic ledgers and this project's real one
— and each render is measured: no line over the width, no frame that stops
short of its own border, no row dropped to make a table fit.

And it agrees. The report claims to be the UI printed, so the check runs the
real UI under a pseudo-terminal at three sizes, reads its cells back out of
curses, and requires every line it drew to be a line the report printed at
the same width. Not the reverse — the report has no bottom, so it shows rows
the terminal had no room for. Both read one frozen copy of the ledger,
because this plugin records a row every time a task finishes, including the
tasks running the check. What neither can say is whether the numbers are
right; that's the verifier above.

`dev/smoke_tui.py` drives the real UI under a pseudo-terminal — every tab,
the mouse, the search input, sizes from a postage stamp to a wall. Then it
reads the cells back out of curses at eight terminal sizes and checks the
one property a full-screen layout has to hold whatever room it is given:
every box that opens closes, and nothing paints over the edges in between.
A label one column too long for its panel doesn't fall off the screen — it
lands on that panel's border, and the frame stops reading as a frame.

`dev/bench_tui.py` does the same for the UI, which has its own kind of
correctness: a table you can't scroll smoothly is a table you don't read. It
starts the real app in a pseudo-terminal and times whole frames — the budget
is 16.67ms, which is one frame at 60fps — and reports the median and the
95th percentile per tab, because a UI that is fast on average and stutters
every tenth frame is a UI that stutters. `--synthetic N` measures against a
ledger of N rows rather than this project's, since the frame that matters is
the one a heavy project pays for. At 12,000 rows every frame lands under
1.3ms, hover and scroll included.

Two things keep it there. Nothing is rolled up on the way into a frame: the
views, and the text of every cell in them, are built when the ledger is read
and kept until it is read again, so drawing costs the rows on screen rather
than the rows on disk. And the roll-ups happen while nobody is waiting —
the input loop builds the tabs you haven't opened yet during the lulls
between keystrokes, giving up the instant a key arrives.

## Limits

- **Costs are estimates** computed from published API rates. On a subscription
  plan nothing is billed per token, so read the figure as "what this would
  have cost on the API".
- **Imported task counts are approximate** — turn boundaries are
  reconstructed from the transcript. Token and cost totals are exact.
- **The ledger format is not stable.** This is a work in progress: a new
  version may change what a row holds without reading what an old one wrote.
  When that happens, rebuild from the transcripts still on disk with
  `--backfill --force` rather than expecting old rows to be understood.
- **A session's own hooks load at startup**, so a plugin installed or updated
  mid-session records nothing further until you restart. Nothing is lost:
  the next sync reads that session's transcript from where recording stopped.
- Backfill can only reach as far back as Claude Code still keeps transcripts.
