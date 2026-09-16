"""``core/bridge.py``: regression coverage for the shutdown race.

``stop()`` closes every registered client socket. The connection thread that
was just accepted may not have reached its read loop yet, so the loop's setup
call (``conn.settimeout``) ran against an already-closed socket and raised
``OSError`` (EBADF), which escaped the daemon thread as an unhandled exception.

Reproducing that by racing ``stop()`` against ``accept()`` is what made it flaky.
These tests build the exact situation from a socket that is already closed and
hand it straight to the read loop / connection handler, so the outcome does not
depend on thread scheduling. They pin:

  * the read loop returning quietly for a closed socket,
  * the connection handler containing that case with no peer left registered
    and no unhandled thread exception,
  * an unexpected error while serving a connection being contained and logged
    with a traceback,
  * the ordinary paths (valid message, disconnect, stop-with-client) still
    working, without a thread exception.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from contextlib import contextmanager

from mewgenics_overlay.core import bridge

LOGGER_NAME = "mewgenics_overlay.bridge"


# ── helpers ─────────────────────────────────────────────────────────────────

def closed_socket() -> socket.socket:
    """A real socket that has already been closed (EBADF on any use)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.close()
    return sock


@contextmanager
def thread_exceptions():
    """Collect unhandled exceptions raised by ``threading.Thread`` bodies.

    Yields the list the hook appends to. The hook is restored afterwards, so a
    failure cannot leak into other tests.
    """
    caught = []
    previous = threading.excepthook

    def hook(args):
        caught.append(args)

    threading.excepthook = hook
    try:
        yield caught
    finally:
        threading.excepthook = previous


def bridge_failures(caught):
    """Only exceptions from bridge connection threads, not other test threads."""
    return [args for args in caught if args.thread.name.startswith("mewgenics")]


def join_connection_threads(timeout: float = 3.0) -> None:
    """Wait (bounded) for every live bridge connection thread to finish.

    ``stop()`` closes the client sockets but does not join their threads; the
    read loop notices via its 0.5 s recv timeout. Joining by name makes the
    "no unhandled exception" assertion wait for the thread rather than race it.
    """
    end = time.monotonic() + timeout
    for thread in threading.enumerate():
        if not thread.name.startswith("mewgenics"):
            continue
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        if thread.is_alive():
            thread.join(timeout=remaining)


class FocusRecorder:
    """Record focus requests, with an event to wait on without sleeping."""

    def __init__(self):
        self.requests = []
        self.event = threading.Event()

    def __call__(self, request):
        self.requests.append(request)
        self.event.set()


class CountRecorder:
    """Record client counts, waiting for the *n*-th without sleeping."""

    def __init__(self):
        self.values = []
        self._cv = threading.Condition()

    def __call__(self, count):
        with self._cv:
            self.values.append(count)
            self._cv.notify_all()

    def wait_for_len(self, count, timeout=3.0):
        end = time.monotonic() + timeout
        with self._cv:
            while len(self.values) < count:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return False
                self._cv.wait(remaining)
        return True


# ── the closed socket handed to the read loop ───────────────────────────────

def test_read_loop_with_a_closed_socket_returns_quietly(caplog):
    server = bridge.BridgeServer(on_focus=lambda _r: None, port=0)
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        # Must not raise: stop() closing the socket before the loop starts is a
        # normal shutdown, not a failure.
        server._read_loop(closed_socket(), ("127.0.0.1", 40000))

    messages = [record.getMessage() for record in caplog.records]
    assert any("closed before read loop" in m for m in messages)
    assert not any(record.exc_info for record in caplog.records), \
        "a quiet shutdown was logged as a failure"


def test_handle_with_a_closed_socket_leaves_no_client_and_no_thread_exception():
    server = bridge.BridgeServer(on_focus=lambda _r: None, port=0)
    join_connection_threads()
    with thread_exceptions() as caught:
        worker = threading.Thread(
            target=server._handle,
            args=(closed_socket(), ("127.0.0.1", 40001)),
            name="mewgenics-bridge-conn", daemon=True)
        worker.start()
        worker.join(timeout=3.0)
        assert not worker.is_alive(), "connection handler did not return"

    assert bridge_failures(caught) == []
    assert server.client_count == 0


# ── an unexpected error while serving a connection ──────────────────────────

def test_unexpected_serve_error_is_contained_and_logged(monkeypatch, caplog):
    server = bridge.BridgeServer(on_focus=lambda _r: None, port=0)

    def explode(_conn, _peer):
        raise RuntimeError("read loop exploded")

    monkeypatch.setattr(server, "_read_loop", explode)
    with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
        # Must not raise: one bad connection may not kill its thread.
        server._handle(closed_socket(), ("127.0.0.1", 40002))

    assert server.client_count == 0
    failures = [record for record in caplog.records
                if "connection" in record.getMessage()
                and "failed" in record.getMessage()]
    assert failures, "contained failure left no log record"
    assert failures[0].exc_info is not None, "failure logged without a traceback"
    assert failures[0].exc_info[0] is RuntimeError


# ── the ordinary paths, unchanged ───────────────────────────────────────────

def test_a_valid_message_still_reaches_the_callback():
    recorder = FocusRecorder()
    server = bridge.BridgeServer(on_focus=recorder, port=0)
    assert server.start() is True
    try:
        with socket.create_connection(("127.0.0.1", server.port),
                                      timeout=3.0) as client:
            client.sendall(
                json.dumps({"v": 1, "type": "focus", "key": 341}).encode() + b"\n")
            assert recorder.event.wait(3.0), "valid message was not delivered"
        assert recorder.requests[0].key == 341
    finally:
        server.stop()


def test_a_disconnecting_client_is_unregistered_without_a_thread_exception():
    counts = CountRecorder()
    server = bridge.BridgeServer(on_focus=lambda _r: None, port=0,
                                 on_clients_changed=counts)
    assert server.start() is True
    client = socket.create_connection(("127.0.0.1", server.port), timeout=3.0)
    try:
        assert counts.wait_for_len(1), "client never registered"
        assert counts.values == [1]

        join_connection_threads()
        with thread_exceptions() as caught:
            client.close()
            assert counts.wait_for_len(2), "disconnect did not unregister"
            join_connection_threads()

        assert counts.values[:2] == [1, 0]
        assert server.client_count == 0
        assert bridge_failures(caught) == []
    finally:
        client.close()
        server.stop()


def test_stop_with_a_client_connected_leaves_nothing_registered():
    counts = CountRecorder()
    server = bridge.BridgeServer(on_focus=lambda _r: None, port=0,
                                 on_clients_changed=counts)
    assert server.start() is True
    client = socket.create_connection(("127.0.0.1", server.port), timeout=3.0)
    try:
        assert counts.wait_for_len(1), "client never registered"

        join_connection_threads()
        with thread_exceptions() as caught:
            server.stop()
            join_connection_threads()

        assert server.client_count == 0
        assert server.running is False
        assert bridge_failures(caught) == []
    finally:
        client.close()
        server.stop()
