"""Suppress the echo of an outbound "show in game" select.

Picking "Show in game" sends a select to the game; the game changes its
selection, the mod's selection hook fires and reports that change straight
back as a focus message. Without this filter the overlay re-selects that cat,
which is harmless when the command carried the focused cat but silently
re-roots the whole analysis when it carried a partner row's cat instead (the
partner, not the focused cat, is what gets sent).

Pure logic: no Qt, no locks, no signals. Every caller runs on the UI thread
(the send from the card button / Ctrl+G shortcut, and the bridge controller's
queued focus signal), so a plain timestamp map is enough.
"""

from __future__ import annotations

import time
from typing import Dict

#: How long an outbound select stays suppressed, in seconds.
#: The observed round trip from sending a select to the mod reporting that
#: selection back is under 500 ms, so 750 ms covers it with margin while
#: staying short enough that a real in-game click right after a command is
#: still honoured.
ECHO_WINDOW_SECONDS = 0.75


class SelectEchoFilter:
    """Remember what the overlay asked the game for, to spot the report back.

    The map holds every key requested inside the window, not just the latest,
    so two commands in quick succession cannot let the first one's echo
    through. Entries are pruned by age on every call, which also bounds the
    map to the handful of sends that can fit in one window.
    """

    def __init__(self, window: float = ECHO_WINDOW_SECONDS) -> None:
        self._window = window
        self._requested: Dict[int, float] = {}

    def note_request(self, key: int) -> None:
        """Remember an outbound select for *key*."""
        now = time.monotonic()
        self._prune(now)
        self._requested[int(key)] = now

    def is_echo(self, key: int) -> bool:
        """True when *key* was requested inside the window."""
        now = time.monotonic()
        self._prune(now)
        return int(key) in self._requested

    def _prune(self, now: float) -> None:
        """Drop entries older than the window."""
        cutoff = now - self._window
        stale = [key for key, seen in self._requested.items() if seen < cutoff]
        for key in stale:
            del self._requested[key]
