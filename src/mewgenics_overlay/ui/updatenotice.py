"""UpdateNotice - the tab-corner "update available" button.

Extracted from ``PaletteWindow`` (god-file split, step 4): owns the whole
update-check UI side - the hidden corner button, the interval-gated background
GitHub check and the "open the download page" click.

It is window-agnostic: the live settings dict and the "persist it" callable
arrive from the host, so the check interval / opt-out keys are read and
written here. The fetch itself lives in ``ui/update_check.py``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton, QWidget

from mewgenics_overlay import __version__
from mewgenics_overlay.ui import links as _links
from mewgenics_overlay.ui import theme as _theme
from mewgenics_overlay.ui import update_check as _updates

log = logging.getLogger("mewgenics_overlay.ui")


class UpdateNotice(QPushButton):
    """Hidden-by-default button that appears when a newer release exists.

    Hidden until the check finds a newer tag; clicking it opens the website
    download section in the system browser. Never downloads anything.
    """

    def __init__(self, settings: dict, save_settings: Callable[[], None],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__("", parent)
        self._settings = settings
        self._save_settings = save_settings
        self.setVisible(False)
        self.setToolTip("Open the download page in your browser\n"
                        "(Windows and Linux builds are listed there)")
        self.setStyleSheet(
            f"QPushButton {{ color:{_theme.C_GOOD}; font-weight:600; "
            f"border:1px solid {_theme.C_GRIP}; border-radius:10px; "
            "padding:0 8px; }}")
        self.clicked.connect(self._open_update)

    # ── background check ───────────────────────────────────────────────────
    def start_check(self) -> None:
        """Ask GitHub for the newest release, at most once per interval."""
        if not self._settings.get("check_for_updates", True):
            return
        if not _updates.due(self._settings.get("last_update_check")):
            return
        self._settings["last_update_check"] = time.time()
        self._save_settings()
        log.info("checking for a new release (local %s)", __version__)

        def work():
            result = _updates.latest_release()
            # Receiver = self (lives on the UI thread). Without a receiver,
            # the timer is created in THIS worker thread, which has no event
            # loop, so the update button would never appear.
            QTimer.singleShot(
                0, self, lambda: self._show_update_available(result))

        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _show_update_available(self, remote) -> None:
        if remote is None:
            return
        local = _updates.parse_version(__version__)
        if remote <= local:
            log.info("no newer release (local %s)", __version__)
            return
        label = f"v{'.'.join(str(x) for x in remote)}"
        log.info("update available: %s -> %s", __version__, label)
        self.setText(f"\u2b07 {label} available")
        self.setVisible(True)

    def _open_update(self) -> None:
        import webbrowser
        webbrowser.open(_links.DOWNLOAD_URL)
