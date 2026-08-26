#!/usr/bin/env python3
"""Push the claude.ai copy of this plugin up to the repo's HEAD.

Claude Desktop does not run the plugin the CLI installed. When a plugin
exists both locally and on claude.ai it takes the claude.ai one -- the log
line is `Plugin "token-cost@token-cost" exists in both remote and local.
Using remote.` So `claude plugin update` leaves the desktop untouched, and
the desktop's Update button correctly reads "On latest version": latest as
claude.ai knows it, which is whatever commit it last cloned.

claude.ai clones the repo when the marketplace is added and then again only
when something asks it to. The marketplace record says
`auto_sync_on_push: true`, but with `has_webhook_secret: false` there is no
webhook delivering pushes, so nothing ever asks. This is that ask -- the
same POST the desktop's own refresh sends:

    POST /api/organizations/{org}/marketplaces/{id}/account-sync

Auth is the desktop's claude.ai session cookie, read out of its Chromium
cookie jar with the AES key macOS Keychain holds under "Claude Safe
Storage". The cookie is used for these requests and never written down.

A sync that lands can still report `exec_surface_changed` -- claude.ai
noticed the plugin's hooks or executables differ from the version already
installed and wants a look before anyone runs them. The new version is
stored either way; the note is printed because it is worth seeing.

Prints the SHA claude.ai now holds. Exit code 0 only if that is HEAD.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn

COOKIES = Path.home() / "Library/Application Support/Claude/Cookies"
KEYCHAIN_SERVICE = "Claude Safe Storage"
KEYCHAIN_ACCOUNT = "Claude Key"
BASE = "https://claude.ai/api/organizations"

# Chromium on macOS: AES-128-CBC, key stretched from the Keychain password,
# IV of sixteen spaces, "v10"/"v11" tag on the front of every value.
CHROMIUM_SALT = b"saltysalt"
CHROMIUM_ROUNDS = 1003
CHROMIUM_IV = b" " * 16

# The desktop identifies itself as Electron; Cloudflare wants the cookies
# and the user agent to tell the same story.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Claude/1.0.0 Chrome/132.0.0.0 "
    "Electron/34.0.0 Safari/537.36"
)

PENDING = {"unspecified", "in_progress"}
POLL_EVERY = 2.0
POLL_FOR = 60.0


def die(msg: str) -> NoReturn:
    print(f"resync: {msg}", file=sys.stderr)
    raise SystemExit(1)


def safe_storage_key() -> bytes:
    """The AES key behind every cookie the desktop stores."""
    try:
        password = subprocess.check_output(
            ["security", "find-generic-password", "-w",
             "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        die(f"Keychain has no {KEYCHAIN_SERVICE!r} entry -- "
            "is Claude Desktop installed and signed in?")
    return hashlib.pbkdf2_hmac(
        "sha1", password, CHROMIUM_SALT, CHROMIUM_ROUNDS, 16)


def decrypt(value: bytes, key: bytes) -> str:
    if value[:3] not in (b"v10", b"v11"):
        return value.decode("utf8", "replace")
    plain = subprocess.run(
        ["openssl", "enc", "-d", "-aes-128-cbc",
         "-K", key.hex(), "-iv", CHROMIUM_IV.hex(), "-nopad"],
        input=value[3:], capture_output=True, check=True,
    ).stdout
    pad = plain[-1] if plain else 0
    if 0 < pad <= 16:
        plain = plain[:-pad]
    try:
        return plain.decode("utf8")
    except UnicodeDecodeError:
        # Newer Chromium prefixes the plaintext with a domain hash.
        return plain[32:].decode("utf8", "replace")


def session() -> tuple[str, str]:
    """The desktop's claude.ai cookie header, and the org it last used."""
    if not COOKIES.exists():
        die(f"no cookie jar at {COOKIES}")
    key = safe_storage_key()
    with tempfile.TemporaryDirectory() as tmp:
        # The desktop holds the live file open; read a copy.
        copy = Path(tmp) / "Cookies"
        shutil.copy(COOKIES, copy)
        rows = sqlite3.connect(copy).execute(
            "select name, encrypted_value from cookies "
            "where host_key like '%claude.ai'"
        ).fetchall()
    jar = {name: decrypt(value, key) for name, value in rows}
    if "sessionKey" not in jar:
        die("no claude.ai session cookie -- sign in to Claude Desktop first")
    org = jar.get("lastActiveOrg")
    if not org:
        die("no active organization cookie")
    return "; ".join(f"{k}={v}" for k, v in jar.items()), org


def call(cookies: str, url: str, method: str = "GET") -> dict:
    request = urllib.request.Request(url, method=method)
    request.add_header("Cookie", cookies)
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept", "application/json")
    request.add_header("anthropic-client-platform", "web_claude_ai")
    request.add_header("Referer", "https://claude.ai/")
    if method == "POST":
        request.add_header("Content-Length", "0")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read()[:300].decode("utf8", "replace")
        die(f"{method} {url.rsplit('/', 1)[-1]} -> HTTP {error.code}: {detail}")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        die("claude.ai answered with something other than JSON "
            "(a Cloudflare challenge, most likely) -- open the desktop app "
            "once to refresh its cookies, then try again")


def repo_slug(root: Path) -> str:
    """owner/name for this checkout's origin, lowercased."""
    url = subprocess.check_output(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        text=True,
    ).strip()
    slug = url.removesuffix(".git").rstrip("/")
    return "/".join(slug.replace(":", "/").split("/")[-2:]).lower()


def find_marketplace(cookies: str, org: str, slug: str) -> dict:
    listed = call(cookies, f"{BASE}/{org}/marketplaces/list-account-marketplaces")
    for market in listed.get("marketplaces", []):
        source = (market.get("source_url") or "").removesuffix(".git")
        if source.rstrip("/").lower().endswith(slug):
            return market
    names = ", ".join(m.get("name", "?") for m in listed.get("marketplaces", []))
    die(f"no claude.ai marketplace points at {slug} (found: {names or 'none'})")


def sync(cookies: str, org: str, market_id: str) -> dict:
    root = f"{BASE}/{org}/marketplaces/{market_id}"
    state = call(cookies, f"{root}/account-sync", method="POST")
    deadline = time.monotonic() + POLL_FOR
    while state.get("sync_status") in PENDING and time.monotonic() < deadline:
        time.sleep(POLL_EVERY)
        state = call(cookies, f"{root}/account-get")
    return state


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()

    cookies, org = session()
    market = find_marketplace(cookies, org, repo_slug(root))
    print(f"marketplace {market['name']} ({market['id']})")
    print(f"  was at {(market.get('last_synced_sha') or '?')[:12]}")

    state = sync(cookies, org, market["id"])
    landed = state.get("last_synced_sha") or ""
    print(f"  now at {landed[:12] or '?'} ({state.get('sync_status')})")

    errors = state.get("sync_errors")
    if errors:
        note = errors if isinstance(errors, str) else json.dumps(errors)
        print(f"  note: {note}")

    if landed != head:
        print(f"claude.ai is not at HEAD ({head[:12]}) -- "
              "is the commit pushed?", file=sys.stderr)
        return 1

    print("claude.ai is at HEAD. Restart Claude Desktop to pick it up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
