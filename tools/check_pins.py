"""Fail if a pinned helper (see mdi/addons.py) has gone stale.

    python tools/check_pins.py

Deno is pinned to one version and hash for safety, which means nobody
bumps it unless something reminds them. The release workflow runs this
before building: if the pinned release is more than MAX_AGE_DAYS old, the
release stops and says which newer version to pin instead.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mdi.addons import ADDONS  # noqa: E402

MAX_AGE_DAYS = 183     # about 6 months


def github(path: str) -> dict:
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "mdi-check-pins"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:              # avoids the low anonymous rate limit on CI
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"https://api.github.com/repos/{path}",
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def age_days(published_at: str, today: dt.date | None = None) -> int:
    published = dt.datetime.fromisoformat(
        published_at.replace("Z", "+00:00")).date()
    return ((today or dt.date.today()) - published).days


def stale(published_at: str, today: dt.date | None = None) -> bool:
    return age_days(published_at, today) > MAX_AGE_DAYS


def main() -> int:
    failed = False
    for addon in ADDONS.values():
        if not addon.sha256:           # only pinned helpers can go stale
            continue
        pinned = github(f"{addon.repo}/releases/{addon.which}")
        days = age_days(pinned["published_at"])
        name = addon.title.split(" (")[0]
        if days <= MAX_AGE_DAYS:
            print(f"OK    {name} {pinned['tag_name']} is {days} days old")
            continue
        latest = github(f"{addon.repo}/releases/latest")
        print(f"STALE {name} {pinned['tag_name']} is {days} days old "
              f"(limit {MAX_AGE_DAYS}). Latest is {latest['tag_name']}: "
              f"update the version and sha256 in mdi/addons.py (the "
              f"digest of {addon.asset} on that release page), test, "
              "and release again.")
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
