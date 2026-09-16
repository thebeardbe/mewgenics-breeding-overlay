"""``PaletteWindow.select_reported_cat``: silent in-game selections.

The in-game companion mod reports the cat the player clicked; the overlay must
resolve that report against the save it has loaded and select the matching cat
*without* showing, raising, activating or focusing its window, and without
changing the window's visibility. That silence is the whole point: an in-game
click must not yank focus out of the game. The selected cat simply waits in
place for the next time the user summons the overlay.

Building a full ``PaletteWindow`` is heavy (save controller, watcher, asset
loader, threads), so these tests bind the real
``select_reported_cat`` / ``set_focus_key`` methods to a small ``QWidget``
stand-in carrying only the attributes they read (``_session`` and
``_tablectl``), the same convention as ``test_show_in_game.py``. The
stand-in also overrides the window operations (``show``/``hide``/``raise_``/
``activateWindow``/``setFocus``/``setVisible``) to record any call, so a
regression that re-added an engage would fail loudly.

Gap: a stand-in proves the method's contract, not that a live ``PaletteWindow``
build loads a session into the same attribute; that wiring is covered by the
save/reload tests. The app-level path that delivers the report is covered by
``test_app_bootstrap.py``.
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
from mewgenics_overlay.ui import selectecho  # noqa: E402

LOG_NAME = "mewgenics_overlay.ui"


# ── fakes / helpers ────────────────────────────────────────────────────────
class _RecordingTable:
    """Records every programmatic focus, standing in for TableCoordinator."""

    def __init__(self):
        self.keys = []

    def set_focus_key(self, db_key):
        self.keys.append(db_key)


class ReportedCatHost(QWidget):
    """Real ``QWidget`` carrying the real ``PaletteWindow`` methods under test.

    Every window operation is overridden to record the call *and* delegate, so
    a hidden window would become visible if the method ever showed it, and the
    recorder catches show/raise/activate/focus/setVisible even when the state
    would stay the same (raising an already-visible window).
    """

    select_reported_cat = palette.PaletteWindow.select_reported_cat
    set_focus_key = palette.PaletteWindow.set_focus_key
    # The real select_reported_cat delegates to this shared resolution helper.
    _apply_reported_selection = palette.PaletteWindow._apply_reported_selection

    def __init__(self, session):
        super().__init__()
        self._session = session
        self._tablectl = _RecordingTable()
        # The real window always carries one (PaletteWindow.__init__); the
        # echo filter is real here so select_reported_cat runs unmodified.
        self._select_echo = selectecho.SelectEchoFilter()
        self.window_ops = []

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


def _records(caplog, level):
    return [r for r in caplog.records if r.name == LOG_NAME
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
        host = ReportedCatHost(session)
        hosts.append(host)
        return host

    yield _make
    for host in hosts:
        host.hide()
        host.close()
        host.deleteLater()
    qapp.processEvents()


# ── happy path: the report selects the cat ─────────────────────────────────
def test_a_reported_key_selects_that_cat(make_host):
    host = make_host(_session([_cat(341, "L'Via", "0xabc")]))

    host.select_reported_cat(bridge.FocusRequest(key=341))

    assert host._tablectl.keys == [341]


def test_a_key_zero_is_a_valid_cat_key(make_host):
    # 0 is a real game key, not a "missing" sentinel; the boundary must resolve.
    host = make_host(_session([_cat(0, "Zero", "0x0")]))

    host.select_reported_cat(bridge.FocusRequest(key=0))

    assert host._tablectl.keys == [0]


def test_a_uid_only_report_also_resolves(make_host):
    host = make_host(_session([_cat(42, "Meeko", "0xDEADBEEF")]))

    host.select_reported_cat(bridge.FocusRequest(uid="0xdeadbeef"))

    assert host._tablectl.keys == [42]


def test_a_unicode_name_report_resolves_case_insensitively(make_host):
    host = make_host(_session([_cat(7, "Mèeko 🐱", "0x7")]))

    host.select_reported_cat(bridge.FocusRequest(name="mèeko 🐱"))

    assert host._tablectl.keys == [7]


# ── silence: no show / raise / activate / focus, visibility unchanged ──────
def test_a_report_does_not_touch_a_hidden_window(make_host):
    host = make_host(_session([_cat(341, "L'Via", "0xabc")]))
    assert host.isVisible() is False

    host.select_reported_cat(bridge.FocusRequest(key=341))

    assert host.isVisible() is False
    assert host.window_ops == []
    assert host._tablectl.keys == [341]      # the selection still happened


def test_a_report_does_not_touch_an_already_visible_window(make_host, qapp):
    host = make_host(_session([_cat(341, "L'Via", "0xabc")]))
    host.show()
    qapp.processEvents()
    assert host.isVisible() is True
    host.window_ops.clear()

    host.select_reported_cat(bridge.FocusRequest(key=341))

    assert host.isVisible() is True
    assert host.window_ops == []
    assert host._tablectl.keys == [341]


def test_an_unknown_key_does_not_touch_the_window_either(make_host):
    # The failure path must be just as silent as the success path.
    host = make_host(_session([_cat(341, "L'Via", "0xabc")]))

    host.select_reported_cat(bridge.FocusRequest(key=999999))

    assert host.isVisible() is False
    assert host.window_ops == []
    assert host._tablectl.keys == []


# ── negative paths: a report that resolves to nothing changes nothing ──────
def test_an_unknown_key_logs_and_changes_nothing(make_host, caplog):
    host = make_host(_session([_cat(341, "L'Via", "0xabc")]))

    with caplog.at_level(logging.INFO, logger=LOG_NAME):
        host.select_reported_cat(bridge.FocusRequest(key=999999))

    assert host._tablectl.keys == []
    assert host.window_ops == []
    infos = _records(caplog, logging.INFO)
    assert len(infos) == 1
    assert "no cat" in infos[0].getMessage()


def test_an_ambiguous_name_is_refused_and_changes_nothing(make_host, caplog):
    # Two cats share a name: the overlay must never guess which one was clicked.
    host = make_host(_session([_cat(1, "Murphy", "0x1"),
                               _cat(2, "Murphy", "0x2")]))

    with caplog.at_level(logging.DEBUG):
        host.select_reported_cat(bridge.FocusRequest(name="Murphy"))

    assert host._tablectl.keys == []
    assert host.window_ops == []
    # The resolver refuses the ambiguous name (WARNING on the bridge logger)
    # and the palette reports that nothing was selected (INFO).
    assert any(r.name == "mewgenics_overlay.bridge"
               and r.levelno == logging.WARNING
               and "ambiguous" in r.getMessage() for r in caplog.records)
    assert len(_records(caplog, logging.INFO)) == 1


def test_an_empty_session_logs_and_selects_nothing(make_host, caplog):
    host = make_host(_session())

    with caplog.at_level(logging.INFO, logger=LOG_NAME):
        host.select_reported_cat(bridge.FocusRequest(key=341))

    assert host._tablectl.keys == []
    assert host.window_ops == []
    assert len(_records(caplog, logging.INFO)) == 1


def test_no_loaded_session_logs_and_changes_nothing(make_host, caplog):
    host = make_host(None)

    with caplog.at_level(logging.INFO, logger=LOG_NAME):
        host.select_reported_cat(bridge.FocusRequest(key=341))

    assert host._tablectl.keys == []
    assert host.window_ops == []
    assert len(_records(caplog, logging.INFO)) == 1


# ── resolution uses the palette's own (live) session ───────────────────────
def test_resolution_reads_the_palettes_own_session(make_host):
    # Swapping the loaded session must change what resolves: the method reads
    # the palette's session at call time, not a value captured elsewhere.
    host = make_host(_session([_cat(1, "First", "0x1")]))
    host.select_reported_cat(bridge.FocusRequest(key=1))

    host._session = _session([_cat(2, "Second", "0x2")])
    host.select_reported_cat(bridge.FocusRequest(key=1))   # gone from the save
    host.select_reported_cat(bridge.FocusRequest(key=2))

    assert host._tablectl.keys == [1, 2]
