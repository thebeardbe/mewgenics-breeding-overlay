"""Release update check for the overlay.

Read-only network call on a background thread: asks GitHub for the latest
release tag and returns (version, release_url). Never downloads anything —
the UI offers a link to the release page. Failures are quiet (debug-level),
so a missing network connection never bothers the user.

Run at most once per CHECK_INTERVAL by the caller (window startup).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from typing import Optional, Tuple

log = logging.getLogger("mewgenics_overlay.update")

# github "latest" always resolves to the newest *non-prerelease* tag.
UPDATE_API_URL = ("https://api.github.com/repos/thebeardbe/"
                  "mewgenics-breeding-overlay/releases/latest")
RELEASES_URL = ("https://github.com/thebeardbe/"
                "mewgenics-breeding-overlay/releases/latest")
# check interval in seconds; env override (minutes) exists for testing/demo
CHECK_INTERVAL = float(
    os.environ.get("MEWGENICS_UPDATE_CHECK_MIN", "360")) * 60.0
REQUEST_TIMEOUT = 8.0

Version = Tuple[int, int, int]


def parse_version(v) -> Version:
    """'v0.1.47' / '0.1.47' / '0.1.47-beta' -> (0,1,47); junk -> (0,)."""
    if not v:
        return (0,)
    digits = re.findall(r"\d+", str(v))[:3]
    return tuple(int(d) for d in digits) or (0,)


def is_newer(local: str, remote: str) -> bool:
    return parse_version(remote) > parse_version(local)


def latest_release(url: str = UPDATE_API_URL,
                   timeout: float = REQUEST_TIMEOUT
                   ) -> Optional[Tuple[Version, str]]:
    """Return (version, release_url) for the newest release, or None."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "mewgenics-breeding-overlay",
                          "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        tag = str(data.get("tag_name") or "")
        html = str(data.get("html_url") or RELEASES_URL)
        if not tag:
            return None
        return parse_version(tag), html
    except Exception as exc:  # offline / rate-limited: stay quiet
        log.debug("update check failed: %s", exc)
        return None


def due(last_check: Optional[float], now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    if last_check is None:
        return True
    return now - float(last_check) >= CHECK_INTERVAL
