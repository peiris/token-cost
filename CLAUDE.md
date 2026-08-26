# token-cost — project rules

## No legacy support, at all

This plugin is a work in progress with a single user (the author, for
testing). The current ledger format is the only format.

- Never write code that reads, repairs, migrates, or improves rows written
  by older plugin versions — no version-aware branches, no post-hoc fix-up
  passes, no "this field may be missing on old rows" fallbacks, no display
  states that mean "recorded before we tracked this".
- When the ledger format changes: bump `ledger.FORMAT` and the plugin
  version. A project whose format stamp disagrees is rebuilt from its own
  transcripts by that project's next `sync()` — SessionStart there,
  `/token-cost`, the UI, or `--backfill`. Rebuilds are staged (scratch
  file, one rename, stamp written last, `.bak` left beside) so a killed
  hook leaves either the old world or a finished rebuild. Lossless only
  while Claude Code still keeps the transcripts (~30 days), so a format
  bump wants releasing promptly.
- **The plugin acts on the current project only.** Nothing — code or
  agent — may enumerate, rebuild, or write other projects' ledgers.
  Healing happens where the user actually runs token-cost, never
  machine-wide.
- Version numbers are burned: never reuse or roll back one — the plugin
  cache is keyed by the version string.

The one distinction that is NOT legacy support: robustness toward Claude
Code's own transcript format (e.g. `is_human_prompt()`'s field fallbacks).
Those files are input we read but don't control, and old ones legitimately
sit on disk.

## Publishing ends at the local install, not at the push

The author's own Claude Code runs the installed plugin, not this
checkout, so a release the local install never received changed nothing.

- **Publish without being asked.** Finishing a change means releasing
  it: once the work is done and verified, run the full publish sequence
  below as part of the same task — don't stop at the working tree and
  offer to release.
- Publish = bump the plugin version, commit, push, then **both**
  installs: `claude plugin update token-cost@token-cost` for the CLI —
  the bare name doesn't resolve; the `@token-cost` marketplace suffix is
  required — and `python3 dev/resync_claudeai.py` for Claude Desktop.
  Each needs its own restart to apply.
- Push before updating: the marketplace pulls from GitHub, not from this
  directory. And without a version bump there is nothing for it to pull
  — the cache is keyed by the version string.
- **Claude Desktop is a second install, and it wins.** Where a plugin
  exists both locally and on claude.ai, the desktop runs the claude.ai
  copy and ignores the one the CLI installed. claude.ai only re-clones
  the repo when asked — the marketplace says `auto_sync_on_push` but has
  no webhook behind it — so `claude plugin update` alone leaves the
  desktop on whatever commit claude.ai last saw, with its Update button
  reading "On latest version". `dev/resync_claudeai.py` is the ask; it
  exits non-zero unless claude.ai lands on HEAD.
- A re-sync that changes hooks or executables comes back flagged
  `exec_surface_changed`. That is claude.ai noting the plugin's exec
  surface moved, not a failed sync — the new version is stored.

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
