# token-cost — project rules

## No legacy support, at all

This plugin is a work in progress with a single user (the author, for
testing). The current ledger format is the only format.

- Never write code that reads, repairs, migrates, or improves rows written
  by older plugin versions — no version-aware branches, no post-hoc fix-up
  passes, no "this field may be missing on old rows" fallbacks, no display
  states that mean "recorded before we tracked this".
- When the ledger format changes: bump the version and rebuild the data
  with `python3 scripts/record.py --backfill --force --cwd <project>`.
  Rebuild promptly — it is lossless only while Claude Code still keeps the
  transcripts (~30 days).
- The rebuild stays manual-only. It must never be reachable from `sync()`,
  hooks, or anything that runs automatically.
- Version numbers are burned: never reuse or roll back one — the plugin
  cache is keyed by the version string.

The one distinction that is NOT legacy support: robustness toward Claude
Code's own transcript format (e.g. `is_human_prompt()`'s field fallbacks).
Those files are input we read but don't control, and old ones legitimately
sit on disk.
