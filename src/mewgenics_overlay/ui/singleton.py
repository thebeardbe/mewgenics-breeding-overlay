"""Single-instance channel for the overlay, plus the Linux toggle hand-off.

On Linux the app cannot grab a global key (Wayland forbids it and Qt has no
portable API), so the desktop environment owns the shortcut and runs
``<app> --toggle``. That second process must not open a second window: it
forwards a toggle to the overlay that is already running and exits.

The channel is a per-user ``QLocalServer``/``QLocalSocket`` pair (a Unix
domain socket on Linux, a named pipe on Windows). No D-Bus dependency, no
extra runtime requirement, and the same code path works if the user runs the
app twice by accident.

Usage:

    instance = SingleInstance()
    if not instance.try_acquire():       # a live peer owns it, or listen failed
        if instance.send_toggle():       # ask the peer to show/hide
            return 0                     # hand-off landed; exit
        ...                              # no peer answered: start normally
    instance.listen(palette.toggle_activate)
    ...
    instance.close()
"""

from __future__ import annotations

import getpass
import hashlib
import logging
import os
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger("mewgenics_overlay.singleton")

_TOGGLE_COMMAND = b"toggle"
_TOGGLE_MESSAGE = _TOGGLE_COMMAND + b"\n"
# Local connects either answer at once or not at all; a short wait keeps a
# stale socket or a half-started peer from hanging startup.
_PING_TIMEOUT_MS = 300
_SEND_TIMEOUT_MS = 1000
# A toggle is 7 bytes. Cap the bytes buffered for one line so a same-user
# process that streams without ever sending a newline cannot grow this
# process's memory without bound.
_MAX_MESSAGE_BYTES = 4096


def channel_name() -> str:
    """A stable, filesystem-safe channel name, scoped to the current user.

    The username is hashed rather than embedded so names with spaces or
    punctuation cannot produce an invalid socket path, and two users on one
    machine never share a channel.
    """
    try:
        identity = getpass.getuser()
    except (OSError, KeyError):
        identity = str(os.getuid()) if hasattr(os, "getuid") else str(os.getpid())
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return f"mewgenics-overlay-{digest}"


class _ClientChannel(QObject):
    """One accepted client connection; buffers until a newline arrives."""

    closed = Signal()

    def __init__(self, socket: QLocalSocket, on_message: Callable[[bytes], None],
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._socket = socket
        socket.setParent(self)
        self._on_message = on_message
        self._buffer = bytearray()
        socket.readyRead.connect(self._read)
        socket.disconnected.connect(self._on_disconnected)
        self._read()

    def _read(self) -> None:
        self._buffer.extend(bytes(self._socket.readAll()))
        while b"\n" in self._buffer:
            line, _, rest = self._buffer.partition(b"\n")
            if len(line) > _MAX_MESSAGE_BYTES:
                self._drop_oversized(len(line))
                return
            self._buffer[:] = rest
            self._on_message(bytes(line).strip())
        if len(self._buffer) > _MAX_MESSAGE_BYTES:
            self._drop_oversized(len(self._buffer))

    def _drop_oversized(self, size: int) -> None:
        """Log and close a connection whose message exceeds the cap."""
        log.warning("dropping a single-instance connection with an "
                    "oversized message (%d bytes)", size)
        self._socket.abort()

    def _on_disconnected(self) -> None:
        self._socket.deleteLater()
        self.closed.emit()
        self.deleteLater()

    def close(self) -> None:
        self._socket.abort()


class SingleInstance(QObject):
    """Owns the single-instance channel and the second-launch hand-off.

    ``try_acquire`` decides whether this process is the primary instance.
    When it is not, ``send_toggle`` tells the running overlay to toggle.
    """

    def __init__(self, name: Optional[str] = None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._name = name or channel_name()
        self._server: Optional[QLocalServer] = None
        self._on_toggle: Optional[Callable[[], None]] = None
        self._pending_toggle = False
        self._connections: list[_ClientChannel] = []

    @property
    def channel(self) -> str:
        return self._name

    def try_acquire(self) -> bool:
        """Take ownership of the channel, or report an existing instance.

        Returns ``True`` only when this process owns the channel. ``False``
        means either that a live peer already owns it (we are the second
        instance) or that the channel could not be taken; both are logged,
        and the caller must not assume it holds the channel.

        The claim is ``listen()``-first: binding the socket is the atomic
        step, so two simultaneous launches cannot both believe they won (the
        loser sees a listen failure). A leftover socket is removed only after
        a connect probe has ruled out a live peer, so a running instance's
        socket is never unlinked. A very narrow race remains between that
        probe and the unlink, but it is not worth re-engineering.
        """
        server = QLocalServer(self)
        if self._listen(server):
            return self._adopt(server)
        # Listen failed: either a live peer owns the channel or a crash left
        # the socket file behind. Probe before removing so a live peer's
        # socket is never unlinked out from under it.
        if self._peered():
            return False
        QLocalServer.removeServer(self._name)
        server = QLocalServer(self)
        if self._listen(server):
            return self._adopt(server)
        # The retry failed too: a peer may have appeared in the gap. Re-probe
        # so we report a second instance as such; otherwise this is an honest
        # acquisition failure and the caller must not pretend it succeeded.
        if self._peered():
            return False
        log.warning("single-instance channel unavailable (%s); starting "
                    "without it", server.errorString())
        return False

    def listen(self, on_toggle: Callable[[], None]) -> None:
        """Register *on_toggle*; it runs on the UI thread per request."""
        self._on_toggle = on_toggle
        if self._pending_toggle:
            # A second launch toggled during the narrow startup window before
            # this callback existed; deliver it now instead of dropping it.
            self._pending_toggle = False
            self._deliver_toggle()

    def send_toggle(self) -> bool:
        """Ask the running instance to toggle; ``False`` when it is gone.

        Delivery is judged by the connection state, the number of bytes the
        socket accepted, and any real socket error. A flush that reports
        ``False`` while the socket is connected and every byte is queued is
        treated as delivered: in an in-process or busy event loop the flush
        can be reported before the kernel has drained the buffer, and the
        peer has still received the message.
        """
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(_SEND_TIMEOUT_MS):
            log.warning("could not reach the running overlay at %s: %s",
                        self._name, socket.errorString())
            return False
        written = socket.write(_TOGGLE_MESSAGE)
        socket.flush()
        flushed = socket.waitForBytesWritten(_SEND_TIMEOUT_MS)
        connected = (socket.state()
                     == QLocalSocket.LocalSocketState.ConnectedState)
        error = socket.error()
        if (written < len(_TOGGLE_MESSAGE)
                or not connected
                or error != QLocalSocket.LocalSocketError.UnknownSocketError):
            log.warning("could not send the toggle to %s (wrote %d of %d "
                        "bytes, connected=%s, error=%s: %s)",
                        self._name, written, len(_TOGGLE_MESSAGE), connected,
                        error, socket.errorString())
            socket.disconnectFromServer()
            return False
        if not flushed:
            log.debug("toggle to %s queued but not flushed within %d ms; "
                      "treating it as delivered", self._name,
                      _SEND_TIMEOUT_MS)
        socket.disconnectFromServer()
        return True

    def close(self) -> None:
        """Release the channel (on quit); safe to call more than once."""
        for connection in list(self._connections):
            connection.close()
        self._connections.clear()
        if self._server is not None:
            self._server.close()
            QLocalServer.removeServer(self._name)
            self._server = None
            log.info("single-instance channel released: %s", self._name)

    # ── internals ─────────────────────────────────────────────────────────
    def _listen(self, server: QLocalServer) -> bool:
        if server.listen(self._name):
            return True
        log.debug("listen on %s failed: %s", self._name,
                  server.errorString())
        return False

    def _adopt(self, server: QLocalServer) -> bool:
        server.newConnection.connect(self._accept)
        self._server = server
        log.info("single-instance channel acquired: %s", self._name)
        return True

    def _peered(self) -> bool:
        """True when a live peer answers a connect on the channel."""
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        connected = socket.waitForConnected(_PING_TIMEOUT_MS)
        socket.abort()
        if connected:
            log.info("another overlay already owns %s", self._name)
        return connected

    def _accept(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                break
            connection = _ClientChannel(socket, self._handle_message,
                                        parent=self)
            connection.closed.connect(
                lambda c=connection: self._forget(c))
            self._connections.append(connection)

    def _forget(self, connection: _ClientChannel) -> None:
        try:
            self._connections.remove(connection)
        except ValueError:
            pass

    def _handle_message(self, message: bytes) -> None:
        if message != _TOGGLE_COMMAND:
            log.warning("ignoring unknown single-instance message %r", message)
            return
        log.info("toggle requested by a second launch")
        if self._on_toggle is None:
            # The channel accepts connections before listen() registers the
            # callback; hold the toggle so a second launch in that window is
            # delivered once the overlay is ready (at most one is kept).
            self._pending_toggle = True
            log.info("buffering a toggle that arrived before the overlay "
                     "was ready")
            return
        self._deliver_toggle()

    def _deliver_toggle(self) -> None:
        handler = self._on_toggle
        if handler is None:
            return
        try:
            handler()
        except Exception:
            log.exception("single-instance toggle handler failed")
