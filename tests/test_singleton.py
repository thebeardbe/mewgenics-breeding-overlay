"""``ui/singleton.py``: single-instance channel and toggle hand-off.

On Linux the desktop environment owns the global key and runs the app with
``--toggle``; that second process must forward a toggle to the running overlay
instead of opening a second window. These tests drive the real
``QLocalServer``/``QLocalSocket`` pair (the same code path the app uses), with
a manually pumped event loop. No sleeps and no fixed timing: each wait spins
``processEvents`` until the condition holds or a bounded attempt count runs
out.

Every test uses a throwaway channel name so nothing collides with a real
overlay (or a parallel test run).
"""

from __future__ import annotations

import logging
import os
import uuid

# A real QApplication is used because other UI test modules in the same test
# session create one; a bare QCoreApplication would break them.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mewgenics_overlay.ui import singleton  # noqa: E402
from mewgenics_overlay.ui.singleton import SingleInstance  # noqa: E402


# ── fixtures / helpers ─────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def make_instance(qapp):
    """Factory for ``SingleInstance`` objects; closes every one at teardown."""
    made = []

    def _make(name=None):
        instance = SingleInstance(name=name)
        made.append(instance)
        return instance

    yield _make
    for instance in made:
        try:
            instance.close()
        except Exception:                     # teardown must never mask a test
            QLocalServer.removeServer(instance.channel)
    # ``deleteLater`` queued by the connections is only processed with a
    # nested event loop. Flush it now, while the Python wrappers are still
    # alive: left pending, a later test's event loop deletes QObjects whose
    # owners are gone and can crash the interpreter.
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _unique(prefix: str = "mewgenics-overlay-test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _pump(qapp, predicate, attempts: int = 400) -> bool:
    """Process events until *predicate* holds; never sleeps."""
    for _ in range(attempts):
        if predicate():
            return True
        qapp.processEvents()
    return predicate()


# ── 1. channel name ────────────────────────────────────────────────────────
def test_channel_name_is_stable_and_namespaced():
    name = singleton.channel_name()
    assert name == singleton.channel_name()
    assert name.startswith("mewgenics-overlay-")
    # Filesystem-safe: no separators or spaces that would break a socket path.
    assert "/" not in name
    assert "\\" not in name
    assert " " not in name


def test_channel_name_is_scoped_to_the_current_user(monkeypatch):
    monkeypatch.setattr(singleton.getpass, "getuser", lambda: "alice")
    alice = singleton.channel_name()
    monkeypatch.setattr(singleton.getpass, "getuser", lambda: "bob")
    bob = singleton.channel_name()
    assert alice != bob
    assert alice.startswith("mewgenics-overlay-")
    assert bob.startswith("mewgenics-overlay-")


def test_explicit_name_is_used_verbatim(make_instance):
    instance = make_instance("mewgenics-overlay-explicit")
    assert instance.channel == "mewgenics-overlay-explicit"


# ── 2. acquisition ─────────────────────────────────────────────────────────
def test_first_instance_acquires_and_second_is_refused(make_instance):
    name = _unique()
    first = make_instance(name)
    assert first.try_acquire() is True

    second = make_instance(name)
    assert second.try_acquire() is False


def test_close_releases_the_channel_for_reacquisition(make_instance):
    name = _unique()
    first = make_instance(name)
    assert first.try_acquire() is True

    first.close()
    # close() is idempotent, so calling it twice must not raise.
    first.close()

    again = make_instance(name)
    assert again.try_acquire() is True


# ── 3. sending a toggle ────────────────────────────────────────────────────
def test_send_toggle_without_a_listener_returns_false(make_instance):
    client = make_instance(_unique())
    assert client.send_toggle() is False


def test_listener_receives_one_forwarded_toggle(qapp, make_instance):
    name = _unique()
    server = make_instance(name)
    assert server.try_acquire() is True
    got: list[int] = []
    server.listen(lambda: got.append(1))

    client = make_instance(name)
    assert client.send_toggle() is True

    assert _pump(qapp, lambda: len(got) >= 1) is True
    # A single forwarded message must fire the callback exactly once, not once
    # per read/disconnect event.
    for _ in range(50):
        qapp.processEvents()
    assert got == [1]


def test_listener_runs_once_per_forwarded_toggle(qapp, make_instance):
    name = _unique()
    server = make_instance(name)
    assert server.try_acquire() is True
    got: list[int] = []
    server.listen(lambda: got.append(1))

    first = make_instance(name)
    assert first.send_toggle() is True
    assert _pump(qapp, lambda: len(got) >= 1) is True

    second = make_instance(name)
    assert second.send_toggle() is True
    assert _pump(qapp, lambda: len(got) >= 2) is True

    assert got == [1, 1]


def test_unknown_messages_are_ignored(make_instance):
    server = make_instance(_unique())
    assert server.try_acquire() is True
    got: list[int] = []
    server.listen(lambda: got.append(1))

    server._handle_message(b"not-a-toggle")
    assert got == []

    server._handle_message(b"toggle")
    assert got == [1]


# ── 3b. a toggle that arrives before listen() ──────────────────────────────
def test_toggle_before_listen_is_buffered_and_delivered_once(make_instance):
    # The channel accepts connections before listen() registers the callback;
    # a second launch in that narrow startup window must not be dropped.
    server = make_instance(_unique())
    assert server.try_acquire() is True
    got: list[int] = []

    server._handle_message(b"toggle")
    assert server._pending_toggle is True
    assert got == []

    server.listen(lambda: got.append(1))
    assert got == [1]
    assert server._pending_toggle is False

    # A live toggle after listen() is delivered too, so the buffered one was
    # neither lost nor duplicated.
    server._handle_message(b"toggle")
    assert got == [1, 1]


def test_at_most_one_toggle_is_buffered(make_instance, qapp):
    # Several toggles before the overlay is ready collapse to one; a stale
    # queue must not fire the handler repeatedly once listen() runs.
    server = make_instance(_unique())
    assert server.try_acquire() is True
    got: list[int] = []

    for _ in range(3):
        server._handle_message(b"toggle")
    assert server._pending_toggle is True
    assert got == []

    server.listen(lambda: got.append(1))
    assert got == [1]
    for _ in range(20):
        qapp.processEvents()
    assert got == [1]


def test_unknown_message_before_listen_is_not_buffered(make_instance):
    server = make_instance(_unique())
    assert server.try_acquire() is True
    got: list[int] = []

    server._handle_message(b"something-else")
    assert server._pending_toggle is False

    server.listen(lambda: got.append(1))
    assert got == []


def test_forwarded_toggle_before_listen_is_delivered_when_listen_runs(
        qapp, make_instance):
    # End-to-end through the real socket pair: the message is accepted and
    # buffered before listen() exists, then delivered exactly once.
    name = _unique()
    server = make_instance(name)
    assert server.try_acquire() is True

    client = make_instance(name)
    assert client.send_toggle() is True
    assert _pump(qapp, lambda: server._pending_toggle) is True

    got: list[int] = []
    server.listen(lambda: got.append(1))
    assert got == [1]

    again = make_instance(name)
    assert again.send_toggle() is True
    assert _pump(qapp, lambda: got == [1, 1]) is True


# ── 4. listen-first acquisition: never unlink a live peer ──────────────────
def test_try_acquire_never_removes_a_live_peers_socket(monkeypatch,
                                                       make_instance):
    name = _unique()
    first = make_instance(name)
    assert first.try_acquire() is True

    removed: list[str] = []
    real_remove = QLocalServer.removeServer
    monkeypatch.setattr(
        QLocalServer, "removeServer",
        staticmethod(lambda n: removed.append(n) or real_remove(n)))

    second = make_instance(name)
    assert second.try_acquire() is False
    # A live peer owns the channel: probing ruled it out, so the socket file
    # must not have been unlinked from under the running instance.
    assert removed == []


def test_a_second_instance_leaves_the_peers_server_serving(qapp,
                                                           make_instance):
    name = _unique()
    first = make_instance(name)
    assert first.try_acquire() is True
    got: list[int] = []
    first.listen(lambda: got.append(1))

    second = make_instance(name)
    assert second.try_acquire() is False

    # The channel still works for a third process, proving the peer's socket
    # was neither removed nor rebound.
    client = make_instance(name)
    assert client.send_toggle() is True
    assert _pump(qapp, lambda: got == [1]) is True


def test_a_stale_socket_file_is_reclaimed(make_instance):
    name = _unique()
    stale = QLocalServer()
    assert stale.listen(name) is True
    stale.close()                      # no listener answers any more

    instance = make_instance(name)
    assert instance.try_acquire() is True


# ── 5. oversized / never-terminated streams are dropped ────────────────────
def _raw_send(name: str, payload: bytes) -> QLocalSocket:
    """Connect a raw client and write *payload*, bypassing ``send_toggle``."""
    socket = QLocalSocket()
    socket.connectToServer(name)
    assert socket.waitForConnected(1000)
    socket.write(payload)
    socket.flush()
    socket.waitForBytesWritten(1000)
    return socket


def _oversized_client(qapp, make_instance, caplog, payload):
    """A listening server plus a client that sent *payload*; returns server."""
    name = _unique()
    server = make_instance(name)
    assert server.try_acquire() is True
    got: list[int] = []
    server.listen(lambda: got.append(1))
    caplog.set_level(logging.WARNING, logger="mewgenics_overlay.singleton")

    client = _raw_send(name, payload)
    assert _pump(qapp, lambda: "oversized" in caplog.text) is True
    assert _pump(qapp, lambda: server._connections == []) is True
    assert got == []
    client.abort()
    return server


def test_stream_without_a_newline_is_dropped(qapp, make_instance, caplog):
    _oversized_client(qapp, make_instance, caplog,
                      b"x" * (singleton._MAX_MESSAGE_BYTES + 64))
    assert "oversized" in caplog.text


def test_oversized_line_is_dropped(qapp, make_instance, caplog):
    _oversized_client(qapp, make_instance, caplog,
                      b"x" * (singleton._MAX_MESSAGE_BYTES + 1) + b"\n")
    assert "oversized" in caplog.text


def test_message_at_the_cap_is_not_treated_as_oversized(qapp, make_instance,
                                                        caplog):
    name = _unique()
    server = make_instance(name)
    assert server.try_acquire() is True
    server.listen(lambda: None)
    caplog.set_level(logging.WARNING, logger="mewgenics_overlay.singleton")

    client = _raw_send(name, b"z" * singleton._MAX_MESSAGE_BYTES + b"\n")

    # Wait until the message was actually read and (correctly) treated as an
    # unknown message: exactly the cap is accepted, only a value strictly
    # over the cap is dropped as oversized.
    assert _pump(qapp, lambda: "ignoring unknown" in caplog.text) is True
    assert "oversized" not in caplog.text
    client.abort()


def test_server_keeps_serving_after_an_oversized_message(qapp, make_instance,
                                                         caplog):
    name = _unique()
    server = make_instance(name)
    assert server.try_acquire() is True
    got: list[int] = []
    server.listen(lambda: got.append(1))
    caplog.set_level(logging.WARNING, logger="mewgenics_overlay.singleton")

    client = _raw_send(name, b"y" * (singleton._MAX_MESSAGE_BYTES + 32))
    assert _pump(qapp, lambda: "oversized" in caplog.text) is True
    client.abort()
    assert _pump(qapp, lambda: server._connections == []) is True

    sender = make_instance(name)
    assert sender.send_toggle() is True
    assert _pump(qapp, lambda: got == [1]) is True


# ── 6. send_toggle delivery judgement (injected socket) ────────────────────
# ``send_toggle`` judges delivery by the connection state, the bytes accepted
# and any real socket error, not by the flush report alone: an in-process or
# busy event loop can report ``waitForBytesWritten`` as False before the
# kernel drained the buffer even though every byte was queued. These tests
# inject a fake ``QLocalSocket`` so each genuine failure mode (no connection,
# short write, closed peer, real socket error) is pinned without a real peer.
def _inject_socket(monkeypatch, **cfg):
    """Replace ``singleton.QLocalSocket`` with a configurable fake."""
    created = []

    class FakeSocket:
        LocalSocketState = QLocalSocket.LocalSocketState
        LocalSocketError = QLocalSocket.LocalSocketError

        def __init__(self):
            self.disconnect_calls = 0
            self.written = 0
            created.append(self)

        def connectToServer(self, name):
            self.name = name

        def waitForConnected(self, ms):
            return cfg.get("connects", True)

        def write(self, data):
            self.written = cfg.get("written", len(data))
            return self.written

        def flush(self):
            self.flushed = True

        def waitForBytesWritten(self, ms):
            return cfg.get("flushed", True)

        def state(self):
            return cfg.get(
                "state", QLocalSocket.LocalSocketState.ConnectedState)

        def error(self):
            return cfg.get(
                "error", QLocalSocket.LocalSocketError.UnknownSocketError)

        def errorString(self):
            return cfg.get("error_string", "fake socket error")

        def disconnectFromServer(self):
            self.disconnect_calls += 1

    monkeypatch.setattr(singleton, "QLocalSocket", FakeSocket)
    return created


def test_send_toggle_succeeds_when_all_bytes_are_written_without_a_flush(
        monkeypatch, make_instance):
    # The flush report alone must not turn a delivered toggle into a failure.
    created = _inject_socket(monkeypatch, connects=True, flushed=False)

    client = make_instance(_unique())

    assert client.send_toggle() is True
    assert created[0].written == len(singleton._TOGGLE_MESSAGE)
    assert created[0].disconnect_calls == 1


def test_send_toggle_succeeds_for_a_connected_socket(monkeypatch,
                                                     make_instance):
    _inject_socket(monkeypatch)

    assert make_instance(_unique()).send_toggle() is True


def test_send_toggle_fails_on_a_short_write(monkeypatch, make_instance):
    _inject_socket(monkeypatch, written=len(singleton._TOGGLE_MESSAGE) - 1)
    client = make_instance(_unique())

    assert client.send_toggle() is False


def test_send_toggle_fails_when_the_peer_closed(monkeypatch, make_instance):
    # A peer that went away leaves the socket unconnected even though the
    # connect probe initially succeeded.
    _inject_socket(
        monkeypatch, state=QLocalSocket.LocalSocketState.UnconnectedState)

    assert make_instance(_unique()).send_toggle() is False


def test_send_toggle_fails_on_a_real_socket_error(monkeypatch, make_instance):
    _inject_socket(
        monkeypatch,
        error=QLocalSocket.LocalSocketError.PeerClosedError)

    assert make_instance(_unique()).send_toggle() is False


def test_send_toggle_fails_when_the_connection_is_refused(monkeypatch,
                                                          make_instance):
    created = _inject_socket(monkeypatch, connects=False)
    client = make_instance(_unique())

    assert client.send_toggle() is False
    # Nothing was written and there was never a connection to drop.
    assert created[0].written == 0
    assert created[0].disconnect_calls == 0


def test_send_toggle_failure_is_logged(monkeypatch, make_instance, caplog):
    _inject_socket(monkeypatch, written=1)
    caplog.set_level(logging.WARNING, logger="mewgenics_overlay.singleton")

    assert make_instance(_unique()).send_toggle() is False
    assert "could not send the toggle" in caplog.text
