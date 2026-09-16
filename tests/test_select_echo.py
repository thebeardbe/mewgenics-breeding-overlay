"""Selection-echo suppression: ``ui/selectecho.py`` + its palette wiring.

When the overlay asks the game to show a cat (the "Show in game" button or
Ctrl+G), the game changes its selection and the companion mod reports that
change straight back as a ``focus`` message. Acting on it re-selects whatever
was sent, which for a partner-row send silently re-roots the analysis. The
overlay therefore remembers the keys it recently requested and drops a focus
report whose key it just asked for, inside ``ECHO_WINDOW_SECONDS`` (0.75 s,
the measured round trip is under 500 ms). A ``raise`` is an explicit in-game
click and must never be suppressed: it still selects and engages.

The clock is injected by replacing the ``time`` binding ``selectecho`` reads,
so every test is deterministic and no test sleeps. The palette-level tests
bind the real ``PaletteWindow`` methods to a small ``QWidget`` stand-in
carrying only the attributes they read, the same convention as
``test_reported_cat.py`` / ``test_raise_reported_cat.py``.

Gaps: the echo filter matches on the report's ``key`` only, so a third-party
mod that reports a selection with a bare ``uid``/``name`` is treated as a real
click (the bundled mod always sends ``key``; covered below as documented
behaviour). A stand-in proves the method contracts, not that a live
``PaletteWindow`` build wires the bridge's focus signal to these methods; that
routing is covered by ``test_app_bootstrap.py`` / ``test_app_bridge_raise.py``.
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
WINDOW = selectecho.ECHO_WINDOW_SECONDS


# ── deterministic clock ────────────────────────────────────────────────────
class _FakeClock:
    """Stand-in for the ``time`` module ``selectecho`` reads.

    ``selectecho`` calls ``time.monotonic()``; the fixture replaces the
    module's ``time`` binding (never the stdlib function), so the injection is
    local to the filter under test and no other code sees a fake clock.
    """

    def __init__(self, start: float = 10_000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Inject the fake clock into ``selectecho`` for *every* test here."""
    fake = _FakeClock()
    monkeypatch.setattr(selectecho, "time", fake)
    return fake


# ── filter unit surface: no Qt needed ──────────────────────────────────────
def test_the_default_window_is_the_measured_round_trip_margin():
    # 0.75 s: the send -> report round trip is under 500 ms, so this covers it
    # with margin while staying short enough that a real click right after a
    # command is still honoured. A change here is a deliberate UX change.
    assert selectecho.ECHO_WINDOW_SECONDS == 0.75


def test_a_requested_key_is_an_echo_immediately(clock):
    flt = selectecho.SelectEchoFilter()
    flt.note_request(5)
    assert flt.is_echo(5) is True


def test_an_unrequested_key_is_never_an_echo(clock):
    flt = selectecho.SelectEchoFilter()
    assert flt.is_echo(5) is False
    flt.note_request(6)
    assert flt.is_echo(5) is False


def test_key_zero_is_remembered_like_any_other_key(clock):
    # 0 is a real game key, not a "missing" sentinel (see test_reported_cat).
    flt = selectecho.SelectEchoFilter()
    flt.note_request(0)
    assert flt.is_echo(0) is True


def test_the_echo_is_suppressed_at_exactly_the_window(clock):
    flt = selectecho.SelectEchoFilter()
    flt.note_request(1)
    clock.advance(WINDOW)            # not *older* than the window yet
    assert flt.is_echo(1) is True


def test_the_echo_expires_once_the_window_passes(clock):
    flt = selectecho.SelectEchoFilter()
    flt.note_request(1)
    clock.advance(WINDOW + 0.001)
    assert flt.is_echo(1) is False


def test_is_echo_prunes_the_expired_entry(clock):
    flt = selectecho.SelectEchoFilter()
    flt.note_request(1)
    clock.advance(WINDOW + 0.001)
    assert flt.is_echo(1) is False
    assert flt._requested == {}


def test_note_request_prunes_the_expired_entry(clock):
    flt = selectecho.SelectEchoFilter()
    flt.note_request(1)
    clock.advance(WINDOW + 0.001)
    flt.note_request(2)
    assert set(flt._requested) == {2}
    assert flt.is_echo(1) is False


def test_re_requesting_refreshes_the_window(clock):
    flt = selectecho.SelectEchoFilter()
    flt.note_request(1)
    clock.advance(WINDOW - 0.1)
    flt.note_request(1)              # sent again: the clock restarts
    clock.advance(WINDOW - 0.1)
    assert flt.is_echo(1) is True


def test_two_requests_in_one_window_are_both_remembered(clock):
    # Regression: the map must hold *every* key requested in the window, not
    # just the latest, or the first command's echo slips through.
    flt = selectecho.SelectEchoFilter()
    flt.note_request(10)
    clock.advance(0.2)
    flt.note_request(20)
    assert flt.is_echo(10) is True
    assert flt.is_echo(20) is True


def test_the_first_of_two_requests_expires_before_the_second(clock):
    flt = selectecho.SelectEchoFilter()
    flt.note_request(10)
    clock.advance(0.5)
    flt.note_request(20)
    clock.advance(WINDOW - 0.4)      # key 10: 0.85 s old, key 20: 0.35 s old
    assert flt.is_echo(10) is False
    assert flt.is_echo(20) is True


def test_a_burst_of_requests_is_cleared_once_the_window_passes(clock):
    flt = selectecho.SelectEchoFilter()
    for key in range(1000):
        flt.note_request(key)
    assert len(flt._requested) == 1000     # all inside one instant
    clock.advance(WINDOW + 0.001)
    assert flt.is_echo(999) is False
    assert flt._requested == {}


def test_the_map_stays_bounded_while_selects_keep_coming(clock):
    # 400 sends spread over 20 s; only the sends inside one window survive, so
    # the map is bounded by the window, not by the number of commands sent.
    step = 0.05
    bound = int(WINDOW / step) + 2
    flt = selectecho.SelectEchoFilter()
    peak = 0
    for key in range(400):
        flt.note_request(key)
        peak = max(peak, len(flt._requested))
        clock.advance(step)
    assert peak <= bound
    assert len(flt._requested) <= bound


def test_a_custom_window_is_honoured(clock):
    flt = selectecho.SelectEchoFilter(window=2.0)
    flt.note_request(1)
    clock.advance(1.9)
    assert flt.is_echo(1) is True
    clock.advance(0.2)
    assert flt.is_echo(1) is False


# ── palette wiring: host fakes ─────────────────────────────────────────────
class _RecordingTable:
    """Records every programmatic focus into the host's event log."""

    def __init__(self, events):
        self.keys = []
        self._events = events

    def set_focus_key(self, db_key):
        self.keys.append(db_key)
        self._events.append(("select", db_key))


class _FakeBridge:
    """Records every select and returns a scripted delivery count."""

    def __init__(self, delivered=1):
        self.delivered = delivered
        self.keys = []

    def send_select(self, key):
        self.keys.append(key)
        return self.delivered


class EchoHost(QWidget):
    """Real ``QWidget`` carrying the real ``PaletteWindow`` methods under test.

    ``_engage`` is recorded instead of run; every other window operation is
    recorded *and* delegated, so a regression that showed/raised/activated or
    focused the window is caught even when the state would not change.
    """

    select_reported_cat = palette.PaletteWindow.select_reported_cat
    _apply_reported_selection = palette.PaletteWindow._apply_reported_selection
    raise_reported_cat = palette.PaletteWindow.raise_reported_cat
    show_in_game = palette.PaletteWindow.show_in_game
    show_focused_in_game = palette.PaletteWindow.show_focused_in_game
    set_focus_key = palette.PaletteWindow.set_focus_key
    _cat_name = palette.PaletteWindow._cat_name

    def __init__(self, session, bridge_ctl=None):
        super().__init__()
        self._session = session
        self._bridge = bridge_ctl
        self._focus = None
        # The real window creates one of these in __init__; the tests must
        # exercise that same object, never a stub.
        self._select_echo = selectecho.SelectEchoFilter()
        self.events = []
        self._tablectl = _RecordingTable(self.events)
        self.window_ops = []
        self.statuses = []
        self._set_status = self.statuses.append

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


def _debug_messages(caplog):
    return [r.getMessage() for r in caplog.records if r.name == LOG_NAME
            and r.levelno == logging.DEBUG]


def _messages(caplog, level):
    return [r.getMessage() for r in caplog.records if r.name == LOG_NAME
            and r.levelno == level]


# ── palette fixtures ───────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def make_host(qapp):
    hosts = []

    def _make(session, bridge_ctl=None):
        host = EchoHost(session, bridge_ctl)
        hosts.append(host)
        return host

    yield _make
    for host in hosts:
        host.hide()
        host.close()
        host.deleteLater()
    qapp.processEvents()


@pytest.fixture
def cat_session():
    return _session([_cat(1, "Meeko", "0x1"), _cat(2, "L'Via", "0x2"),
                     _cat(341, "Baby Jane", "0x155")])


# ── the outbound path records the request ──────────────────────────────────
def test_show_in_game_remembers_the_key_it_sent(make_host, cat_session):
    ctl = _FakeBridge(delivered=1)
    host = make_host(cat_session, ctl)

    assert host.show_in_game(341) is True

    assert ctl.keys == [341]
    assert host._select_echo.is_echo(341) is True


def test_the_focused_cat_path_also_records_the_echo(make_host, cat_session):
    # Ctrl+G / the card button delegate to show_in_game, so they record too.
    ctl = _FakeBridge(delivered=1)
    host = make_host(cat_session, ctl)
    host._focus = cat_session.by_key[1]

    assert host.show_focused_in_game() is True

    assert host._select_echo.is_echo(1) is True


def test_a_send_that_reached_nobody_is_not_remembered(make_host, cat_session):
    # Nothing arrived, so a real click for that key right after must not be
    # swallowed by the filter.
    ctl = _FakeBridge(delivered=0)
    host = make_host(cat_session, ctl)

    assert host.show_in_game(341) is False
    assert host._select_echo.is_echo(341) is False

    host.select_reported_cat(bridge.FocusRequest(key=341))

    assert host._tablectl.keys == [341]


# ── the echo of our own select is dropped ──────────────────────────────────
def test_the_echo_of_our_own_select_is_ignored(make_host, cat_session, caplog):
    ctl = _FakeBridge(delivered=1)
    host = make_host(cat_session, ctl)
    host.show_in_game(341)
    host.statuses.clear()

    with caplog.at_level(logging.DEBUG, logger=LOG_NAME):
        host.select_reported_cat(bridge.FocusRequest(key=341))

    assert host._tablectl.keys == []          # no selection change
    assert host.window_ops == []              # no show/raise/activate/focus
    assert host.isVisible() is False
    assert host.statuses == []                # no user-facing status line


def test_an_ignored_echo_is_logged_at_debug(make_host, cat_session, caplog):
    ctl = _FakeBridge(delivered=1)
    host = make_host(cat_session, ctl)
    host.show_in_game(341)
    host.statuses.clear()

    with caplog.at_level(logging.DEBUG, logger=LOG_NAME):
        host.select_reported_cat(bridge.FocusRequest(key=341))

    messages = _debug_messages(caplog)
    assert len(messages) == 1
    assert "echo" in messages[0]
    assert "341" in messages[0]
    # The suppression is not user-facing: no INFO/WARNING status line.
    assert _messages(caplog, logging.INFO) == []
    assert _messages(caplog, logging.WARNING) == []


def test_an_ignored_echo_does_not_disturb_a_visible_window(make_host,
                                                           cat_session, qapp):
    ctl = _FakeBridge(delivered=1)
    host = make_host(cat_session, ctl)
    host.show_in_game(341)
    host.show()
    qapp.processEvents()
    host.window_ops.clear()

    host.select_reported_cat(bridge.FocusRequest(key=341))

    assert host.isVisible() is True
    assert host.window_ops == []
    assert host._tablectl.keys == []


def test_a_key_zero_select_echo_is_ignored(make_host, cat_session):
    ctl = _FakeBridge(delivered=1)
    host = make_host(cat_session, ctl)
    cat_session.by_key[0] = _cat(0, "Zero", "0x0")
    host._session = _session([cat_session.by_key[0], cat_session.by_key[341]])

    host.show_in_game(0)
    host.select_reported_cat(bridge.FocusRequest(key=0))

    assert host._tablectl.keys == []


def test_the_echo_check_runs_before_resolution(make_host, cat_session):
    # A stale report for a key we sent must be dropped even when the key is no
    # longer in the loaded save, and it must not post a "no cat" status line.
    ctl = _FakeBridge(delivered=1)
    host = make_host(cat_session, ctl)
    host.show_in_game(341)
    host.statuses.clear()
    host._session = _session([_cat(1, "Meeko", "0x1")])

    host.select_reported_cat(bridge.FocusRequest(key=341))

    assert host._tablectl.keys == []
    assert host.statuses == []


# ── a report we did not ask for is still honoured ──────────────────────────
def test_a_report_we_did_not_ask_for_is_still_honoured(make_host,
                                                       cat_session):
    host = make_host(cat_session)

    host.select_reported_cat(bridge.FocusRequest(key=341))

    assert host._tablectl.keys == [341]


def test_a_real_click_for_another_cat_right_after_a_command(make_host,
                                                            cat_session):
    ctl = _FakeBridge(delivered=1)
    host = make_host(cat_session, ctl)
    host.show_in_game(341)

    host.select_reported_cat(bridge.FocusRequest(key=2))

    assert host._tablectl.keys == [2]


def test_a_report_after_the_window_passed_is_honoured_again(make_host,
                                                            cat_session,
                                                            clock):
    ctl = _FakeBridge(delivered=1)
    host = make_host(cat_session, ctl)
    host.show_in_game(341)

    clock.advance(WINDOW + 0.001)
    host.select_reported_cat(bridge.FocusRequest(key=341))

    assert host._tablectl.keys == [341]


def test_an_expired_echo_is_also_honoured_on_the_raise_path(make_host,
                                                            cat_session,
                                                            clock):
    ctl = _FakeBridge(delivered=1)
    host = make_host(cat_session, ctl)
    host.show_in_game(341)

    clock.advance(WINDOW + 0.001)
    host.raise_reported_cat(bridge.RaiseRequest(key=341))

    assert host.events == [("select", 341), ("engage",)]


# ── two commands in quick succession ───────────────────────────────────────
def test_two_quick_commands_are_both_suppressed_within_the_window(
        make_host, cat_session, clock):
    ctl = _FakeBridge(delivered=1)
    host = make_host(cat_session, ctl)
    host.show_in_game(1)
    clock.advance(0.2)
    host.show_in_game(2)
    # Key 1 is 0.7 s old and key 2 is 0.5 s old: both are still inside the
    # window, so neither command's echo may be mistaken for a click.
    clock.advance(0.5)

    host.select_reported_cat(bridge.FocusRequest(key=1))

    assert host._tablectl.keys == []          # the late echo is dropped

    host.select_reported_cat(bridge.FocusRequest(key=2))

    assert host._tablectl.keys == []          # and so is the second echo


def test_a_real_click_between_two_quick_commands_is_honoured(make_host,
                                                             cat_session,
                                                             clock):
    ctl = _FakeBridge(delivered=1)
    host = make_host(cat_session, ctl)
    host.show_in_game(1)
    clock.advance(0.2)
    host.show_in_game(2)
    clock.advance(WINDOW - 0.15)

    host.select_reported_cat(bridge.FocusRequest(key=341))

    assert host._tablectl.keys == [341]


# ── the raise path is never suppressed ─────────────────────────────────────
def test_a_raise_for_a_recently_requested_key_selects_and_engages(make_host,
                                                                   cat_session):
    ctl = _FakeBridge(delivered=1)
    host = make_host(cat_session, ctl)
    host.show_in_game(341)

    host.raise_reported_cat(bridge.RaiseRequest(key=341))

    assert host.events == [("select", 341), ("engage",)]
    assert host._tablectl.keys == [341]


def test_a_raise_immediately_after_a_command_still_engages(make_host,
                                                           cat_session,
                                                           clock):
    ctl = _FakeBridge(delivered=1)
    host = make_host(cat_session, ctl)
    host.show_in_game(1)

    # Essentially no gap between the outbound select and the raise: the echo
    # window is wide open, yet a raise is a player action and must go through.
    host.raise_reported_cat(bridge.RaiseRequest(key=1))

    assert host.events == [("select", 1), ("engage",)]
    assert host.isVisible() is False           # _engage is the recorder


# ── documented limit: matching is key-based ────────────────────────────────
def test_a_uid_only_report_is_not_matched_by_the_key_echo(make_host,
                                                          cat_session):
    # The bundled mod always sends key; the filter can only match a report that
    # carries one. A uid-only report is therefore treated as a real click.
    ctl = _FakeBridge(delivered=1)
    host = make_host(cat_session, ctl)
    host.show_in_game(341)

    host.select_reported_cat(bridge.FocusRequest(uid="0x155"))

    assert host._tablectl.keys == [341]
