---
description: Show token usage and estimated cost for this project
argument-hint: "[tasks|sessions|days|ui] [today|week|month]"
allowed-tools: Bash(python3:*)
model: haiku
effort: low
---

!`R="${CLAUDE_PLUGIN_ROOT}/scripts/report.py"; if [ -f "$R" ]; then python3 "$R" --cwd "$PWD" --budget 28000 $ARGUMENTS; else echo "token-cost: cannot locate report.py (CLAUDE_PLUGIN_ROOT=${CLAUDE_PLUGIN_ROOT:-unset}). Is the plugin installed?"; fi`

The command's output is above. It is always there — it runs when this command is invoked, before you ever see this instruction.

Print that output exactly as it appears, inside a code block so any columns stay aligned. Copy it verbatim, character for character: do not re-align columns, re-wrap long lines, reformat it as markdown, shorten a label, round a number, or leave out rows. A task list can run to hundreds of rows and every one of them belongs in the output.

Sometimes the output is a table and sometimes it is a short message — for example, when the UI has been opened in its own window. Print whichever one you got, the same way.

Add nothing of your own: no commentary, no summary, no interpretation. Never say the output is missing or that you did not receive it, never ask the user to paste it, and never offer to run the command yourself.
