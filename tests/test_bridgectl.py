"""``ui/bridgectl.py``: the Qt hop for bridge messages.

``BridgeController`` owns a real ``BridgeServer`` and re-emits each message as
a Qt signal, so ``app.main``'s slots run on the UI thread. These tests drive the
controller's callbacks synchronously (the loopback transport itself, fakes and
malformed input are covered by ``test_bridge.py``) and assert the payloads on
the new ``raise_requested``, ``save_reported`` and ``game_online`` signals plus
the existing ``focus_requested`` one.
"""

from __future__ import annotations

import json
import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mewgenics_overlay.core import bridge  # noqa: E402
from mewgenics_overlay.ui import bridgectl  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app_ = QApplication.instance() or QApplication([])
    yield app_


class Recorder(QObject):
    """QObject receiver: same-thread emissions are delivered synchronously."""

    def __init__(self):
        super().__init__()
        self.focus = []
        self.raises = []
        self.saves = []
        self.online = []

    def on_focus(self, request):
        self.focus.append(request)

    def on_raise(self, request):
        self.raises.append(request)

    def on_save(self, request):
        self.saves.append(request)

    def on_online(self, flag):
        self.online.append(flag)


class _FakeConn:
    """Hashable socket stand-in for the server's peer bookkeeping."""

    def close(self):
        pass


@pytest.fixture
def controller(qapp):
    record = Recorder()
    ctl = bridgectl.BridgeController(port=0)
    ctl.focus_requested.connect(record.on_focus)
    ctl.raise_requested.connect(record.on_raise)
    ctl.save_reported.connect(record.on_save)
    ctl.game_online.connect(record.on_online)
    yield ctl, record
    ctl.stop()


def dispatch(ctl, payload: dict):
    """Feed one protocol line through the real server parse/dispatch path."""
    line = json.dumps(payload).encode("utf-8")
    ctl._server._dispatch(line, ("127.0.0.1", 4242))


# ── save_reported ───────────────────────────────────────────────────────────
def test_save_reported_carries_the_save_request(controller):
    ctl, record = controller

    dispatch(ctl, {"v": 1, "type": "save", "file": "steamcampaign02.sav"})

    assert len(record.saves) == 1
    request = record.saves[0]
    assert isinstance(request, bridge.SaveRequest)
    assert request.file == "steamcampaign02.sav"
    # A save report is never also delivered as a focus request.
    assert record.focus == []


@pytest.mark.parametrize("file", [
    "../../steamcampaign01.sav",
    "sub/steamcampaign01.sav",
    "notes.txt",
    123,
    None,
])
def test_save_reported_is_silent_for_a_rejected_name(controller, file):
    ctl, record = controller

    dispatch(ctl, {"v": 1, "type": "save", "file": file})

    assert record.saves == []
    assert record.focus == []


def test_focus_requested_carries_the_focus_request(controller):
    ctl, record = controller

    dispatch(ctl, {"v": 1, "type": "focus", "key": 341, "name": "L'Via"})

    assert [(r.key, r.name) for r in record.focus] == [(341, "L'Via")]
    assert record.saves == []


# ── raise_requested ─────────────────────────────────────────────────────────
def test_raise_requested_carries_the_raise_request(controller):
    ctl, record = controller

    dispatch(ctl, {"v": 1, "type": "raise", "key": 341})

    assert len(record.raises) == 1
    request = record.raises[0]
    assert type(request) is bridge.RaiseRequest
    assert request.key == 341
    # A raise is never also delivered as a focus request.
    assert record.focus == []
    assert record.saves == []


def test_focus_requested_does_not_fire_raise_requested(controller):
    ctl, record = controller

    dispatch(ctl, {"v": 1, "type": "focus", "key": 349})

    assert [r.key for r in record.focus] == [349]
    assert record.raises == []


def test_raise_requested_does_not_fire_focus_requested(controller):
    ctl, record = controller

    dispatch(ctl, {"v": 1, "type": "raise", "key": 349})

    assert [r.key for r in record.raises] == [349]
    assert record.focus == []


@pytest.mark.parametrize("payload", [
    {"v": 1, "type": "raise"},                    # no identifier
    {"v": 1, "type": "raise", "key": True},       # bool key
    {"v": 99, "type": "raise", "key": 341},      # wrong version
    {"type": "raise", "key": 341},                # missing version
])
def test_raise_requested_is_silent_for_a_rejected_raise(controller, payload):
    ctl, record = controller

    dispatch(ctl, payload)

    assert record.raises == []
    assert record.focus == []
    assert record.saves == []


def test_a_save_report_does_not_fire_raise_requested(controller):
    ctl, record = controller

    dispatch(ctl, {"v": 1, "type": "save", "file": "steamcampaign02.sav"})

    assert record.raises == []
    assert len(record.saves) == 1


# ── game_online ─────────────────────────────────────────────────────────────
def test_game_online_is_true_on_connect_and_false_on_disconnect(controller):
    ctl, record = controller
    conn = _FakeConn()

    assert ctl._server._register(conn) is True
    assert record.online == [True]

    ctl._server._unregister(conn)
    assert record.online == [True, False]


def test_game_online_reports_offline_when_the_server_stops(controller):
    ctl, record = controller
    conn = _FakeConn()
    assert ctl._server._register(conn) is True

    ctl.stop()

    assert record.online == [True, False]


def test_game_online_does_not_fire_twice_for_the_same_peer(controller):
    ctl, record = controller
    conn = _FakeConn()
    ctl._server._register(conn)

    # An unknown peer must not look like a disconnect.
    ctl._server._unregister(_FakeConn())

    assert record.online == [True]


def test_game_online_reports_the_second_of_two_peers(controller):
    ctl, record = controller
    first, second = _FakeConn(), _FakeConn()

    ctl._server._register(first)
    ctl._server._register(second)
    assert record.online == [True, True]

    ctl._server._unregister(first)
    ctl._server._unregister(second)
    assert record.online == [True, True, True, False]


# ── public client count ─────────────────────────────────────────────────────
def test_client_count_is_public_and_tracks_connected_peers(controller):
    # The palette's in_game_available reads this public property; it must
    # mirror the server's own count as mod clients register and unregister.
    ctl, _ = controller
    assert ctl.client_count == 0

    first, second = _FakeConn(), _FakeConn()
    ctl._server._register(first)
    assert ctl.client_count == 1

    ctl._server._register(second)
    assert ctl.client_count == 2

    ctl._server._unregister(first)
    assert ctl.client_count == 1

    ctl._server._unregister(second)
    assert ctl.client_count == 0


def test_send_select_uses_the_protocol_version_and_key(controller,
                                                       monkeypatch):
    ctl, _ = controller
    sent = []
    monkeypatch.setattr(ctl._server, "send",
                        lambda payload: sent.append(payload) or 1)

    assert ctl.send_select(341) == 1

    assert sent == [{"v": bridge.PROTOCOL_VERSION, "type": "select",
                     "key": 341}]
