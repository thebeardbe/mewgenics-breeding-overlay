"""``core/bridge.py``: in-game focus requests over loopback TCP.

Covers the wire protocol, request-to-cat resolution (key, uid, exact name,
ambiguous name refused), and a real server round-trip including malformed and
oversized input. The module is Qt-free, so these tests need no Qt and no save
file: sessions are duck-typed.
"""

from __future__ import annotations

import json
import socket
import threading
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

    def __call__(self, request):
        self.requests.append(request)
        self.event.set()

    def wait(self, timeout=3.0):
        return self.event.wait(timeout)


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
