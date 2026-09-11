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
