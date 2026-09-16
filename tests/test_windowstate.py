"""WindowController: OS-level window behaviour extracted from PaletteWindow.

``mewgenics_overlay.ui.windowstate.WindowController`` owns framing, geometry
restore/save, the user's persisted keep-on-top choice (native on Windows),
click-through, summon/hide and the Qt event hooks the palette calls from its
thin ``showEvent`` / ``hideEvent`` / ``changeEvent`` overrides.

These tests drive it with an offscreen ``QWidget`` and a recording stand-in
for the header bar, so no ``PaletteWindow`` (and therefore no save, watcher or
timers) is ever constructed. The native Windows topmost path cannot run on
Linux, so the platform is stubbed as the source guards it
(``windowstate.sys.platform``); the tests then assert the raise path activates
the window first, asserts topmost a bounded number of times and stops, and
makes no topmost call at all when keep-on-top is off. One test proves the raw
native call is swallowed *and logged* rather than raised when it really runs.
"""

from __future__ import annotations

import inspect
import logging
import os
from types import SimpleNamespace

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QRect, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from mewgenics_overlay.ui import raisewindow  # noqa: E402
from mewgenics_overlay.ui import windowstate as ws  # noqa: E402
from mewgenics_overlay.ui.windowstate import (  # noqa: E402
    CLICK_THROUGH_DEFAULT,
    WindowController,
)


# ── fixtures / helpers ─────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _stub_hyprland_raise(monkeypatch):
    """``engage()`` also nudges the compositor through ``raisewindow``.

    These tests are about the Qt-level behaviour, so the fallback is stubbed
    out: otherwise a Hyprland dev machine would shell out to the real
    ``hyprctl clients -j`` (bounded only by a 2 s timeout) and read the live
    session during the suite. ``tests/test_raisewindow.py`` covers the
    fallback itself and pins the call from ``engage()``.
    """
    monkeypatch.setattr(raisewindow, "focus_window", lambda *a, **k: False)


class FakeChrome:
    """Records the state syncs WindowController pushes to the header bar."""

    def __init__(self):
        self.click_through = []

    def set_click_through(self, value):
        self.click_through.append(bool(value))


@pytest.fixture
def make_ctl(qapp):
    """Build (controller, window, chrome, settings, saves) triples.

    Windows are torn down after each test; the controller is parented to its
    window, so deleting the window takes it with it.
    """
    windows = []

    def _make(settings=None):
        window = QWidget()
        windows.append(window)
        chrome = FakeChrome()
        settings = settings if settings is not None else {}
        saves = []
        ctl = WindowController(window, chrome, settings,
                               lambda: saves.append(dict(settings)))
        return SimpleNamespace(ctl=ctl, window=window, chrome=chrome,
                               settings=settings, saves=saves)

    yield _make

    for window in windows:
        window.hide()
        window.close()
        window.deleteLater()
    qapp.processEvents()


def _on_screen_rect(margin=10, width=400, height=300) -> QRect:
    """A rect guaranteed to intersect the (offscreen) primary screen."""
    geo = QGuiApplication.screens()[0].availableGeometry()
    return QRect(geo.x() + margin, geo.y() + margin,
                 min(width, geo.width()), min(height, geo.height()))


# ── 1. framing flags ───────────────────────────────────────────────────────
def test_configure_frame_is_frameless_and_topmost_away_from_hyprland(
        make_ctl, monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    h = make_ctl()

    h.ctl.configure_frame()

    flags = h.window.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint


def test_configure_frame_is_frameless_without_the_qt_topmost_flag_on_hyprland(
        make_ctl, monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "1")
    h = make_ctl()

    h.ctl.configure_frame()

    flags = h.window.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert not (flags & Qt.WindowType.WindowStaysOnTopHint)


def test_configure_frame_never_sets_the_qt_topmost_flag_on_windows(
        make_ctl, monkeypatch):
    # On Windows the Qt flag re-creates the HWND (the "lost overlay" bug), so
    # topmost is done natively instead.
    monkeypatch.setattr(ws.sys, "platform", "win32")
    h = make_ctl()

    h.ctl.configure_frame()

    flags = h.window.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert not (flags & Qt.WindowType.WindowStaysOnTopHint)


# ── 2. geometry round-trip ─────────────────────────────────────────────────
def test_geometry_save_then_restore_round_trips_through_settings(make_ctl):
    rect = _on_screen_rect()
    first = make_ctl()
    first.window.setGeometry(rect)

    first.ctl.save_geometry()

    assert first.settings["window_rect"] == [
        rect.x(), rect.y(), rect.width(), rect.height()]
    assert len(first.saves) == 1

    # A fresh window with the same settings comes back at the same place.
    second = make_ctl(settings=first.settings)
    second.ctl.restore_geometry()

    assert second.window.geometry() == rect


def test_save_geometry_calls_the_persist_callback_once_per_save(make_ctl):
    h = make_ctl()
    h.window.setGeometry(QRect(11, 22, 333, 222))

    h.ctl.save_geometry()
    h.ctl.on_hide()

    assert len(h.saves) == 2
    assert h.settings["window_rect"] == [11, 22, 333, 222]


@pytest.mark.parametrize("rect", [
    None,
    {},
    [1, 2, 3],
    [1, 2, 3, 4, 5],
    "0,0,800,600",
    42,
])
def test_restore_geometry_ignores_a_malformed_stored_rect(make_ctl, rect):
    h = make_ctl(settings={"window_rect": rect})
    before = h.window.geometry()

    h.ctl.restore_geometry()

    assert h.window.geometry() == before


def test_restore_geometry_ignores_a_rect_off_every_screen(make_ctl):
    h = make_ctl(settings={"window_rect": [-100000, -100000, 200, 150]})
    before = h.window.geometry()

    h.ctl.restore_geometry()

    assert h.window.geometry() == before


def test_restore_geometry_is_a_noop_without_a_stored_rect(make_ctl):
    h = make_ctl()               # settings carries no window_rect
    before = h.window.geometry()

    h.ctl.restore_geometry()

    assert h.window.geometry() == before
    assert "window_rect" not in h.settings


# ── 3. click-through ───────────────────────────────────────────────────────
def test_set_click_through_sets_the_attribute_and_syncs_the_chrome(make_ctl):
    h = make_ctl()

    h.ctl.set_click_through(True)

    assert h.ctl.click_through is True
    assert h.window.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True
    assert h.chrome.click_through == [True]

    h.ctl.set_click_through(False)

    assert h.ctl.click_through is False
    assert h.window.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False
    assert h.chrome.click_through == [True, False]


def test_click_through_defaults_to_interactive(make_ctl):
    h = make_ctl()

    assert h.ctl.click_through is CLICK_THROUGH_DEFAULT
    assert h.window.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False
    assert h.chrome.click_through == []


def test_header_click_delegates_to_set_click_through(make_ctl):
    h = make_ctl()

    h.ctl.on_click_through_clicked(True)
    h.ctl.on_click_through_clicked(False)

    assert h.chrome.click_through == [True, False]


def test_set_click_through_accepts_truthy_and_falsy_values(make_ctl):
    h = make_ctl()

    h.ctl.set_click_through(1)
    assert h.ctl.click_through is True

    h.ctl.set_click_through(0)
    assert h.ctl.click_through is False


# ── 4. keep-on-top (persisted, tray-controlled) ────────────────────────────
def test_keep_on_top_defaults_on(make_ctl):
    h = make_ctl()                 # settings carries no keep_on_top yet

    assert h.ctl.keep_on_top is True
    # The default is not written to the settings dict until the user toggles.
    assert "keep_on_top" not in h.settings


def test_keep_on_top_reads_a_stored_off_choice(make_ctl):
    h = make_ctl(settings={"keep_on_top": False})

    assert h.ctl.keep_on_top is False


def test_the_pin_toggle_api_is_gone(make_ctl):
    # Regression: the old unconditional pin state was replaced by the
    # persisted keep-on-top choice; the controller no longer carries pin
    # state or a toggle/helper for it.
    h = make_ctl()

    for name in ("pinned", "toggle_pin", "set_pinned"):
        assert not hasattr(h.ctl, name)
    assert not hasattr(h.chrome, "set_pinned")
    # ``_set_topmost_win32`` now takes the on/off choice it must apply (a
    # bound method's signature drops ``self``).
    assert list(inspect.signature(
        h.ctl._set_topmost_win32).parameters) == ["on"]


def test_configure_frame_honours_the_stored_choice_generically(
        make_ctl, monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)

    on = make_ctl(settings={"keep_on_top": True})
    on.ctl.configure_frame()
    assert on.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

    off = make_ctl(settings={"keep_on_top": False})
    off.ctl.configure_frame()
    assert not (off.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)


def test_set_keep_on_top_persists_and_applies_at_once(make_ctl, monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    h = make_ctl()
    h.ctl.configure_frame()
    assert h.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

    h.ctl.set_keep_on_top(False)

    assert h.ctl.keep_on_top is False
    assert h.settings["keep_on_top"] is False
    assert h.saves[-1]["keep_on_top"] is False
    assert not (h.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    h.ctl.set_keep_on_top(True)

    assert h.ctl.keep_on_top is True
    assert h.settings["keep_on_top"] is True
    assert h.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


def test_keep_on_top_survives_a_reload(make_ctl, monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    first = make_ctl()
    first.ctl.set_keep_on_top(False)

    # A fresh controller built from the persisted settings (an app reload).
    reloaded = make_ctl(settings=first.settings)
    reloaded.ctl.configure_frame()

    assert reloaded.ctl.keep_on_top is False
    assert not (reloaded.window.windowFlags()
                & Qt.WindowType.WindowStaysOnTopHint)


def test_set_keep_on_top_accepts_truthy_and_falsy_values(make_ctl,
                                                        monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    h = make_ctl()

    h.ctl.set_keep_on_top(0)
    assert h.ctl.keep_on_top is False
    h.ctl.set_keep_on_top(1)
    assert h.ctl.keep_on_top is True


def test_native_topmost_setup_failure_is_logged_and_swallowed(
        make_ctl, monkeypatch, caplog):
    """The native SetWindowPos path is unavailable under Linux.

    It must degrade to a logged warning, never an exception that would take
    the overlay down for a cosmetic always-on-top.
    """
    monkeypatch.setattr(ws.sys, "platform", "win32")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    h = make_ctl(settings={"keep_on_top": True})
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="mewgenics_overlay.ui"):
        h.ctl.on_show()                # real native path; must not raise

    assert any("native always-on-top setup failed" in r.message
               for r in caplog.records)


def test_native_topmost_clear_failure_is_logged_and_swallowed(
        make_ctl, monkeypatch, caplog):
    monkeypatch.setattr(ws.sys, "platform", "win32")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    h = make_ctl(settings={"keep_on_top": False})
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="mewgenics_overlay.ui"):
        h.ctl.set_keep_on_top(False)   # real native clear; must not raise

    assert any("native always-on-top clear failed" in r.message
               for r in caplog.records)


def test_hyprland_leaves_stacking_alone_and_logs(make_ctl, monkeypatch, caplog):
    # Hyprland owns stacking via compositor rules, so the Qt flag is never
    # touched there; the requested value is only reported in the log.
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "1")
    h = make_ctl()
    h.ctl.configure_frame()
    before = h.window.windowFlags()
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="mewgenics_overlay.ui"):
        h.ctl.set_keep_on_top(False)
        h.ctl.on_show()

    assert h.window.windowFlags() == before
    assert not (h.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    assert h.settings["keep_on_top"] is False
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "Hyprland manages window stacking" in joined
    assert "requested=False" in joined


# ── 5. summon / hide ───────────────────────────────────────────────────────
def test_engage_shows_and_makes_the_window_interactive(make_ctl, qapp):
    h = make_ctl()
    h.ctl.set_click_through(True)

    h.ctl.engage()
    qapp.processEvents()

    assert h.window.isVisible() is True
    assert h.ctl.click_through is False
    assert h.chrome.click_through[-1] is False


def test_toggle_activate_cycles_hidden_passive_active(make_ctl, qapp):
    h = make_ctl()
    assert h.window.isVisible() is False

    h.ctl.toggle_activate()            # hidden -> engage
    qapp.processEvents()
    assert h.window.isVisible() is True
    assert h.ctl.click_through is False

    h.ctl.set_click_through(True)      # passive (click-through) -> engage
    h.ctl.toggle_activate()
    qapp.processEvents()
    assert h.window.isVisible() is True
    assert h.ctl.click_through is False

    h.ctl.toggle_activate()            # active -> hide
    qapp.processEvents()
    assert h.window.isVisible() is False


# ── 5b. Windows raise: bounded topmost settle after activation ─────────────
def test_engage_asserts_topmost_after_activation_not_before(make_ctl,
                                                            monkeypatch):
    """No topmost assert happens until the window is foreground.

    ``show()`` runs the host's ``showEvent`` synchronously, which calls
    ``on_show``; that is where the pre-fix code re-asserted topmost while the
    game still held the foreground. The order log proves the native assert now
    lands after ``activateWindow`` / ``setFocus`` and never during ``show``.
    """
    monkeypatch.setattr(ws.sys, "platform", "win32")
    h = make_ctl(settings={"keep_on_top": True})
    events = []
    real_show = h.window.show

    def show_with_host_showevent():
        events.append("show")
        h.ctl.on_show()                  # the host's showEvent -> on_show
        real_show()

    monkeypatch.setattr(h.window, "show", show_with_host_showevent)
    monkeypatch.setattr(h.window, "raise_", lambda: events.append("raise"))
    monkeypatch.setattr(h.window, "activateWindow",
                        lambda: events.append("activate"))
    monkeypatch.setattr(h.window, "setFocus",
                        lambda: events.append("focus"))
    monkeypatch.setattr(h.ctl, "_set_topmost_win32",
                        lambda on: events.append(f"topmost:{on}"))

    h.ctl.engage()
    h.ctl._stop_topmost_timer()          # do not leave the burst running

    assert events == ["show", "raise", "activate", "focus", "topmost:True"]


def test_engage_topmost_reasserts_are_bounded_and_stop(make_ctl, monkeypatch):
    """One assert after activation, then a fixed small burst, then done."""
    monkeypatch.setattr(ws.sys, "platform", "win32")
    h = make_ctl(settings={"keep_on_top": True})
    calls = []
    monkeypatch.setattr(h.ctl, "_set_topmost_win32", calls.append)

    h.ctl.engage()

    timer = h.ctl._topmost_timer
    assert calls == [True]               # the one assert after activation
    assert timer is not None and timer.isActive()
    assert timer.interval() == ws.WIN32_TOPMOST_REASSERT_INTERVAL_MS
    assert h.ctl._topmost_reasserts_left == ws.WIN32_TOPMOST_REASSERTS

    # Drive the timeout slot exactly as the running timer would.
    for _ in range(ws.WIN32_TOPMOST_REASSERTS):
        h.ctl._reassert_win32_topmost()

    assert calls == [True] * (1 + ws.WIN32_TOPMOST_REASSERTS)   # bounded
    assert timer.isActive() is False           # and no loop is left running
    assert h.ctl._topmost_reasserts_left == 0
    assert not [t for t in h.ctl.findChildren(QTimer) if t.isActive()]


def test_engage_makes_no_topmost_call_when_keep_on_top_is_off(make_ctl,
                                                              monkeypatch):
    """With keep-on-top off the raise is just raise + activate."""
    monkeypatch.setattr(ws.sys, "platform", "win32")
    h = make_ctl(settings={"keep_on_top": False})
    calls = []
    monkeypatch.setattr(h.ctl, "_set_topmost_win32", calls.append)
    activated = []
    monkeypatch.setattr(h.window, "raise_", lambda: activated.append("raise"))
    monkeypatch.setattr(h.window, "activateWindow",
                        lambda: activated.append("activate"))

    h.ctl.engage()

    assert calls == []                    # no topmost call at all
    assert h.ctl._topmost_timer is None   # no burst was even created
    assert activated == ["raise", "activate"]


def test_turning_keep_on_top_off_stops_the_pending_burst(make_ctl,
                                                         monkeypatch):
    """The tray choice must not be overridden by a beat-still-pending assert."""
    monkeypatch.setattr(ws.sys, "platform", "win32")
    h = make_ctl(settings={"keep_on_top": True})
    calls = []
    monkeypatch.setattr(h.ctl, "_set_topmost_win32", calls.append)
    h.ctl.engage()
    assert h.ctl._topmost_timer.isActive()

    h.ctl.set_keep_on_top(False)

    assert h.ctl.keep_on_top is False
    assert h.ctl._topmost_timer.isActive() is False
    assert calls == [True, False]         # assert, then clear; nothing after


def test_the_reassert_burst_stops_when_the_window_is_hidden(make_ctl,
                                                            monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "win32")
    h = make_ctl(settings={"keep_on_top": True})
    calls = []
    monkeypatch.setattr(h.ctl, "_set_topmost_win32", calls.append)
    h.ctl.engage()
    h.window.hide()

    h.ctl._reassert_win32_topmost()

    assert calls == [True]                # no assert while hidden
    assert h.ctl._topmost_timer.isActive() is False


def test_stopping_a_burst_that_never_started_is_safe(make_ctl, monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "win32")
    h = make_ctl(settings={"keep_on_top": False})

    h.ctl._stop_topmost_timer()           # no timer yet; must not raise

    assert h.ctl._topmost_timer is None


def test_engage_on_linux_never_touches_the_win32_burst(make_ctl, monkeypatch):
    """Linux/Hyprland raise behaviour is unchanged."""
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    h = make_ctl(settings={"keep_on_top": True})
    monkeypatch.setattr(h.ctl, "_set_topmost_win32",
                        lambda on: pytest.fail("win32 topmost used on Linux"))

    h.ctl.engage()
    h.window.hide()

    assert h.ctl._topmost_timer is None


# ── 6. Qt event hooks ──────────────────────────────────────────────────────
@pytest.mark.parametrize("choice", [True, False])
def test_on_show_reapplies_the_stored_choice_natively_on_windows(
        make_ctl, monkeypatch, choice):
    monkeypatch.setattr(ws.sys, "platform", "win32")
    h = make_ctl(settings={"keep_on_top": choice})
    seen = []
    monkeypatch.setattr(h.ctl, "_set_topmost_win32",
                        lambda on: seen.append(on))

    h.ctl.on_show()

    assert seen == [choice]


def test_on_show_applies_the_qt_hint_off_windows(make_ctl, monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    h = make_ctl(settings={"keep_on_top": True})
    seen = []
    monkeypatch.setattr(h.ctl, "_set_topmost_win32", seen.append)

    h.ctl.on_show()

    assert seen == []
    assert h.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


def test_on_hide_saves_the_geometry(make_ctl):
    h = make_ctl()
    h.window.setGeometry(QRect(7, 8, 400, 300))

    h.ctl.on_hide()

    assert h.settings["window_rect"] == [7, 8, 400, 300]
    assert len(h.saves) == 1


def test_window_deactivate_engages_click_through(make_ctl, qapp):
    h = make_ctl()
    h.window.show()
    qapp.processEvents()
    assert h.ctl.click_through is False

    h.ctl.on_change(QEvent(QEvent.Type.WindowDeactivate))

    assert h.ctl.click_through is True
    assert h.chrome.click_through[-1] is True


def test_window_deactivate_is_ignored_while_hidden(make_ctl):
    h = make_ctl()
    assert h.window.isVisible() is False

    h.ctl.on_change(QEvent(QEvent.Type.WindowDeactivate))

    assert h.ctl.click_through is False
    assert h.chrome.click_through == []


def test_window_deactivate_is_skipped_while_a_dialog_is_open(make_ctl, qapp):
    h = make_ctl()
    h.window.show()
    qapp.processEvents()
    h.ctl.dialog_open = True

    h.ctl.on_change(QEvent(QEvent.Type.WindowDeactivate))

    assert h.ctl.click_through is False
    assert h.chrome.click_through == []


def test_window_deactivate_is_a_noop_when_already_click_through(make_ctl,
                                                               qapp):
    h = make_ctl()
    h.window.show()
    qapp.processEvents()
    h.ctl.set_click_through(True)
    h.chrome.click_through.clear()

    h.ctl.on_change(QEvent(QEvent.Type.WindowDeactivate))

    assert h.ctl.click_through is True
    assert h.chrome.click_through == []


def test_non_deactivate_events_are_ignored(make_ctl, qapp):
    h = make_ctl()
    h.window.show()
    qapp.processEvents()

    h.ctl.on_change(QEvent(QEvent.Type.WindowActivate))

    assert h.ctl.click_through is False
    assert h.chrome.click_through == []


def test_dialog_open_coerces_truthy_and_falsy_values(make_ctl):
    h = make_ctl()

    h.ctl.dialog_open = 1
    assert h.ctl.dialog_open is True
    h.ctl.dialog_open = 0
    assert h.ctl.dialog_open is False
    h.ctl.dialog_open = "yes"
    assert h.ctl.dialog_open is True


# ── 7. clean shutdown ──────────────────────────────────────────────────────
def test_teardown_after_engage_saves_nothing_extra_and_is_clean(make_ctl,
                                                                qapp):
    """There is no controller shutdown(); the host owns the lifecycle.

    Closing the window must therefore leave the controller usable and
    destructible without a Qt error, and must not spuriously re-persist the
    geometry (the palette's hideEvent is the only save trigger).
    """
    h = make_ctl()
    h.ctl.configure_frame()
    h.ctl.engage()
    qapp.processEvents()

    h.window.hide()
    h.window.close()
    qapp.processEvents()

    assert h.saves == []               # no hidden extra save
    h.ctl.on_hide()                    # still callable after close
    assert len(h.saves) == 1
