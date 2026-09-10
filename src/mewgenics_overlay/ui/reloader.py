"""Reload/session glue: save watcher -> parse -> back onto the UI thread.

Extracted from ``PaletteWindow`` (god-file split): this coordinator owns the
save-file watcher, the debounced "the save changed, reload it" path, the
background partner-ranking requests and the poll timer that drains finished
worker results back to the UI thread, plus adoption of a parsed session.

Threading model (unchanged from the window): heavy work runs on daemon threads
inside :class:`~mewgenics_overlay.ui.savecontroller.SaveController`. Every
request bumps a generation token; the poll loop only adopts a drained result
while that token is still current, so a slow parse or ranking can never
overwrite a newer one. The watcher thread only emits :attr:`save_changed`;
the signal hops the notification onto the UI thread, where the handler runs.

Window-agnostic: the host passes ``on_session`` (a session was adopted and the
host should rebuild its views), ``on_partners`` (rows for one cat finished
computing), ``on_status`` (status-line text, including the error lines) and an
optional ``on_tick`` for work the host wants drained first on every poll (the
palette collects its gpak assets that way). Nothing here reaches into
``PaletteWindow``.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from mewgenics_overlay.core.session import Session
from mewgenics_overlay.ui.savecontroller import SaveController

log = logging.getLogger("mewgenics_overlay.ui.reloader")

POLL_MS = 120   # how often the UI thread drains finished background jobs


class ReloadCoordinator(QObject):
    """Owns the watcher, the reload/partner requests and the drain pump.

    ``save`` is the controller holding the session plus the worker queue and
    the watcher; the three required callbacks are the only view-facing
    surface (see the module docstring).
    """

    # The save watcher fires from its own thread; a signal is the Qt-safe way
    # to hand that notification back to the UI thread (connected below).
    save_changed = Signal()

    def __init__(
        self,
        save: SaveController,
        *,
        on_session: Callable[[Optional[Session]], None],
        on_partners: Callable[[int, list], None],
        on_status: Callable[[str], None],
        on_tick: Optional[Callable[[], None]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._save = save
        self._on_session = on_session
        self._on_partners = on_partners
        self._on_status = on_status
        self._on_tick = on_tick
        self._path: str = ""
        self.save_changed.connect(self.on_save_changed)
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._on_poll)
        # Started here; the host is expected to have built its views and
        # callbacks before constructing the coordinator, so the first tick
        # always finds them ready (no work happens before the event loop).
        self._poll.start()

    # ── lifecycle ─────────────────────────────────────────────────────────
    def start(self, path: str) -> None:
        """Watch *path* (replacing any previous watcher) and remember it.

        The remembered path is what :meth:`request_reload` re-parses, so the
        host does not have to hand the path in twice.
        """
        self._path = path
        self._save.start_watcher(path, on_change=self.save_changed.emit)

    def stop(self) -> None:
        """Stop the poll timer and the watcher before the app exits."""
        self._poll.stop()
        self._save.stop_watcher()

    # ── requests ──────────────────────────────────────────────────────────
    def request_reload(self) -> None:
        """Queue a background re-parse of the watched save (token-guarded)."""
        if self._path:
            self._save.schedule_reload(self._path)

    def schedule_partners(self, cat_key: int, max_rows: int,
                          show_blocked: Optional[int], include_adventure: bool,
                          order: str, stimulation: float) -> None:
        """Queue partner ranking for *cat_key* off the UI thread.

        The arguments are the host's current view settings; the controller
        ignores the request when no session is loaded.
        """
        self._save.schedule_partners(
            cat_key, max_rows, show_blocked, include_adventure, order,
            stimulation)

    def adopt_session(self, sess: Optional[Session]) -> None:
        """Adopt *sess* (``None`` clears it), then let the host rebuild.

        The status line is written first, exactly as before the extraction,
        so hosts may rely on the session being visible in the status text by
        the time ``on_session`` runs.
        """
        self._save.session = sess
        count = len(sess.alive) if sess else 0
        self._on_status(
            f"{count} cats in house/on adventures" if sess else "no save loaded")
        self._on_session(sess)

    # ── watcher / poll (UI thread unless stated otherwise) ────────────────
    def on_save_changed(self) -> None:
        """The watched save was rewritten: reload it.

        Runs on the UI thread (the signal queues it there); host code may
        also call it directly, so a broken callback is caught and reported
        instead of killing the caller.
        """
        try:
            self._on_status("save changed - reloading…")
            self.request_reload()
        except Exception:
            log.exception("save-change handler failed")
            self._on_status("⚠ save changed but reload failed - see the log")

    def _on_poll(self) -> None:
        """Drain completed background jobs on the UI thread (token-guarded)."""
        if self._on_tick is not None:
            self._on_tick()
        items = self._save.drain()
        token_now = self._save.token
        for token, kind, result in items:
            if kind == "session":
                if token >= token_now:
                    self.adopt_session(result)
            elif kind == "session_error":
                if token >= token_now:
                    # Keep the previous roster; never leave the UI hanging on
                    # a corrupt/hostile save.
                    self._on_status("⚠ could not read save - keeping the "
                                    "previous cats")
                    log.warning("save reload failed: %s", result)
            elif kind == "partners":
                cat_key, rows = result
                if token >= token_now:
                    # Whether these rows still belong to the focused cat is
                    # the host's call (it owns the selection).
                    self._on_partners(cat_key, rows)
            elif kind == "partners_error":
                # raw exception already logged in the worker; show a
                # friendly line, never a Python traceback in the UI.
                self._on_status("⚠ partner scoring failed - see the log; "
                                "try selecting another cat")
