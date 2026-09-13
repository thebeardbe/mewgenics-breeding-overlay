"""In-game bridge: receive focus requests from the Mewgenics mod.

The companion mod (``mewgenics-breeding-mod``) runs inside Mewgenics and, when
the player asks for a cat, sends a small JSON line to this overlay over a
loopback TCP socket. This module owns:

  * the wire protocol (:func:`parse_message`),
  * turning a request into a cat key the overlay can focus
    (:func:`resolve_focus_key`),
  * the loopback server that delivers requests to a callback
    (:class:`BridgeServer`).

It is deliberately Qt-free and holds no session state: the callback runs on a
transport thread, and the UI layer is responsible for hopping to the main
thread (see ``ui/bridgectl.py``).

Protocol (one JSON object per line, UTF-8)::

    {"v": 1, "type": "focus", "key": 341, "name": "L'Via"}
    {"v": 1, "type": "save", "file": "steamcampaign02.sav"}

``key`` is the game's cat key, which is also the overlay's ``db_key`` (proven
against the live game; see the mod repo's ``RESEARCH.md``). ``uid`` and ``name``
are optional fallbacks for mods that cannot read the key. A ``save`` message
names the save file the game is currently playing; the file name is validated
with :func:`mewgenics_overlay.core.livesave.is_save_file_name` and the UI side
resolves it to a path (see ``core/livesave.py``).

Security: the server binds loopback only. Any local process can focus a cat,
which is harmless, but it cannot read anything or change game state.
"""

from __future__ import annotations

import json
import logging
import select
import socket
import threading
from dataclasses import dataclass
from typing import Callable, Optional, Union

from mewgenics_overlay.core import livesave

log = logging.getLogger("mewgenics_overlay.bridge")

DEFAULT_PORT = 45780
PROTOCOL_VERSION = 1
#: Inbound message types the mod may send.
FOCUS_TYPE = "focus"
SAVE_TYPE = "save"
#: Reject anything longer: a real message is well under 200 bytes, so this only
#: guards against a hostile or confused local process.
MAX_LINE_BYTES = 4096
LISTEN_BACKLOG = 8
ACCEPT_TIMEOUT = 0.5
#: Most peers registered at once. The mod keeps one connection open, so a
#: handful of slots covers reconnects and a second mod; more is a bug or a
#: hostile local process.
MAX_CLIENTS = 8


class ProtocolError(ValueError):
    """A line that is not a valid bridge message."""


@dataclass(frozen=True, slots=True)
class FocusRequest:
    """A request to focus a cat, as sent by the mod.

    All fields are optional: the mod should send ``key``, but a future or
    third-party mod may only know a uid or a name.
    """

    key: Optional[int] = None
    uid: Optional[str] = None
    name: Optional[str] = None


@dataclass(frozen=True, slots=True)
class SaveRequest:
    """The save file the game is currently playing, as sent by the mod.

    ``file`` is always a bare ``.sav`` file name, never a path: the overlay
    decides which copy on disk that refers to (see
    :func:`mewgenics_overlay.core.livesave.resolve_save_path`).
    """

    file: str


#: Anything the mod may send on one line.
InboundMessage = Union[FocusRequest, SaveRequest]


def _as_int(value: object) -> Optional[int]:
    """Best-effort int for JSON numbers and numeric strings, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                return int(text, 0)
            except ValueError:
                return None
    return None


def parse_message(raw: str) -> InboundMessage:
    """Parse one protocol line into a :class:`FocusRequest` or :class:`SaveRequest`.

    Raises :class:`ProtocolError` for malformed JSON, an unsupported version, an
    unknown type, a focus message with no usable identifier, or a save message
    whose file name is not a plain ``.sav`` name.
    """
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ProtocolError(f"not JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ProtocolError("message is not a JSON object")

    version = _as_int(payload.get("v"))
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version {payload.get('v')!r}")

    message_type = payload.get("type")
    if message_type == FOCUS_TYPE:
        return _parse_focus(payload)
    if message_type == SAVE_TYPE:
        return _parse_save(payload)
    raise ProtocolError(f"unsupported message type {message_type!r}")


def _parse_focus(payload: dict) -> FocusRequest:
    """Build a :class:`FocusRequest`, refusing one with no usable identifier."""
    request = FocusRequest(
        key=_as_int(payload.get("key")),
        uid=None if payload.get("uid") is None else str(payload["uid"]),
        name=None if payload.get("name") is None else str(payload["name"]),
    )
    if request.key is None and request.uid is None and request.name is None:
        raise ProtocolError("focus message carries no key, uid or name")
    return request


def _parse_save(payload: dict) -> SaveRequest:
    """Build a :class:`SaveRequest`, refusing anything that is not a bare name."""
    raw = payload.get("file")
    text = raw.strip() if isinstance(raw, str) else ""
    if not livesave.is_save_file_name(text):
        raise ProtocolError(f"save message carries no plain .sav name: {raw!r}")
    return SaveRequest(file=text)


def resolve_focus_key(session, request: FocusRequest) -> Optional[int]:
    """Resolve *request* to a cat key in *session*, or None.

    Order: exact key, then uid, then an exact (case-insensitive) name match.
    An ambiguous name is refused rather than guessed, so the overlay never
    focuses the wrong cat. *session* may be any object exposing ``by_key`` and
    ``cats``; a missing or unloaded session resolves to None.
    """
    if session is None:
        return None

    cats = getattr(session, "cats", None) or []

    if request.key is not None:
        by_key = getattr(session, "by_key", {}) or {}
        cat = by_key.get(request.key)
        if cat is not None:
            return cat.db_key

    if request.uid:
        wanted = request.uid.strip().lower()
        for cat in cats:
            if str(getattr(cat, "unique_id", "")).lower() == wanted:
                return cat.db_key

    if request.name:
        wanted = request.name.strip().casefold()
        hits = [c for c in cats if str(getattr(c, "name", "")).casefold() == wanted]
        if len(hits) == 1:
            return hits[0].db_key
        if len(hits) > 1:
            log.warning("bridge: name %r is ambiguous (%d cats); ignoring",
                        request.name, len(hits))
    return None


class BridgeServer:
    """Loopback TCP server for bridge messages.

    Callbacks (all optional except ``on_focus``) run on whichever thread
    observed the event - a connection thread for messages, the caller's thread
    when ``stop()`` clears the peers - so the UI layer must hop to the main
    thread (see ``ui/bridgectl.py``). Exceptions a callback raises are logged,
    never allowed to kill the server.

    ``on_focus``            every valid focus request.
    ``on_save``             every valid save request, i.e. which save the game plays.
    ``on_clients_changed``  the peer count after a client connected or went away,
                            so the UI can show the game as connected.
    """

    def __init__(
        self,
        on_focus: Callable[[FocusRequest], None],
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        parent_log: Optional[logging.Logger] = None,
        on_save: Optional[Callable[[SaveRequest], None]] = None,
        on_clients_changed: Optional[Callable[[int], None]] = None,
    ) -> None:
        self._on_focus = on_focus
        self._on_save = on_save
        self._on_clients_changed = on_clients_changed
        self._host = host
        self._requested_port = port
        self._log = parent_log or log
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._port: Optional[int] = None
        # Connected peers. The mod keeps one connection open so we can send
        # commands back (select a cat in game). Guarded by a lock because the
        # UI thread sends while connection threads register/unregister.
        self._clients: set[socket.socket] = set()
        self._clients_lock = threading.Lock()

    # ── state ──────────────────────────────────────────────────────────────
    @property
    def port(self) -> Optional[int]:
        """The bound port, or None before start (0 in means "any")."""
        return self._port

    @property
    def address(self) -> Optional[tuple[str, int]]:
        if self._port is None or self._sock is None:
            return None
        return self._sock.getsockname()[:2]

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def client_count(self) -> int:
        with self._clients_lock:
            return len(self._clients)

    # ── outbound ───────────────────────────────────────────────────────────
    def send(self, payload: dict) -> int:
        """Send one JSON line to every connected peer; returns how many got it.

        Safe to call from the UI thread: each write only happens when the
        socket is already writable, so a stalled peer is dropped instead of
        blocking the caller. Dead peers are dropped too.
        """
        data = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        with self._clients_lock:
            clients = list(self._clients)
        delivered = 0
        for conn in clients:
            # Only write when the socket is immediately writable: a peer whose
            # send buffer is full must never stall the UI thread. Such a peer
            # is dropped rather than waited on.
            try:
                _, writable, _ = select.select([], [conn], [], 0)
            except (OSError, ValueError):
                writable = []
            if not writable:
                self._log.warning(
                    "bridge: client not writable; dropping it")
                self._unregister(conn)
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            try:
                conn.sendall(data)
                delivered += 1
            except OSError:
                self._unregister(conn)
        return delivered

    def _register(self, conn: socket.socket) -> bool:
        """Add *conn* to the peer set; False when the client cap is reached."""
        with self._clients_lock:
            if len(self._clients) >= MAX_CLIENTS:
                return False
            self._clients.add(conn)
            count = len(self._clients)
        self._notify_clients(count)
        return True

    def _unregister(self, conn: socket.socket) -> None:
        with self._clients_lock:
            if conn not in self._clients:
                return
            self._clients.discard(conn)
            count = len(self._clients)
        self._notify_clients(count)

    def _notify_clients(self, count: int) -> None:
        """Report the peer count; called with no lock held."""
        callback = self._on_clients_changed
        if callback is None:
            return
        try:
            callback(count)
        except Exception:
            self._log.exception("bridge: client-count handler failed")

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> bool:
        """Bind and serve. Returns False (and logs) when the port is busy."""
        if self.running:
            return True
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self._host, self._requested_port))
            sock.listen(LISTEN_BACKLOG)
            sock.settimeout(ACCEPT_TIMEOUT)
        except OSError as exc:
            sock.close()
            self._log.warning("bridge: cannot listen on %s:%s: %s",
                              self._host, self._requested_port, exc)
            return False

        self._sock = sock
        self._port = sock.getsockname()[1]
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._serve, name="mewgenics-bridge", daemon=True)
        self._thread.start()
        self._log.info("bridge: listening on %s:%d", self._host, self._port)
        return True

    def stop(self) -> None:
        self._stop.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
        for conn in clients:
            try:
                conn.close()
            except OSError:
                pass
        if clients:
            self._notify_clients(0)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._port = None

    # ── transport ──────────────────────────────────────────────────────────
    def _serve(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                break
            try:
                conn, peer = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                # Closed by stop(); anything else is also terminal here.
                break
            threading.Thread(
                target=self._handle, args=(conn, peer),
                name="mewgenics-bridge-conn", daemon=True).start()

    def _handle(self, conn: socket.socket, peer: tuple) -> None:
        if not self._register(conn):
            self._log.warning("bridge: client limit (%d) reached; refusing %s",
                              MAX_CLIENTS, peer)
            conn.close()
            return
        try:
            self._read_loop(conn, peer)
        finally:
            self._unregister(conn)
            conn.close()

    def _read_loop(self, conn: socket.socket, peer: tuple) -> None:
        conn.settimeout(ACCEPT_TIMEOUT)
        buffer = b""
        while not self._stop.is_set():
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            if len(buffer) > MAX_LINE_BYTES and b"\n" not in buffer:
                self._log.warning("bridge: dropping oversized message from %s", peer)
                return
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                self._dispatch(line, peer)

    def _dispatch(self, line: bytes, peer: tuple) -> None:
        if not line.strip():
            return
        if len(line) > MAX_LINE_BYTES:
            self._log.warning("bridge: dropping oversized message from %s", peer)
            return
        try:
            message = parse_message(line.decode("utf-8", errors="strict"))
        except (ProtocolError, UnicodeDecodeError) as exc:
            self._log.warning("bridge: ignoring bad message from %s: %s", peer, exc)
            return
        # A UI bug must never kill the listener thread, but it must leave a
        # trace - silent bridge failures are the worst kind.
        if isinstance(message, SaveRequest):
            self._notify_save(message)
        else:
            self._notify_focus(message)

    def _notify_focus(self, request: FocusRequest) -> None:
        try:
            self._on_focus(request)
        except Exception:
            self._log.exception("bridge: focus handler failed")

    def _notify_save(self, request: SaveRequest) -> None:
        if self._on_save is None:
            self._log.debug("bridge: save %r reported; no handler installed",
                            request.file)
            return
        try:
            self._on_save(request)
        except Exception:
            self._log.exception("bridge: save handler failed")
