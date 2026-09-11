"""Bridge controller: run the loopback bridge and hop onto the UI thread.

``core/bridge.py`` is Qt-free and delivers requests on a transport thread. This
QObject owns the server and re-emits each request as a Qt signal, so the
window's slot runs on the main thread and may safely touch widgets. It is the
only place the bridge meets Qt.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal

from mewgenics_overlay.core import bridge

log = logging.getLogger("mewgenics_overlay.bridgectl")


class BridgeController(QObject):
    """Own a :class:`~mewgenics_overlay.core.bridge.BridgeServer`."""

    #: Emitted (queued) for every valid focus request, on the UI thread.
    focus_requested = Signal(object)

    def __init__(self, port: int = bridge.DEFAULT_PORT,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._server = bridge.BridgeServer(
            on_focus=self.focus_requested.emit, port=port)

    @property
    def port(self) -> Optional[int]:
        return self._server.port

    @property
    def running(self) -> bool:
        return self._server.running

    def start(self) -> bool:
        return self._server.start()

    def stop(self) -> None:
        self._server.stop()
