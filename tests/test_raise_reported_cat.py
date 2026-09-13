"""``PaletteWindow.raise_reported_cat``: select the reported cat, then raise.

The companion mod can report a cat *and* ask for the overlay, because the
player pressed an in-game call-to-action button. That is the deliberate
opposite of the silent focus path (``select_reported_cat``): the raise must
resolve and select the cat exactly like focus, and *then* bring the window to
the front through the normal engage path, in that order. A focus report must
stay silent - no engage, no show, raise, activate or focus.

Building a full ``PaletteWindow`` is heavy (save controller, watcher, asset
loader, threads), so these tests bind the real
``raise_reported_cat`` / ``select_reported_cat`` / ``set_focus_key`` methods to
a small ``QWidget`` stand-in carrying only the attributes they read
(``_session`` and ``_tablectl``), the same convention as ``test_reported_cat``
and ``test_show_in_game``. The stand-in overrides ``_engage`` and every window
operation to record calls, so the selection/engagement order is observable and
a regression that re-added a silent engage (or dropped the raise engage) fails
loudly.

Gap: a stand-in proves the method's contract, not that a live ``PaletteWindow``
build loads a session into the same attribute; that wiring is covered by the
save/reload tests. The app-level routing (which signal reaches which method) is
covered by ``test_app_bootstrap.py``.
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")
pytest.importorskip("lz4")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from mewgenics_overlay.core import bridge  # noqa: E402
from mewgenics_overlay.ui import palette  # noqa: E402

LOG_NAME = "mewgenics_overlay.ui"


# ── fakes / helpers ────────────────────────────────────────────────────────
class _RecordingTable:
    """Records every programmatic focus into the host's event log."""

    def __init__(self, events):
        self.keys = []
        self._events = events

    def set_focus_key(self, db_key):
        self.keys.append(db_key)
        self._events.append(("select", db_key))


class RaiseHost(QWidget):
    """Real ``QWidget`` carrying the real ``PaletteWindow`` methods under test.

    ``_engage`` (the normal summon path the raise must use) is recorded instead
    of run; every other window operation is recorded *and* delegated, so a
    method that showed or focused the window would be caught even when the
    state would not change (raising an already-visible window).
    """

    select_reported_cat = palette.PaletteWindow.select_reported_cat
    raise_reported_cat = palette.PaletteWindow.raise_reported_cat
    set_focus_key = palette.PaletteWindow.set_focus_key

    def __init__(self, session):
        super().__init__()
        self._session = session
        self.events = []
        self._tablectl = _RecordingTable(self.events)
        self.window_ops = []

    def _engage(self):
        self.events.append(("engage",))
        self.window_ops.append(("engage",))

    def show(self):  # noqa: N802 (Qt API)
        self.window_ops.append(("show",))
        super().show()

    def hide(self):  # noqa: N802 (Qt API)
        self.window_ops.append(("hide",))
        super().hide()

    def raise_(self):
        self.window_ops.append(("raise",))
        super().raise_()

    def activateWindow(self):  # noqa: N802 (Qt API)
        self.window_ops.append(("activate",))
        super().activateWindow()

    def setFocus(self, reason=None):  # noqa: N802 (Qt API)
        self.window_ops.append(("focus",))
        if reason is None:
            super().setFocus()
        else:
            super().setFocus(reason)

    def setVisible(self, visible):  # noqa: N802 (Qt API)
        self.window_ops.append(("setVisible", bool(visible)))
        super().setVisible(visible)


def _cat(db_key, name, unique_id):
    return SimpleNamespace(db_key=db_key, name=name, unique_id=unique_id)


def _session(cats=()):
    cats = list(cats)
    return SimpleNamespace(cats=cats, by_key={c.db_key: c for c in cats})


def _messages(caplog, level):
    return [r.getMessage() for r in caplog.records if r.name == LOG_NAME
            and r.levelno == level]


# ── fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def make_host(qapp):
    hosts = []

    def _make(session):
        host = RaiseHost(session)
        hosts.append(host)
        return host

    yield _make
    for host in hosts:
        host.hide()
        host.close()
        host.deleteLater()
    qapp.processEvents()


# ── happy path: select then engage, in that order ──────────────────────────
def test_a_raise_selects_the_cat_then_engages(make_host):
    host = make_host(_session([_cat(341, "L'Via", "0xabc")]))

    host.raise_reported_cat(bridge.RaiseRequest(key=341))

    # The order matters: the window comes up already showing the cat.
    assert host.events == [("select", 341), ("engage",)]
    assert host._tablectl.keys == [341]


def test_a_uid_only_raise_resolves_and_engages(make_host):
    host = make_host(_session([_cat(42, "Meeko", "0xDEADBEEF")]))

    host.raise_reported_cat(bridge.RaiseRequest(uid="0xdeadbeef"))

    assert host.events == [("select", 42), ("engage",)]


def test_a_unicode_name_raise_resolves_case_insensitively_and_engages(
        make_host):
    host = make_host(_session([_cat(7, "Mèeko 🐱", "0x7")]))

    host.raise_reported_cat(bridge.RaiseRequest(name="mèeko 🐱"))

    assert host.events == [("select", 7), ("engage",)]


def test_a_raise_engages_exactly_once(make_host):
    host = make_host(_session([_cat(341, "L'Via", "0xabc")]))

    host.raise_reported_cat(bridge.RaiseRequest(key=341))

    assert [hold for hold in host.events if hold[0] == "engage"] == [("engage",)]


# ── a raise still comes up when the cat cannot be resolved ─────────────────
def test_a_raise_for_an_unknown_key_still_engages(make_host):
    # The player clicked the in-game button, so the overlay must come up even
    # though the request names no cat in the loaded save.
    host = make_host(_session([_cat(341, "L'Via", "0xabc")]))

    host.raise_reported_cat(bridge.RaiseRequest(key=999999))

    assert host.events == [("engage",)]
    assert host._tablectl.keys == []


def test_a_raise_without_a_loaded_session_still_engages(make_host):
    host = make_host(None)

    host.raise_reported_cat(bridge.RaiseRequest(key=341))

    assert host.events == [("engage",)]
    assert host._tablectl.keys == []


def test_a_raise_with_an_ambiguous_name_engages_without_guessing(make_host):
    host = make_host(_session([_cat(1, "Murphy", "0x1"),
                               _cat(2, "Murphy", "0x2")]))

    host.raise_reported_cat(bridge.RaiseRequest(name="Murphy"))

    # No cat selected (the name is ambiguous), but the overlay still comes up.
    assert host.events == [("engage",)]
    assert host._tablectl.keys == []


# ── logging ────────────────────────────────────────────────────────────────
def test_a_raise_logs_the_selection_then_the_raise(make_host, caplog):
    host = make_host(_session([_cat(341, "L'Via", "0xabc")]))

    with caplog.at_level(logging.INFO, logger=LOG_NAME):
        host.raise_reported_cat(bridge.RaiseRequest(key=341))

    messages = _messages(caplog, logging.INFO)
    assert any("focusing cat key=341" in m for m in messages)
    assert any("raising overlay for raise request" in m for m in messages)


# ── the focus path stays silent (regression guard) ─────────────────────────
def test_a_focus_report_selects_without_engaging(make_host):
    host = make_host(_session([_cat(341, "L'Via", "0xabc")]))

    host.select_reported_cat(bridge.FocusRequest(key=341))

    assert host.events == [("select", 341)]
    assert host.window_ops == []
    assert host.isVisible() is False


def test_a_focus_report_never_raises_an_already_visible_window(make_host,
                                                               qapp):
    host = make_host(_session([_cat(341, "L'Via", "0xabc")]))
    host.show()
    qapp.processEvents()
    assert host.isVisible() is True
    host.window_ops.clear()

    host.select_reported_cat(bridge.FocusRequest(key=341))

    # No engage, no show/raise/activate/focus/setVisible on the visible window.
    assert host.events == [("select", 341)]
    assert host.window_ops == []
    assert host.isVisible() is True


def test_only_the_raise_path_engages(make_host):
    # Same host and same key, one focus then one raise: only the raise moves
    # the window, proving the two methods do not share an engage.
    host = make_host(_session([_cat(341, "L'Via", "0xabc")]))

    host.select_reported_cat(bridge.FocusRequest(key=341))
    host.raise_reported_cat(bridge.RaiseRequest(key=341))

    assert host.events == [("select", 341), ("select", 341), ("engage",)]
