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
    #: Emitted (queued) for every valid save report, on the UI thread. Carries
    #: the :class:`~mewgenics_overlay.core.bridge.SaveRequest`.
    save_reported = Signal(object)
    #: Emitted (queued) when the game comes and goes: True while at least one
    #: client (the mod) is connected, False once they all left.
    game_online = Signal(bool)

    def __init__(self, port: int = bridge.DEFAULT_PORT,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._server = bridge.BridgeServer(
            on_focus=self.focus_requested.emit,
            on_save=self.save_reported.emit,
            on_clients_changed=self._on_clients_changed,
            port=port)

    def _on_clients_changed(self, count: int) -> None:
        """Re-emit the peer count as a plain online/offline flag."""
        self.game_online.emit(count > 0)

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

    def send_select(self, key: int) -> int:
        """Ask the game to show cat *key*. Returns how many clients got it."""
        return self._server.send({
            "v": bridge.PROTOCOL_VERSION,
            "type": "select",
            "key": int(key),
        })
