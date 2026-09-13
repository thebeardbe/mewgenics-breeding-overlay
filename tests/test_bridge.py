"""``core/bridge.py``: in-game focus requests over loopback TCP.

Covers the wire protocol, request-to-cat resolution (key, uid, exact name,
ambiguous name refused), and a real server round-trip including malformed and
oversized input. The module is Qt-free, so these tests need no Qt and no save
file: sessions are duck-typed.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from mewgenics_overlay.core import bridge


# ── fixtures ────────────────────────────────────────────────────────────────

class FakeCat:
    def __init__(self, db_key, name, unique_id):
        self.db_key = db_key
        self.name = name
        self.unique_id = unique_id


def fake_session(cats):
    return SimpleNamespace(by_key={c.db_key: c for c in cats}, cats=list(cats))


@pytest.fixture
def session():
    return fake_session([
        FakeCat(341, "L'Via", "0x140e6990d8ce6041"),
        FakeCat(349, "Murphy", "0xdae1444862dc29fa"),
        FakeCat(360, "Murphy", "0xbc2a91889655ed19"),   # duplicate name
    ])


def message(**fields):
    payload = {"v": bridge.PROTOCOL_VERSION, "type": "focus"}
    payload.update(fields)
    return json.dumps(payload)


# ── protocol ────────────────────────────────────────────────────────────────

def test_parse_focus_message_reads_key_uid_and_name():
    req = bridge.parse_message(message(key=341, uid="0xabc", name="L'Via"))
    assert (req.key, req.uid, req.name) == (341, "0xabc", "L'Via")


def test_parse_accepts_numeric_key_as_string():
    assert bridge.parse_message(message(key="341")).key == 341


def test_parse_allows_uid_only():
    req = bridge.parse_message(message(uid="0xabc"))
    assert req.key is None and req.uid == "0xabc"


@pytest.mark.parametrize("line", [
    "",                          # blank
    "{not json",                 # malformed
    "[1, 2, 3]",                 # not an object
    '{"v": 1, "type": "nope"}',  # unknown type
    '{"type": "focus", "key": 1}',        # missing version
    '{"v": 99, "type": "focus", "key": 1}',  # wrong version
    '{"v": 1, "type": "focus"}',          # no identifier
])
def test_parse_rejects_bad_messages(line):
    with pytest.raises(bridge.ProtocolError):
        bridge.parse_message(line)


# ── resolution ──────────────────────────────────────────────────────────────

def test_resolve_by_key(session):
    assert bridge.resolve_focus_key(session, bridge.FocusRequest(key=341)) == 341


def test_resolve_by_uid(session):
    req = bridge.FocusRequest(uid="0xDAE1444862DC29FA")   # case-insensitive
    assert bridge.resolve_focus_key(session, req) == 349


def test_resolve_by_exact_name(session):
    assert bridge.resolve_focus_key(session, bridge.FocusRequest(name="murphy")) is None


def test_resolve_by_unique_name():
    session = fake_session([FakeCat(7, "Chalky", "0x1")])
    assert bridge.resolve_focus_key(session, bridge.FocusRequest(name="chalky")) == 7


def test_resolve_refuses_ambiguous_name(session):
    # two "Murphy" rows: never guess
    assert bridge.resolve_focus_key(session, bridge.FocusRequest(name="Murphy")) is None


def test_resolve_unknown_key_is_none(session):
    assert bridge.resolve_focus_key(session, bridge.FocusRequest(key=999999)) is None


def test_resolve_without_session_is_none():
    assert bridge.resolve_focus_key(None, bridge.FocusRequest(key=1)) is None


def test_resolve_falls_through_key_to_name(session):
    # unknown key, valid name: still resolves
    req = bridge.FocusRequest(key=999999, name="L'Via")
    assert bridge.resolve_focus_key(session, req) == 341


# ── server ──────────────────────────────────────────────────────────────────

class Collector:
    def __init__(self):
        self.requests = []
        self.event = threading.Event()
        # A semaphore per request lets a test block until *n* requests have
        # arrived, without polling or sleeping.
        self._delivered = threading.Semaphore(0)

    def __call__(self, request):
        self.requests.append(request)
        self._delivered.release()
        self.event.set()

    def wait(self, timeout=3.0):
        return self.event.wait(timeout)

    def wait_count(self, count, timeout=3.0):
        """Block until *count* requests have been delivered (or time out)."""
        end = time.monotonic() + timeout
        for _ in range(count):
            remaining = end - time.monotonic()
            if remaining <= 0:
                return False
            if not self._delivered.acquire(timeout=remaining):
                return False
        return True


def start_server():
    collector = Collector()
    server = bridge.BridgeServer(on_focus=collector, port=0)
    assert server.start() is True
    return server, collector


def send(port, line: str):
    with socket.create_connection(("127.0.0.1", port), timeout=3.0) as sock:
        sock.sendall(line.encode("utf-8") + b"\n")


def test_server_delivers_a_focus_request():
    server, collector = start_server()
    try:
        send(server.port, message(key=341, name="L'Via"))
        assert collector.wait(), "no request delivered"
        assert collector.requests[0].key == 341
    finally:
        server.stop()


def test_server_survives_bad_input_then_delivers():
    server, collector = start_server()
    try:
        send(server.port, "garbage not json")
        send(server.port, message(key=349))
        assert collector.wait(), "valid request after garbage not delivered"
        assert collector.requests[-1].key == 349
    finally:
        server.stop()


def test_server_drops_oversized_line_and_stays_up():
    server, collector = start_server()
    try:
        send(server.port, "x" * (bridge.MAX_LINE_BYTES + 10))
        send(server.port, message(key=360))
        assert collector.wait(), "valid request after oversized line not delivered"
        assert collector.requests[-1].key == 360
    finally:
        server.stop()


def test_server_send_reaches_a_connected_client():
    server, _ = start_server()
    try:
        client = socket.create_connection(("127.0.0.1", server.port), timeout=3.0)
        deadline = time.time() + 2.0
        while server.client_count == 0 and time.time() < deadline:
            time.sleep(0.01)
        assert server.client_count == 1

        assert server.send({"v": 1, "type": "select", "key": 423}) == 1
        client.settimeout(3.0)
        payload = json.loads(client.recv(1024).decode("utf-8").strip())
        assert payload == {"v": 1, "type": "select", "key": 423}
        client.close()
    finally:
        server.stop()


def test_server_send_without_clients_is_zero():
    server, _ = start_server()
    try:
        assert server.send({"v": 1, "type": "select", "key": 1}) == 0
    finally:
        server.stop()


def test_server_keeps_the_connection_open_for_commands():
    server, collector = start_server()
    try:
        client = socket.create_connection(("127.0.0.1", server.port), timeout=3.0)
        client.sendall((message(key=341) + "\n").encode("utf-8"))
        assert collector.wait(), "focus not delivered"
        deadline = time.time() + 2.0
        while server.client_count == 0 and time.time() < deadline:
            time.sleep(0.01)
        assert server.send({"v": 1, "type": "select", "key": 341}) == 1
        client.settimeout(3.0)
        assert b"select" in client.recv(1024)
        client.close()
    finally:
        server.stop()


def test_server_stop_is_idempotent_and_releases_port():
    server, _ = start_server()
    port = server.port
    server.stop()
    assert server.running is False
    server.stop()
    # The port must be reusable straight away.
    again = bridge.BridgeServer(on_focus=lambda _r: None, port=port)
    assert again.start() is True
    again.stop()


def test_server_focus_handler_exception_does_not_kill_listener():
    def explode(_request):
        raise RuntimeError("boom")

    server = bridge.BridgeServer(on_focus=explode, port=0)
    assert server.start() is True
    try:
        send(server.port, message(key=1))
        # Still serving: a second send must not raise at the socket level.
        send(server.port, message(key=2))
        assert server.running is True
    finally:
        server.stop()


# ── registered-client cap ───────────────────────────────────────────────────
def test_server_refuses_connections_beyond_the_cap(caplog):
    server, collector = start_server()
    clients = []
    try:
        # Fill every slot, proving each connection registered by its focus
        # message (registration happens before the read loop dispatches).
        for index in range(bridge.MAX_CLIENTS):
            conn = socket.create_connection(
                ("127.0.0.1", server.port), timeout=3.0)
            conn.sendall((message(key=index) + "\n").encode("utf-8"))
            clients.append(conn)
        assert collector.wait_count(bridge.MAX_CLIENTS), "cap was not filled"
        assert server.client_count == bridge.MAX_CLIENTS

        with caplog.at_level(logging.WARNING,
                            logger="mewgenics_overlay.bridge"):
            extra = socket.create_connection(
                ("127.0.0.1", server.port), timeout=3.0)
            try:
                extra.settimeout(3.0)
                # A refused client is closed immediately, so it reads EOF.
                assert extra.recv(1024) == b""
            finally:
                extra.close()

        # The refused connection never counts against the live peers.
        assert server.client_count == bridge.MAX_CLIENTS
        assert any("client limit" in r.getMessage() for r in caplog.records)
    finally:
        for conn in clients:
            conn.close()
        server.stop()


# ── outbound readiness check ────────────────────────────────────────────────
class _FakeConn:
    """Hashable stand-in for a registered client socket (no real fd)."""

    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []
        self.closed = False

    def sendall(self, data):
        if self.fail:
            raise OSError("peer went away mid-write")
        self.sent.append(data)

    def close(self):
        self.closed = True


def _install_select(monkeypatch, is_writable):
    """Drive the readiness check without depending on real socket state.

    ``select.select`` returns ``(readable, writable, exceptional)``. The fake
    keeps that exact order, so an implementation that unpacks the wrong slot
    is caught rather than accidentally satisfied.
    """
    def fake_select(rlist, wlist, xlist, timeout=0):
        return ([c for c in rlist if is_writable(c)],
                [c for c in wlist if is_writable(c)],
                [])

    monkeypatch.setattr(bridge, "select",
                        SimpleNamespace(select=fake_select))


def _fresh_server():
    return bridge.BridgeServer(on_focus=lambda _r: None, port=0)


def test_send_delivers_to_a_writable_peer(monkeypatch):
    server = _fresh_server()
    conn = _FakeConn()
    assert server._register(conn) is True
    _install_select(monkeypatch, lambda _c: True)

    assert server.send({"v": 1, "type": "select", "key": 423}) == 1

    assert json.loads(conn.sent[0].decode("utf-8")) == {
        "v": 1, "type": "select", "key": 423}
    assert server.client_count == 1


def test_send_drops_a_non_writable_peer_and_logs(monkeypatch, caplog):
    server = _fresh_server()
    conn = _FakeConn()
    assert server._register(conn) is True
    _install_select(monkeypatch, lambda _c: False)

    with caplog.at_level(logging.WARNING,
                         logger="mewgenics_overlay.bridge"):
        assert server.send({"v": 1, "type": "select", "key": 5}) == 0

    # The stalled peer is gone, its socket closed, and it was never written.
    assert conn.sent == []
    assert conn.closed is True
    assert server.client_count == 0
    assert any("not writable" in r.getMessage() for r in caplog.records)


def test_send_counts_only_successful_writes(monkeypatch):
    server = _fresh_server()
    good, bad = _FakeConn(), _FakeConn(fail=True)
    assert server._register(good) is True
    assert server._register(bad) is True
    _install_select(monkeypatch, lambda _c: True)

    assert server.send({"v": 1, "type": "select", "key": 7}) == 1

    assert good.sent                       # the healthy peer got the line
    assert server.client_count == 1        # the broken peer was dropped


# ── save reports (which save the game is playing) ───────────────────────────
def save_message(file):
    return json.dumps({"v": bridge.PROTOCOL_VERSION, "type": "save",
                       "file": file})


def start_server_with_save():
    """Server with both callbacks, so save reports never land as focus ones."""
    focus = Collector()
    saves = Collector()
    server = bridge.BridgeServer(on_focus=focus, on_save=saves, port=0)
    assert server.start() is True
    return server, focus, saves


def send_lines(port, lines):
    """Send every line down *one* connection, proving it survives them all."""
    with socket.create_connection(("127.0.0.1", port), timeout=3.0) as sock:
        for line in lines:
            sock.sendall(line.encode("utf-8") + b"\n")


def test_parse_save_message_reads_the_file_name():
    req = bridge.parse_message(save_message("steamcampaign02.sav"))
    assert isinstance(req, bridge.SaveRequest)
    assert req.file == "steamcampaign02.sav"


def test_parse_save_message_strips_surrounding_whitespace():
    assert bridge.parse_message(save_message(" steamcampaign02.sav ")).file == \
        "steamcampaign02.sav"


@pytest.mark.parametrize("file", [
    "../../steamcampaign01.sav",       # traversal
    "..\\..\\steamcampaign01.sav",
    "sub/steamcampaign01.sav",         # path separator
    "C:steamcampaign01.sav",           # drive specifier
    "/steam/root/steamcampaign01.sav",  # absolute path
    "notes.txt",                       # not a save
    "steamcampaign01.sav.exe",         # wrong suffix
    "", " ", "..", ".",
    None, 123, ["steamcampaign01.sav"], {"name": "steamcampaign01.sav"},
])
def test_parse_rejects_a_bad_save_file_name(file):
    with pytest.raises(bridge.ProtocolError):
        bridge.parse_message(save_message(file))


def test_server_delivers_a_save_request():
    server, focus, saves = start_server_with_save()
    try:
        send(server.port, save_message("steamcampaign02.sav"))
        assert saves.wait(), "no save report delivered"
        assert saves.requests[0].file == "steamcampaign02.sav"
        assert saves.requests[0] == bridge.SaveRequest("steamcampaign02.sav")
        # A save report must never be mistaken for a focus request.
        assert focus.requests == []
    finally:
        server.stop()


@pytest.mark.parametrize("bad", [
    save_message("../../steamcampaign01.sav"),
    save_message("sub/steamcampaign01.sav"),
    save_message("notes.txt"),
    save_message(123),
    save_message(None),
    '{"v": 1, "type": "save"}',                      # no file at all
    '{"v": 1, "type": "select", "key": 341}',        # unknown type
    '{"v": 99, "type": "save", "file": "a.sav"}',    # wrong version
])
def test_server_rejects_a_bad_save_message_and_keeps_the_connection(bad):
    server, focus, saves = start_server_with_save()
    try:
        send_lines(server.port, [bad, message(key=349)])

        assert focus.wait(), "connection did not survive the rejected message"
        assert focus.requests[-1].key == 349
        assert saves.requests == []
        assert server.running is True
    finally:
        server.stop()


class LogRecordWaiter(logging.Handler):
    """Capture bridge log records and wait for one as the handler runs.

    A save report is dispatched by a connection (transport) thread, so a
    ``caplog.at_level`` block can exit before that thread has logged; the
    assertion then races the capture. This handler stays installed for the
    whole test and lets the test wait for the record itself, so the outcome
    does not depend on a second message being processed first.
    """

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []
        self._cv = threading.Condition()

    def emit(self, record):
        with self._cv:
            self.records.append(record)
            self._cv.notify_all()

    def wait_for(self, predicate, timeout=3.0):
        """Block until *predicate* matches a captured record (or time out)."""
        end = time.monotonic() + timeout
        with self._cv:
            while True:
                if any(predicate(record) for record in self.records):
                    return True
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return False
                self._cv.wait(remaining)


@contextmanager
def capture_bridge_records(logger_name="mewgenics_overlay.bridge"):
    """Keep a waiting log handler installed for the whole ``with`` block."""
    logger = logging.getLogger(logger_name)
    handler = LogRecordWaiter()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def test_server_reports_a_save_when_no_handler_is_installed():
    # A build without an on_save callback must ignore the report (with a
    # trace) instead of crashing the listener thread. The trace is logged by
    # the connection's transport thread, so wait for that record while the
    # capture is still installed rather than racing the capture block.
    collector = Collector()
    server = bridge.BridgeServer(on_focus=collector, port=0)
    assert server.start() is True
    try:
        with capture_bridge_records() as logs:
            send(server.port, save_message("steamcampaign01.sav"))
            assert logs.wait_for(
                lambda r: "no handler installed" in r.getMessage()
            ), "unhandled save report left no trace"

            # The listener survived it: a following focus request is handled.
            send(server.port, message(key=341))
            assert collector.wait(), "listener died on an unhandled save report"
    finally:
        server.stop()


# ── client-count callback (drives the UI's "game connected" flag) ───────────
class CountRecorder:
    """Record every client count, waiting for the *n*-th without sleeping."""

    def __init__(self):
        self.values = []
        self._cv = threading.Condition()

    def __call__(self, count):
        with self._cv:
            self.values.append(count)
            self._cv.notify_all()

    def wait_for(self, count, timeout=3.0):
        end = time.monotonic() + timeout
        with self._cv:
            while len(self.values) < count:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return False
                self._cv.wait(remaining)
        return True


def start_server_with_counts():
    counts = CountRecorder()
    server = bridge.BridgeServer(on_focus=lambda _r: None, port=0,
                                 on_clients_changed=counts)
    assert server.start() is True
    return server, counts


def test_client_count_fires_on_connect_and_disconnect():
    server, counts = start_server_with_counts()
    client = None
    try:
        client = socket.create_connection(("127.0.0.1", server.port),
                                          timeout=3.0)
        assert counts.wait_for(1), "connect did not change the count"
        assert counts.values[:1] == [1]

        client.close()
        assert counts.wait_for(2), "disconnect did not change the count"
        assert counts.values[:2] == [1, 0]
    finally:
        if client is not None:
            client.close()
        server.stop()


def test_client_count_fires_on_stop_with_a_client_connected():
    server, counts = start_server_with_counts()
    client = None
    try:
        client = socket.create_connection(("127.0.0.1", server.port),
                                          timeout=3.0)
        assert counts.wait_for(1)

        server.stop()

        assert counts.wait_for(2), "stop did not report the game offline"
        assert counts.values[:2] == [1, 0]
    finally:
        if client is not None:
            client.close()
        server.stop()


def test_stop_without_clients_does_not_report():
    server, counts = start_server_with_counts()
    try:
        server.stop()
        assert counts.values == []
    finally:
        server.stop()


def test_a_connection_refused_by_the_cap_never_changes_the_count():
    server, counts = start_server_with_counts()
    clients = []
    extra = None
    try:
        for _ in range(bridge.MAX_CLIENTS):
            clients.append(socket.create_connection(
                ("127.0.0.1", server.port), timeout=3.0))
        assert counts.wait_for(bridge.MAX_CLIENTS), "cap was not filled"
        assert counts.values == list(range(1, bridge.MAX_CLIENTS + 1))

        extra = socket.create_connection(("127.0.0.1", server.port),
                                         timeout=3.0)
        extra.settimeout(3.0)
        # The refused client is closed immediately, so it reads EOF. Once that
        # is observed the server has already taken the refusal path, which must
        # not have notified anyone.
        assert extra.recv(1024) == b""
        assert counts.values == list(range(1, bridge.MAX_CLIENTS + 1))
    finally:
        for conn in clients:
            conn.close()
        if extra is not None:
            extra.close()
        server.stop()
