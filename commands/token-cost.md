---
description: Show token usage and estimated cost for this project
argument-hint: "[sessions|today]"
allowed-tools: Bash(python3:*)
---

!`R="${CLAUDE_PLUGIN_ROOT}/scripts/report.py"; if [ -f "$R" ]; then python3 "$R" --cwd "$PWD" $ARGUMENTS; else echo "token-cost: cannot locate report.py (CLAUDE_PLUGIN_ROOT=${CLAUDE_PLUGIN_ROOT:-unset}). Is the plugin installed?"; fi`

Print the table above exactly as it appears, inside a code block so the columns stay aligned. Add no commentary, no summary, and no interpretation.
