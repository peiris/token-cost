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

## Never discard another agent's work in the working tree

More than one Claude session often works in this checkout at once. Treat
every uncommitted change you didn't make as a colleague's work in
progress, not noise.

- Before editing, check `git status` / `git diff` and re-read any file
  that changed on disk since you last saw it. Edit around other agents'
  hunks; never revert, overwrite, or "clean up" their uncommitted changes.
- Write full files only when you authored the whole current content;
  otherwise make targeted edits so concurrent hunks survive.
- Commit only the files that carry your own change (carrying a
  collaborator's finished edit in the same file is fine — say so in the
  commit message). Leave their in-progress files dirty for their own
  commit, and never `git checkout --`, `stash`, or `reset` anything you
  didn't write.
- Coordinate through commits and cross-session messages when the same
  file is contested.
