"""Background runner for the desktop-shortcut install/remove actions.

The desktop-shortcut managers shell out to ``gsettings``/``hyprctl``/D-Bus and
each subprocess waits up to 15 seconds; running them on the UI thread freezes
the whole overlay while a stalled session bus answers. This worker runs the
injected action on a daemon thread and hands the ``(ok, message)`` result back
to the UI thread through the QTimer receiver pattern (a timer parented to this
object, which lives on the UI thread, so the callback lands there too), with a
token guard so the result of a superseded request is dropped.

At most one action runs at a time. A request that arrives while an action is
in flight replaces any earlier pending request; the in-flight result is dropped
as stale and the latest pending request runs once the thread frees up. A rapid
Set up then Remove therefore serializes (never two concurrent subprocess runs)
and ends with the configuration matching the last delivered message.

No UI calls are made from the worker thread.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer

log = logging.getLogger("mewgenics_overlay.shortcutworker")

ShortcutResult = tuple[bool, str]

_Pending = tuple[Callable[[], ShortcutResult], Callable[[bool, str], None], int]


class ShortcutWorker(QObject):
    """Runs desktop-shortcut actions one at a time, newest request wins.

    ``run`` starts a daemon thread for *action* (which may block for up to
    four 15-second subprocess timeouts) and delivers its result to *on_done*
    on the UI thread. Only one action executes at a time: if one is already
    running, a new request is held as the single latest pending request, the
    running result is dropped, and the pending action starts when the running
    one finishes. Superseded pending requests are discarded unrun.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        # Every field below is read and written on the UI thread only: run(),
        # cancel(), the destroyed-signal hook and the QTimer-delivered
        # _finish() all run there, so no lock is needed.
        self._token = 0
        self._running = False
        self._pending: Optional[_Pending] = None

    def run(self, action: Callable[[], ShortcutResult],
            on_done: Callable[[bool, str], None]) -> int:
        """Queue *action*, returning its request token.

        Starts the action immediately when nothing is running; otherwise it
        becomes the latest pending request (replacing any previous one) and
        runs when the current action finishes. A later ``run`` (or ``cancel``)
        makes an earlier request's result stale, so it is dropped instead of
        delivered.
        """
        self._token += 1
        token = self._token
        if self._running:
            self._pending = (action, on_done, token)
            return token
        self._running = True
        self._start(action, on_done, token)
        return token

    def cancel(self) -> None:
        """Drop the in-flight result and any pending request (teardown)."""
        self._token += 1
        self._pending = None

    def _start(self, action: Callable[[], ShortcutResult],
               on_done: Callable[[bool, str], None], token: int) -> None:
        def work() -> None:
            try:
                ok, message = action()
            except Exception:
                # The managers should return (False, message) themselves; if
                # one raises, surface it instead of tearing down the UI.
                log.exception("desktop-shortcut action failed")
                ok, message = False, "The action failed unexpectedly; see the log."
            try:
                QTimer.singleShot(
                    0, self,
                    lambda: self._finish(token, on_done, ok, message))
            except RuntimeError:
                # The worker (or its Qt parent) was deleted while the action
                # ran, e.g. the settings tab closed mid-action; scheduling on
                # that object would raise from this daemon thread. The
                # cancel/token logic already suppresses delivery, so the
                # result is simply dropped.
                log.debug("dropping a desktop-shortcut result: the worker "
                          "was deleted before delivery")

        threading.Thread(target=work, name="desktop-shortcut",
                         daemon=True).start()

    def _finish(self, token: int, on_done: Callable[[bool, str], None],
                ok: bool, message: str) -> None:
        """Deliver a finished action on the UI thread, then run any pending."""
        self._running = False
        pending = self._pending
        self._pending = None
        if token == self._token:
            on_done(ok, message)
        else:
            log.info("dropping a superseded desktop-shortcut result")
        if pending is None:
            return
        action, next_done, next_token = pending
        # cancel() may have invalidated the pending request meanwhile.
        if next_token != self._token:
            return
        self._running = True
        self._start(action, next_done, next_token)
