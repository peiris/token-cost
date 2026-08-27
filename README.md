# Claude Code Token Cost

Claude Code plugin that tracks token usage and estimated cost per task and project

When a task finishes, the `Stop` hook reads the token usage that session's tasks consumed
and appends it to a private JSON in your local user directory. `/token-cost`
renders that JSON as a visual overview.

The claude code built-in `/cost` command covers only the totals of current session, and claude deletes transcripts after about 30 days by default. The JSON is kept indefinitely.

## Install

```
$ claude
```

```
/plugin marketplace add peiris/token-cost
/plugin install token-cost@token-cost
```

When a Claude Code session starts, the plugin imports whatever history Claude Code still has on disk for that project, and from then on every completed task is recorded as it happens.

<details>
<summary>Installing from a local clone instead</summary>

```
git clone https://github.com/peiris/token-cost
```

```
/plugin marketplace add /path/to/token-cost
/plugin install token-cost@token-cost
```

</details>

#### Standalone TUI `$ token-cost`

<img width="1164" height="819" alt="Claude Code Token Cost Plugin Overview Screenshot" src="https://github.com/user-attachments/assets/f1a895eb-49df-4b25-8085-c933e045cec1" />

#### Browser html UI `/token-cost html`

<img width="1460" height="974" alt="Screenshot 2026-08-27 at 10 39 11 PM" src="https://github.com/user-attachments/assets/89d3badd-b107-45a6-903c-1ed27bc9da16" />

#### Inside Claude Code `/token-cost:token-cost`

<img width="1297" height="953" alt="Screenshot 2026-08-27 at 10 41 03 PM" src="https://github.com/user-attachments/assets/44708826-b9d5-44d4-af77-5f904c977a3a" />

#### Inside Claude Code Desktop `/token-cost:token-cost`

<img width="2124" height="1296" alt="Screenshot 2026-08-27 at 10 44 59 PM" src="https://github.com/user-attachments/assets/60f7baaa-1ad9-4846-9afc-18bf34cc5563" />

### Updating

```
$ claude plugin update token-cost@token-cost
```

The update downloads immediately and applies when you restart Claude Code. Check
what you're running with `claude plugin list` and compare against the `version`
in [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json) on `main`.

### Uninstalling

```
$ claude plugin uninstall token-cost@token-cost
```

Your ledger is not removed. Delete the `token-cost/` directory inside your
Claude Code data directory to remove the recorded history, and
`~/.local/bin/token-cost` to remove the terminal command.

## Available commands

Every table command opens with a summary panel and then the table itself.

| Command                | Shows                                                                |
| ---------------------- | -------------------------------------------------------------------- |
| `/token-cost`          | the visual overview: project, models, days, and costly tasks         |
| `/token-cost days`     | the model split, then one row per day                                |
| `/token-cost tasks`    | the model split, then one row per task — prompt, model, what it cost |
| `/token-cost sessions` | a sessions summary, then one row per session, newest first           |
| `/token-cost today`    | today only: the model split, then its tasks                          |
| `/token-cost week`     | the last 7 days: the model split, then a row per day                 |
| `/token-cost month`    | the last 30 days: the model split, then a row per day                |
| `/token-cost ui`       | opens the full-screen TUI in a new window                            |
| `/token-cost html`     | opens the whole report as a page in your browser                     |

### Narrowing the results

```
/token-cost tasks week        every task in the last 7 days
/token-cost sessions month    every session in the last 30 days
/token-cost days month        a row per day for the last 30 days
```

### From a terminal

The plugin installs a `token-cost` command into `~/.local/bin` (or
`$XDG_BIN_HOME`) at session start, so the UI and the browser report are
reachable without opening Claude Code.

```
$ token-cost           the full-screen UI for the current directory
$ token-cost html      the report as a page in your browser
$ token-cost --cwd /path/to/project
```

The installed file resolves the newest installed copy of the plugin at run time,
so it keeps working across updates. Set `TOKEN_COST_NO_SHIM=1` to skip
installing it.

## How this works

### The hooks it registers

| Hook               | What it runs                                                                       |
| ------------------ | ---------------------------------------------------------------------------------- |
| `Stop`             | records the finished task, and refreshes the browser report if the project has one |
| `SessionStart`     | imports any sessions not yet in the ledger, and installs the `token-cost` command  |
| `UserPromptSubmit` | answers `/token-cost` directly, so the report costs no tokens                      |

The `UserPromptSubmit` hook runs on every prompt in every project. It compares
the prompt against the command name and exits; anything else is one interpreter
start and a string compare. Set `TOKEN_COST_NO_INTERCEPT=1` to turn it off and
let the slash command answer through the model instead.

### Where the data lives

```
<claude-data-dir>/token-cost/<project-key>.jsonl       the ledger
<claude-data-dir>/token-cost/<project-key>.format      the ledger format stamp
<claude-data-dir>/token-cost/<project-key>.jsonl.bak   the previous ledger, kept after a rebuild
<claude-data-dir>/token-cost/.state/<session>.json     per-session scan cursors
<claude-data-dir>/token-cost/.reports/<key>.html       the browser report
~/.local/bin/token-cost                                the terminal command
```

`<claude-data-dir>` is wherever Claude Code already keeps its data, usually
`~/.claude`, or `$CLAUDE_CONFIG_DIR`.

Nothing is written inside your project, so it cannot be committed by accident,
and it survives re-cloning the repo. Nothing leaves your machine.

The browser report is one file per project. `/token-cost html` builds it and
opens it; from then on the `Stop` hook rewrites it as each task finishes, so
refreshing the browser tab shows the latest data.

### JSON ledger format

```json
{
  "cache_read": 3033033,
  "cache_write_1h": 46790,
  "cache_write_5m": 0,
  "cost_usd": 2.1914415,
  "ctx": 68094,
  "input": 40,
  "kind": "main",
  "model": "claude-opus-5",
  "output": 8273,
  "prompt": "Save the prompt that started each task",
  "reqs": 30,
  "session": "b7f51c83-…",
  "ts": "2026-08-25T09:23:00Z",
  "turn": 1
}
```

### How the numbers are produced

Claude Code writes each content block of a reply as its own transcript line,
all sharing one `requestId` and one _identical_ usage block. Counting them
line-by-line over-counts tokens by roughly 2.5×, so every request is deduped
by `requestId` before anything is summed.

Subagents are billed separately, write to their own transcript files, and
often run a different model than the main loop — those are read too, and show
up with `"kind":"subagent"`.

Cache writes are tracked separately by TTL, because a 1-hour write costs 2×
base input while a 5-minute write costs 1.25×.

Rates live in `scripts/pricing.json` and are applied at write time, so old rows
stay correct after prices change. Which rates apply is read off each request's
own usage block. A model with no entry records `cost_usd: null` and reports
as `?`.

### Limits

- **Costs are estimates** computed from [published API rates](https://platform.claude.com/docs/en/about-claude/pricing). On a subscription
  plan nothing is billed per token, so read the figure as "what this would
  have cost on the API".
- **Imported task counts are approximate.** Turn boundaries are reconstructed
  from the transcript, so a backfilled session's task count can be slightly
  off. Its token and cost totals are exact.
- **A session's own hooks load at startup**, so a plugin installed mid-session
  records nothing until you restart, and one updated mid-session keeps running
  the version the session started with. The next sync reads that session's
  transcript from where recording stopped.
