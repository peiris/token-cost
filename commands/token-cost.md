---
description: Show token usage and estimated cost for this project
argument-hint: "[tasks|sessions|days|ui] [today|week|month]"
allowed-tools: Bash(python3:*)
model: haiku
---

!`R="${CLAUDE_PLUGIN_ROOT}/scripts/report.py"; if [ -f "$R" ]; then python3 "$R" --cwd "$PWD" --budget 28000 $ARGUMENTS; else echo "token-cost: cannot locate report.py (CLAUDE_PLUGIN_ROOT=${CLAUDE_PLUGIN_ROOT:-unset}). Is the plugin installed?"; fi`

Print the output above exactly as it appears, inside a code block so the columns stay aligned.

Copy it verbatim, character for character. Do not re-align columns, re-wrap long lines, reformat it as markdown, shorten a label, round a number, or leave out rows — a task list can run to hundreds of rows and every one of them belongs in the output. Add no commentary, no summary, and no interpretation.
