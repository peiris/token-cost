#!/usr/bin/env python3
"""Put `token-cost` on the user's PATH, and keep it there.

Asking someone to run `ln -s` before they can use a feature is a setup
ritual, not an install. This runs as a SessionStart hook so the command
simply exists.

The installed file is a copy of scripts/token-cost, not a symlink into it. A
symlink would point inside a version directory that the next plugin update
replaces and eventually prunes, leaving a dangling command; a copy keeps
working no matter what happens to the cache, because it resolves the newest
installed plugin at run time rather than trusting where it lives.

Set TOKEN_COST_NO_SHIM=1 to opt out. As a hook this must be invisible: no
stdout, no stderr, always exit 0.
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path

MARKER = "# installed by the token-cost plugin — safe to delete"
STAMP = "# token-cost-shim "


def target_dir() -> Path:
    """Where a user-level command belongs on this machine."""
    xdg = os.environ.get("XDG_BIN_HOME")
    if xdg:
        return Path(xdg).expanduser()
    return Path.home() / ".local" / "bin"


def source() -> Path:
    return Path(__file__).resolve().parent / "token-cost"


def shim_text(launcher: str) -> str:
    """The launcher, stamped so a later version knows to refresh it."""
    digest = hashlib.sha256(launcher.encode("utf-8")).hexdigest()[:12]
    lines = launcher.split("\n")
    head = lines[0] if lines and lines[0].startswith("#!") else "#!/usr/bin/env python3"
    body = "\n".join(lines[1:] if lines[0].startswith("#!") else lines)
    return f"{head}\n{MARKER}\n{STAMP}{digest}\n{body}"


def install() -> str:
    """Returns a word describing what happened, for tests and --report."""
    if os.environ.get("TOKEN_COST_NO_SHIM"):
        return "opted-out"

    launcher = source()
    if not launcher.is_file():
        return "no-source"

    wanted = shim_text(launcher.read_text(encoding="utf-8"))
    directory = target_dir()
    path = directory / "token-cost"

    if path.exists() or path.is_symlink():
        try:
            current = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "unreadable"
        if MARKER not in current:
            # Someone else's token-cost. Not ours to replace.
            return "foreign"
        if current == wanted:
            return "current"

    try:
        directory.mkdir(parents=True, exist_ok=True)
        # Write beside it and rename, so a command that is being run right
        # now is never half-written underneath the shell.
        temporary = directory / f".token-cost.{os.getpid()}.tmp"
        temporary.write_text(wanted, encoding="utf-8")
        temporary.chmod(temporary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP
                        | stat.S_IXOTH)
        os.replace(temporary, path)
    except OSError:
        return "failed"
    return "installed"


def on_path(directory: Path) -> bool:
    entries = [Path(p).expanduser() for p in
               os.environ.get("PATH", "").split(os.pathsep) if p]
    return any(entry == directory for entry in entries)


def main() -> int:
    if "--report" in sys.argv[1:]:
        result = install()
        print(f"{result} {target_dir() / 'token-cost'}"
              f" (on PATH: {on_path(target_dir())})")
        return 0
    try:
        install()
    except Exception:
        pass  # a shim failure must never disturb a session
    return 0


if __name__ == "__main__":
    sys.exit(main())
